"""Fan platform for Vornado Transom."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import (
    DIRECTION_FORWARD,
    DIRECTION_REVERSE,
    FanEntity,
    FanEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import VornadoTransomConfigEntry, VornadoTransomCoordinator
from .entity import VornadoTransomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VornadoTransomConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vornado Transom fans from a config entry."""
    coordinator: VornadoTransomCoordinator = entry.runtime_data
    async_add_entities(
        VornadoTransomFan(coordinator, key) for key in coordinator.data
    )


class VornadoTransomFan(VornadoTransomEntity, FanEntity):
    """Representation of a Vornado Transom fan."""

    _attr_name = None
    _attr_supported_features = (
        FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.DIRECTION
    )

    def __init__(
        self, coordinator: VornadoTransomCoordinator, appliance_key: str
    ) -> None:
        """Initialize the fan entity."""
        super().__init__(coordinator, appliance_key)
        self._attr_unique_id = f"{appliance_key}_fan"
        self._attr_preset_modes = [
            mode.label for mode in self.device.controls.speed_modes
        ]

    @property
    def is_on(self) -> bool | None:
        """Return true if the fan is on."""
        if self.device.power is None:
            return None
        return self.device.power == "ON"

    @property
    def preset_mode(self) -> str | None:
        """Return the current speed preset label."""
        if not self.device.speed_mode:
            return None
        for binding in self.device.controls.speed_modes:
            if binding.alexa_mode == self.device.speed_mode:
                return binding.label
        return None

    @property
    def current_direction(self) -> str | None:
        """Return the current airflow direction."""
        direction_mode = self.device.direction_mode
        if not direction_mode:
            return None
        if direction_mode == self.device.controls.direct_mode.alexa_mode:
            return DIRECTION_FORWARD
        if direction_mode == self.device.controls.exhaust_mode.alexa_mode:
            return DIRECTION_REVERSE
        return None

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on, optionally selecting a preset."""
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
            return
        await self.coordinator.async_set_power(self._appliance_key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        await self.coordinator.async_set_power(self._appliance_key, False)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the speed preset mode."""
        binding = next(
            (
                mode
                for mode in self.device.controls.speed_modes
                if mode.label == preset_mode
            ),
            None,
        )
        if binding is None:
            raise HomeAssistantError(f"Invalid preset mode: {preset_mode}")

        if self.device.power != "ON":
            await self.coordinator.async_set_power(self._appliance_key, True)
        await self.coordinator.async_set_speed_mode(
            self._appliance_key, binding.alexa_mode
        )

    async def async_set_direction(self, direction: str) -> None:
        """Set the airflow direction."""
        if direction == DIRECTION_FORWARD:
            alexa_mode = self.device.controls.direct_mode.alexa_mode
        elif direction == DIRECTION_REVERSE:
            alexa_mode = self.device.controls.exhaust_mode.alexa_mode
        else:
            raise HomeAssistantError(f"Invalid direction: {direction}")
        await self.coordinator.async_set_direction_mode(
            self._appliance_key, alexa_mode
        )
