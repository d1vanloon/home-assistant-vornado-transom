"""Auto climate platform for Vornado Transom."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alexa.commands import (
    CommandRejected,
    SetClimateFanMode,
    SetTargetTemperature,
    climate_intent,
    quantize_setpoint,
)
from .alexa.endpoints import (
    climate_hvac_action,
    climate_hvac_mode,
    climate_target_temperature,
    speed_label,
)
from .coordinator import VornadoTransomConfigEntry, VornadoTransomCoordinator
from .entity import VornadoTransomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VornadoTransomConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up auto climate for fans that expose both climate controllers."""
    coordinator: VornadoTransomCoordinator = entry.runtime_data
    async_add_entities(
        VornadoTransomClimate(coordinator, key)
        for key, device in coordinator.data.items()
        if device.controls.auto_climate is not None
    )


class VornadoTransomClimate(VornadoTransomEntity, ClimateEntity):
    """The fan's auto mode, shown as a thermostat."""

    _attr_name = "Auto climate"
    _attr_hvac_modes = (HVACMode.OFF, HVACMode.COOL, HVACMode.FAN_ONLY)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    # shortcut: Alexa reports Degrees; the recorded Transom reading 72 is Fahrenheit, upgrade if a fan reports Celsius
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT

    def __init__(
        self, coordinator: VornadoTransomCoordinator, appliance_key: str
    ) -> None:
        """Initialize the climate entity from the discovered range and speeds."""
        super().__init__(coordinator, appliance_key)
        climate = self.device.controls.auto_climate
        if climate is None:
            raise HomeAssistantError("This fan has no auto climate")
        span = climate.set_temperature_range
        self._attr_unique_id = f"{appliance_key}_climate"
        self._attr_min_temp = span.minimum
        self._attr_max_temp = span.maximum
        self._attr_precision = span.precision
        self._attr_target_temperature_step = span.precision
        self._attr_fan_modes = [mode.label for mode in self.device.controls.speed_modes]

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Cool when auto is on, fan only when the fan runs with auto off."""
        mode = climate_hvac_mode(self.device)
        if mode is None:
            return None
        return HVACMode(mode)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Fan while power is on. The device has no separate running flag."""
        action = climate_hvac_action(self.device)
        if action is None:
            return None
        return HVACAction(action)

    @property
    def current_temperature(self) -> float | None:
        """Return the measured air temperature."""
        return self.device.air_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return a numeric setpoint so the climate card can show the control."""
        return climate_target_temperature(self.device)

    @property
    def fan_mode(self) -> str | None:
        """Return the current speed in cool and in fan only."""
        return speed_label(self.device)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Cool enables auto. Fan only runs the fan with auto off. Off powers it off."""
        try:
            intent = climate_intent(str(hvac_mode))
        except CommandRejected as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_command(self._appliance_key, intent)

    async def async_turn_on(self) -> None:
        """Turn the thermostat on."""
        await self.async_set_hvac_mode(HVACMode.COOL)

    async def async_turn_off(self) -> None:
        """Turn the fan off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the auto climate target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            raise HomeAssistantError("No temperature provided")
        climate = self.device.controls.auto_climate
        if climate is None:
            raise HomeAssistantError("This fan has no auto climate")
        snapped = quantize_setpoint(temperature, climate.set_temperature_range)
        if snapped is None:
            raise HomeAssistantError(f"Invalid target temperature: {temperature}")
        mode = kwargs.get(ATTR_HVAC_MODE)
        await self.coordinator.async_command(
            self._appliance_key,
            SetTargetTemperature(snapped, None if mode is None else str(mode)),
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the speed while cool or fan only is active."""
        await self.coordinator.async_command(
            self._appliance_key, SetClimateFanMode(fan_mode)
        )
