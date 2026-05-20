"""Support for DayBetter refresh buttons."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger("custom_components.daybetter_services")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up DayBetter buttons from a config entry."""
    async_add_entities([DayBetterRefreshButton(hass, entry.entry_id)])


class DayBetterRefreshButton(ButtonEntity):
    """Button that refreshes DayBetter cloud state for all entities."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the refresh button."""
        self.hass = hass
        self._entry_id = entry_id
        self._attr_name = "DayBetter Refresh Devices"
        self._attr_unique_id = f"{entry_id}_refresh_devices"

    async def async_press(self) -> None:
        """Refresh all DayBetter entities from the cloud."""
        runtime: dict[str, Any] = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        api = runtime.get("api")
        if api is None:
            _LOGGER.warning("Cannot refresh DayBetter entities; API client is missing")
            return

        try:
            devices = await api.fetch_devices_with_statuses()
        except Exception:  # pragma: no cover
            _LOGGER.exception("Failed to refresh DayBetter devices from cloud")
            devices = None

        if devices is not None:
            runtime["devices"] = devices
            for light in runtime.get("light_entities", []):
                light.update_from_devices(devices)
                light.async_write_ha_state()
            for switch in runtime.get("switch_entities", []):
                switch.update_from_devices(devices)
                switch.async_write_ha_state()

        try:
            sensor_devices = await api.fetch_sensor_data()
        except Exception:  # pragma: no cover
            _LOGGER.exception("Failed to refresh DayBetter sensor data from cloud")
            sensor_devices = None

        if sensor_devices is not None:
            runtime["sensor_devices"] = sensor_devices
            for sensor in runtime.get("sensor_entities", []):
                sensor.async_write_ha_state()

