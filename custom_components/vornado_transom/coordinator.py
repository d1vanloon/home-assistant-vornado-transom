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
from .alexa.endpoints import (
    Gate,
    Rejected,
    apply_fan_state,
    classify_endpoint,
    kept_devices,
)
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

            devices: dict[str, FanDevice] = {}
            for key, template in self._controls_by_key.items():
                try:
                    states = await self.client.async_get_fan_state(template.endpoint_id)
                    devices[key] = apply_fan_state(template, states)
                except AlexaApiError as err:
                    LOGGER.warning(
                        "Failed to refresh fan %s: %s", template.name, err
                    )
                    devices[key] = FanDevice(
                        endpoint_id=template.endpoint_id,
                        appliance_key=template.appliance_key,
                        name=template.name,
                        serial_number=template.serial_number,
                        model=template.model,
                        manufacturer=template.manufacturer,
                        controls=template.controls,
                        power=template.power,
                        speed_mode=template.speed_mode,
                        direction_mode=template.direction_mode,
                        available=False,
                    )
            return devices
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

    async def async_set_power(self, appliance_key: str, turn_on: bool) -> None:
        """Set power and optimistically update coordinator data."""
        device = self._require_device(appliance_key)
        try:
            await self.client.async_set_power(device.endpoint_id, turn_on)
        except AlexaApiError as err:
            raise HomeAssistantError(str(err)) from err
        device.power = "ON" if turn_on else "OFF"
        self.async_set_updated_data(dict(self.data))

    async def async_set_speed_mode(self, appliance_key: str, alexa_mode: str) -> None:
        """Set speed mode and optimistically update coordinator data."""
        device = self._require_device(appliance_key)
        try:
            await self.client.async_set_mode(
                device.endpoint_id, device.controls.speed_instance, alexa_mode
            )
        except AlexaApiError as err:
            raise HomeAssistantError(str(err)) from err
        device.speed_mode = alexa_mode
        device.power = "ON"
        self.async_set_updated_data(dict(self.data))

    async def async_set_direction_mode(
        self, appliance_key: str, alexa_mode: str
    ) -> None:
        """Set direction mode and optimistically update coordinator data."""
        device = self._require_device(appliance_key)
        direction = device.controls.direction
        if direction is None:
            raise HomeAssistantError("This fan does not support airflow direction")
        try:
            await self.client.async_set_mode(
                device.endpoint_id, direction.instance, alexa_mode
            )
        except AlexaApiError as err:
            raise HomeAssistantError(str(err)) from err
        device.direction_mode = alexa_mode
        self.async_set_updated_data(dict(self.data))
