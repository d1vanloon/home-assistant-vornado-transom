"""Auto climate platform for Vornado Transom."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alexa.commands import SetClimateFanMode, SetHvacAuto, SetTargetTemperature
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
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.AUTO]
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
        """Return auto or off from the auto toggle alone."""
        if self.device.auto_on is True:
            return HVACMode.AUTO
        if self.device.auto_on is False:
            return HVACMode.OFF
        return None

    @property
    def current_temperature(self) -> float | None:
        """Return the measured air temperature."""
        return self.device.air_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the set temperature."""
        return self.device.target_temperature

    @property
    def fan_mode(self) -> str | None:
        """Return the label for the current speed."""
        if not self.device.speed_mode:
            return None
        for binding in self.device.controls.speed_modes:
            if binding.alexa_mode == self.device.speed_mode:
                return binding.label
        return None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set auto climate on or off."""
        if hvac_mode == HVACMode.AUTO:
            on = True
        elif hvac_mode == HVACMode.OFF:
            on = False
        else:
            raise HomeAssistantError(f"Unsupported HVAC mode: {hvac_mode}")
        await self.coordinator.async_command(self._appliance_key, SetHvacAuto(on))

    async def async_turn_on(self) -> None:
        """Turn auto climate on."""
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        """Turn auto climate off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the auto climate target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            raise HomeAssistantError("No temperature provided")
        await self.coordinator.async_command(
            self._appliance_key, SetTargetTemperature(temperature)
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the speed used while auto climate is on."""
        await self.coordinator.async_command(
            self._appliance_key, SetClimateFanMode(fan_mode)
        )
