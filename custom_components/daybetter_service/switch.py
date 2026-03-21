"""Support for DayBetter switches."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger("custom_components.daybetter_services")

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up DayBetter switches from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    devices = data["devices"]

    # Get switch PIDs list
    pids_data = await api.fetch_pids()
    switch_pids_str = pids_data.get("switch", "")
    switch_pids = set(switch_pids_str.split(",")) if switch_pids_str else set()

    # If a device is categorized as sensor, don't expose it as a switch.
    sensor_pids_str = pids_data.get("sensor", "")
    sensor_pids = set(sensor_pids_str.split(",")) if sensor_pids_str else set()

    switches = [
        DayBetterSwitch(api, dev) 
        for dev in devices 
        if dev.get("deviceMoldPid") in switch_pids and dev.get("deviceMoldPid") not in sensor_pids
    ]
    async_add_entities(switches)

class DayBetterSwitch(SwitchEntity):
    """Representation of a DayBetter switch."""

    def __init__(self, api, device: dict[str, Any]) -> None:
        """Initialize the switch."""
        self._api = api
        self._device = device
        self._attr_name = device.get("deviceGroupName", "DayBetter Switch")
        self._attr_unique_id = str(device.get("deviceName", "unknown"))
        self._is_on = device.get("deviceState", 0) == 1

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        result = await self._api.control_device(
            self._device["deviceName"], 
            True, 
            None,
            None,
            None
        )
        
        # Update status based on control results
        if result.get("code", 1):
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        result = await self._api.control_device(
            self._device["deviceName"], 
            False, 
            None,
            None,
            None
        )
        
        # Update status based on control results
        if result.get("code", 1):
            self._is_on = False
            self.async_write_ha_state()