"""DataUpdateCoordinator for Vornado Transom."""

from __future__ import annotations

from pathlib import Path

from aioamazondevices.exceptions import (
    CannotAuthenticate,
    CannotConnect,
    CannotRetrieveData,
)
from aioamazondevices.structures import AmazonSaveDataConfig
from aiohttp import ClientSession

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .alexa.client import AlexaSmartHomeClient
from .alexa.commands import CommandRejected, FeatureWrite, Intent, feature_request
from .alexa.endpoints import (
    Gate,
    Rejected,
    classify_endpoint,
    fold_poll,
    kept_devices,
    mark_unavailable,
)
from .alexa.execution import CommandGate
from .alexa.models import AlexaApiError, FanDevice
from .const import CONF_LOGIN_DATA, DOMAIN, LOGGER, SCAN_INTERVAL

type VornadoTransomConfigEntry = ConfigEntry[VornadoTransomCoordinator]


class VornadoTransomCoordinator(DataUpdateCoordinator[dict[str, FanDevice]]):
    """Coordinator that discovers and polls Vornado Transom fans."""

    config_entry: VornadoTransomConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: VornadoTransomConfigEntry,
        session: ClientSession,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            name=entry.title,
            config_entry=entry,
            update_interval=SCAN_INTERVAL,
            request_refresh_debouncer=Debouncer(
                hass, LOGGER, cooldown=SCAN_INTERVAL.total_seconds(), immediate=False
            ),
        )
        self.client = AlexaSmartHomeClient(
            session,
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            entry.data[CONF_LOGIN_DATA],
            save_data=AmazonSaveDataConfig(
                path=Path(hass.config.path(DOMAIN)),
            ),
        )
        self._controls_by_key: dict[str, FanDevice] = {}
        self._gate = CommandGate()
        self._write_failures = (
            AlexaApiError,
            CannotAuthenticate,
            CannotConnect,
            CannotRetrieveData,
        )

    async def _async_update_data(self) -> dict[str, FanDevice]:
        """Discover (once) and poll fan state."""
        try:
            await self.client.async_login_stored()
            if not self._controls_by_key:
                endpoints = await self.client.async_get_endpoints()
                verdicts = []
                for endpoint in endpoints:
                    verdict = classify_endpoint(endpoint)
                    if isinstance(verdict, Rejected) and verdict.gate is Gate.CAPABILITIES:
                        LOGGER.debug(
                            "Skipping endpoint %s: capability parse failure",
                            endpoint.get("friendlyName") or endpoint.get("id"),
                        )
                    verdicts.append(verdict)
                self._controls_by_key = kept_devices(verdicts)
                if not self._controls_by_key:
                    LOGGER.warning(
                        "No Vornado Transom-compatible fans found"
                    )

            started: dict[str, int] = {}
            proposed: dict[str, FanDevice] = {}
            for key, template in self._controls_by_key.items():
                started[key] = self._gate.epoch(key)
                try:
                    states = await self.client.async_get_fan_state(template.endpoint_id)
                    proposed[key] = fold_poll(self._cached_device(key), template, states)
                except AlexaApiError as err:
                    LOGGER.warning(
                        "Failed to refresh fan %s: %s", template.name, err
                    )
                    proposed[key] = mark_unavailable(
                        self._cached_device(key) or template
                    )
            return await self._gate.merge_poll(
                started, proposed, self._cached_device
            )
        except CannotAuthenticate as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
                translation_placeholders={"error": repr(err)},
            ) from err
        except CannotConnect as err:
            raise UpdateFailed(f"Error connecting to Amazon: {err}") from err
        except (CannotRetrieveData, AlexaApiError, ValueError) as err:
            raise UpdateFailed(f"Error retrieving data from Amazon: {err}") from err

    def _require_device(self, appliance_key: str) -> FanDevice:
        """Return a device from coordinator data or raise."""
        if not self.data or appliance_key not in self.data:
            raise HomeAssistantError(f"Unknown fan device: {appliance_key}")
        return self.data[appliance_key]

    def _cached_device(self, appliance_key: str) -> FanDevice | None:
        """Return the last published device, if this coordinator has one."""
        try:
            data = self.data
        except RuntimeError:
            return None
        if not isinstance(data, dict):
            return None
        device = data.get(appliance_key)
        if not isinstance(device, FanDevice):
            return None
        return device

    def _publish(self, appliance_key: str, device: FanDevice) -> None:
        """Replace one fan in coordinator data."""
        data = dict(self.data)
        data[appliance_key] = device
        self.async_set_updated_data(data)

    async def async_command(self, appliance_key: str, intent: Intent) -> None:
        """Plan the intent, send every write, then publish the folded device."""

        async def send(write: FeatureWrite) -> None:
            device = self._require_device(appliance_key)
            await self.client.async_set_endpoint_feature(
                feature_request(device.endpoint_id, write)
            )

        try:
            await self._gate.run(
                appliance_key,
                lambda: self._require_device(appliance_key),
                intent,
                send,
                lambda device: self._publish(appliance_key, device),
                self._write_failures,
            )
        except CommandRejected as err:
            raise HomeAssistantError(str(err)) from err
        except self._write_failures as err:
            # The failed write may have reached the fan. Poll now. The debounced
            # refresh waits a full scan interval, and a failed login starts reauth.
            self.hass.async_create_task(self.async_refresh())
            raise HomeAssistantError(str(err)) from err
