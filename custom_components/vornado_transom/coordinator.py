"""DataUpdateCoordinator for Vornado Transom."""

from __future__ import annotations

from typing import Any

from aioamazondevices.exceptions import (
    CannotAuthenticate,
    CannotConnect,
    CannotRetrieveData,
)
from aiohttp import ClientSession

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .alexa.client import AlexaSmartHomeClient
from .alexa.fan_controls import discover_vornado_controls, parse_capabilities
from .alexa.models import AlexaApiError, FanDevice
from .const import CONF_LOGIN_DATA, DOMAIN, LOGGER, SCAN_INTERVAL

ENDPOINT_PREFIX = "amzn1.alexa.endpoint."

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
        )
        self._controls_by_key: dict[str, FanDevice] = {}

    @staticmethod
    def _appliance_key(endpoint: dict[str, Any]) -> str:
        """Derive a stable appliance key for an endpoint."""
        legacy = endpoint.get("legacyAppliance") or {}
        if key := legacy.get("applianceKey"):
            return str(key)
        endpoint_id = str(endpoint.get("id") or endpoint.get("endpointId") or "")
        if endpoint_id.startswith(ENDPOINT_PREFIX):
            return endpoint_id.removeprefix(ENDPOINT_PREFIX)
        return endpoint_id

    @staticmethod
    def _text_value(field: Any) -> str | None:
        """Extract nested Alexa text value fields."""
        if not isinstance(field, dict):
            return None
        value = field.get("value")
        if isinstance(value, dict):
            text = value.get("text")
            return str(text) if text is not None else None
        return None

    def _discover_devices(self, endpoints: list[dict[str, Any]]) -> dict[str, FanDevice]:
        """Filter FAN endpoints that match the Transom capability pattern."""
        discovered: dict[str, FanDevice] = {}
        for endpoint in endpoints:
            enablement = endpoint.get("enablement")
            if enablement is not None and enablement != "ENABLED":
                continue

            display = endpoint.get("displayCategories") or {}
            primary = (display.get("primary") or {}).get("value")
            if primary != "FAN":
                continue

            legacy = endpoint.get("legacyAppliance") or {}
            capabilities = parse_capabilities(legacy.get("capabilities"))
            if capabilities is None:
                LOGGER.debug(
                    "Skipping endpoint %s: capability parse failure",
                    endpoint.get("friendlyName") or endpoint.get("id"),
                )
                continue

            controls = discover_vornado_controls(capabilities)
            if controls is None:
                continue

            appliance_key = self._appliance_key(endpoint)
            endpoint_id = str(endpoint.get("id") or endpoint.get("endpointId") or "")
            if not appliance_key or not endpoint_id:
                continue

            discovered[appliance_key] = FanDevice(
                endpoint_id=endpoint_id,
                appliance_key=appliance_key,
                name=str(endpoint.get("friendlyName") or appliance_key),
                serial_number=self._text_value(endpoint.get("serialNumber")),
                model=self._text_value(endpoint.get("model")),
                manufacturer=self._text_value(endpoint.get("manufacturer")),
                controls=controls,
            )
        return discovered

    async def _async_refresh_device_state(self, device: FanDevice) -> FanDevice:
        """Poll power/speed/direction for one device."""
        states = await self.client.async_get_fan_state(device.endpoint_id)
        power = device.power
        speed_mode = device.speed_mode
        direction_mode = device.direction_mode

        for state in states:
            if state.name == "power" and state.power_state is not None:
                power = state.power_state
            elif (
                state.name == "mode"
                and state.instance == device.controls.speed_instance
                and state.mode_value is not None
            ):
                speed_mode = state.mode_value
            elif (
                state.name == "mode"
                and state.instance == device.controls.direction_instance
                and state.mode_value is not None
            ):
                direction_mode = state.mode_value

        return FanDevice(
            endpoint_id=device.endpoint_id,
            appliance_key=device.appliance_key,
            name=device.name,
            serial_number=device.serial_number,
            model=device.model,
            manufacturer=device.manufacturer,
            controls=device.controls,
            power=power,
            speed_mode=speed_mode,
            direction_mode=direction_mode,
            available=True,
        )

    async def _async_update_data(self) -> dict[str, FanDevice]:
        """Discover (once) and poll fan state."""
        try:
            await self.client.async_login_stored()
            if not self._controls_by_key:
                endpoints = await self.client.async_get_endpoints()
                self._controls_by_key = self._discover_devices(endpoints)
                if not self._controls_by_key:
                    LOGGER.warning(
                        "No Vornado Transom-compatible fans found"
                    )

            devices: dict[str, FanDevice] = {}
            for key, template in self._controls_by_key.items():
                try:
                    devices[key] = await self._async_refresh_device_state(template)
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
        try:
            await self.client.async_set_mode(
                device.endpoint_id, device.controls.direction_instance, alexa_mode
            )
        except AlexaApiError as err:
            raise HomeAssistantError(str(err)) from err
        device.direction_mode = alexa_mode
        self.async_set_updated_data(dict(self.data))
