"""Air temperature sensor for Vornado Transom."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import VornadoTransomConfigEntry, VornadoTransomCoordinator
from .entity import VornadoTransomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VornadoTransomConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up air temperature sensors for fans that expose one."""
    coordinator: VornadoTransomCoordinator = entry.runtime_data
    async_add_entities(
        VornadoTransomAirTemperature(coordinator, key)
        for key, device in coordinator.data.items()
        if device.controls.air_temperature_instance is not None
    )


class VornadoTransomAirTemperature(VornadoTransomEntity, SensorEntity):
    """Current air temperature reported by the fan."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    # shortcut: Alexa reports Degrees; the recorded Transom reading 72 is Fahrenheit, upgrade if a fan reports Celsius
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_name = "Air temperature"

    def __init__(
        self, coordinator: VornadoTransomCoordinator, appliance_key: str
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, appliance_key)
        self._attr_unique_id = f"{appliance_key}_air_temperature"

    @property
    def native_value(self) -> float | None:
        """Return the measured air temperature."""
        return self.device.air_temperature
