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


def _safe_float(value: Any) -> float | None:
    """Cast a raw value to float if possible."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_first_existing(dev: dict[str, Any], keys: tuple[str, ...]) -> Any | None:
    """Read a value from top-level or common nested status containers."""
    for container_key in (
        "deviceData",
        "data",
        "status",
        "extend",
        "extra",
        "properties",
        "property",
        "dp",
        "dps",
    ):
        container = dev.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value is not None:
                return value

    for key in keys:
        value = dev.get(key)
        if value is not None:
            return value

    for value in dev.values():
        if not isinstance(value, dict):
            continue
        nested = _read_first_existing(value, keys)
        if nested is not None:
            return nested
    return None


def _status_to_bool(raw: Any) -> bool | None:
    """Convert common DayBetter status fields to a boolean."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().casefold()
        if normalized in ("1", "on", "true", "yes"):
            return True
        if normalized in ("0", "off", "false", "no"):
            return False
        return None
    value = _safe_float(raw)
    if value == 0:
        return False
    if value == 1:
        return True
    return None

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
    data["switch_entities"] = switches

class DayBetterSwitch(SwitchEntity):
    """Representation of a DayBetter switch."""

    def __init__(self, api, device: dict[str, Any]) -> None:
        """Initialize the switch."""
        self._api = api
        self._device = device
        self._attr_name = device.get("deviceGroupName", "DayBetter Switch")
        self._attr_unique_id = str(device.get("deviceName", "unknown"))
        self._is_on = device.get("deviceState", 0) == 1
        self._attr_should_poll = True
        self._apply_device_update(device)

    async def async_update(self) -> None:
        """Fetch the latest switch state from DayBetter."""
        try:
            devices = await self._api.fetch_devices_with_statuses()
        except Exception:  # pragma: no cover
            _LOGGER.exception("Failed to update DayBetter switch %s", self._attr_unique_id)
            return

        self.update_from_devices(devices)

    def _matches_device(self, device: dict[str, Any]) -> bool:
        """Return true if a cloud device row belongs to this entity."""
        for key in (
            "deviceName",
            "deviceId",
            "deviceGroupName",
            "id",
            "devId",
            "deviceNo",
            "deviceSn",
            "sn",
            "mac",
        ):
            current = self._device.get(key)
            incoming = device.get(key)
            if current is None or incoming is None:
                continue
            if str(current).strip() == str(incoming).strip():
                return True
        return False

    def _apply_device_update(self, device: dict[str, Any]) -> bool:
        """Apply state fields from a refreshed DayBetter device row."""
        changed = False
        self._device.update(device)
        is_on = _status_to_bool(
            _read_first_existing(
                device,
                (
                    "on",
                    "power",
                    "switch",
                    "switchStatus",
                    "powerState",
                    "state",
                    "status",
                    "deviceState",
                ),
            )
        )
        if is_on is not None and is_on != self._is_on:
            self._is_on = is_on
            changed = True
        return changed

    def update_from_devices(self, devices: list[dict[str, Any]]) -> bool:
        """Update this switch from a refreshed list of cloud devices."""
        for device in devices:
            if self._matches_device(device):
                return self._apply_device_update(device)
        return False

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
