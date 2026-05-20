"""The DayBetter Services integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .daybetter_api import DayBetterApi

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BUTTON,
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DayBetter Services from a config entry."""
    api = DayBetterApi(hass, entry.data["token"])
    devices = await api.fetch_devices()
    
    hass.data.setdefault("daybetter_services", {})
    hass.data["daybetter_services"][entry.entry_id] = {
        "api": api,
        "devices": devices,
    }
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data["daybetter_services"].pop(entry.entry_id)
    
    return unload_ok
