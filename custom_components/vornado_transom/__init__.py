"""The Vornado Transom integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .coordinator import VornadoTransomConfigEntry, VornadoTransomCoordinator

PLATFORMS = [Platform.FAN]


async def async_setup_entry(
    hass: HomeAssistant, entry: VornadoTransomConfigEntry
) -> bool:
    """Set up Vornado Transom from a config entry."""
    session = aiohttp_client.async_create_clientsession(hass)
    coordinator = VornadoTransomCoordinator(hass, entry, session)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: VornadoTransomConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
