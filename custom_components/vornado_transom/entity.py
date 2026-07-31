"""Base entity for Vornado Transom."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .alexa.models import FanDevice
from .const import DOMAIN, MANUFACTURER
from .coordinator import VornadoTransomCoordinator


class VornadoTransomEntity(CoordinatorEntity[VornadoTransomCoordinator]):
    """Base entity backed by the Vornado Transom coordinator."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: VornadoTransomCoordinator, appliance_key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._appliance_key = appliance_key
        device = coordinator.data[appliance_key]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.appliance_key)},
            name=device.name,
            manufacturer=device.manufacturer or MANUFACTURER,
            model=device.model or "Transom",
            serial_number=device.serial_number,
        )

    @property
    def device(self) -> FanDevice:
        """Return the current fan device data."""
        return self.coordinator.data[self._appliance_key]

    @property
    def available(self) -> bool:
        """Return True if the device is present and available."""
        return (
            super().available
            and self._appliance_key in self.coordinator.data
            and self.device.available
        )
