"""Support for DayBetter lights."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN

_LOGGER = logging.getLogger("custom_components.daybetter_services")
SCAN_INTERVAL = timedelta(seconds=300)


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


def _brightness_to_ha(raw: Any) -> int | None:
    """Convert DayBetter brightness formats to Home Assistant's 0-255 scale."""
    value = _safe_float(raw)
    if value is None:
        return None
    if value <= 0:
        return 0
    if value <= 1:
        return max(1, min(255, round(value * 255)))
    if value <= 100:
        return max(1, min(255, round(value * 255 / 100)))
    return max(0, min(255, round(value)))


def _kelvin_to_mired(kelvin: float) -> int:
    """Convert Kelvin to mired."""
    return round(1000000 / kelvin)


def _mired_to_kelvin(mired: float) -> int:
    """Convert mired to Kelvin."""
    return round(1000000 / mired)


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
    if value is None:
        return None
    if value == 0:
        return False
    if value == 1:
        return True
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up DayBetter lights from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    try:
        devices = await api.fetch_devices_with_statuses()
        data["devices"] = devices
    except Exception:  # pragma: no cover
        _LOGGER.exception("Failed to fetch DayBetter light devices during setup")
        devices = data["devices"]

    # Get light PIDs list
    pids_data = await api.fetch_pids()
    light_pids_str = pids_data.get("light", "")
    light_pids = set(light_pids_str.split(",")) if light_pids_str else set()

    # If a device is categorized as sensor, don't expose it as a light.
    # This avoids 温湿度计 误被创建成灯类实体的情况。
    sensor_pids_str = pids_data.get("sensor", "")
    sensor_pids = set(sensor_pids_str.split(",")) if sensor_pids_str else set()
    
    lights = [
        DayBetterLight(api, dev) 
        for dev in devices 
        if dev.get("deviceMoldPid") in light_pids and dev.get("deviceMoldPid") not in sensor_pids
    ]    
    async_add_entities(lights)
    data["light_entities"] = lights


class DayBetterLight(LightEntity):
    """Representation of a DayBetter light."""

    def __init__(self, api, device: dict[str, Any]) -> None:
        """Initialize the light."""
        self._api = api
        self._device = device
        self._attr_name = device.get("deviceGroupName", "DayBetter Light")
        self._attr_unique_id = str(device.get("deviceName", "unknown"))
        self._is_on = device.get("deviceState", 0) == 1
        self._brightness = 255  # Default maximum brightness
        self._hs_color = (0.0, 0.0)  # The default is white (hue, saturation)
        self._color_temp = 300  # Default color temperature (mireds unit)
        self._verify_state_unsub = None
        self._attr_should_poll = True
        
        device_features = device.get("deviceFeatures", [])
        
        # Home Assistant 2026.3.x 会对 supported_color_modes 做组合校验。
        # 为了确保实体能注册成功，这里采用“只声明一个最具体模式”的策略：
        # - 只要支持 HS，就只声明 HS（不再额外声明 BRIGHTNESS）
        # - 否则只声明 COLOR_TEMP
        # - 否则只声明 BRIGHTNESS
        # Color-capable modes imply brightness support in Home Assistant.
        supported_modes: set[ColorMode] = set()
        if 3 in device_features:
            supported_modes.add(ColorMode.HS)
        if 4 in device_features:
            supported_modes.add(ColorMode.COLOR_TEMP)
        if not supported_modes and 2 in device_features:
            supported_modes = {ColorMode.BRIGHTNESS}
        elif not supported_modes:
            supported_modes = {ColorMode.BRIGHTNESS}
            
        self._attr_supported_color_modes = supported_modes
        
        if ColorMode.HS in supported_modes:
            self._attr_color_mode = ColorMode.HS
        elif ColorMode.COLOR_TEMP in supported_modes:
            self._attr_color_mode = ColorMode.COLOR_TEMP
        elif ColorMode.BRIGHTNESS in supported_modes:
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_color_mode = ColorMode.UNKNOWN
            
        if ColorMode.COLOR_TEMP in supported_modes:
            self._min_mireds = 150
            self._max_mireds = 500
            self._attr_min_color_temp_kelvin = _mired_to_kelvin(self._max_mireds)
            self._attr_max_color_temp_kelvin = _mired_to_kelvin(self._min_mireds)
            
        self._device_features = device_features
        self._apply_device_update(device)

    async def async_update(self) -> None:
        """Fetch the latest light state from DayBetter."""
        try:
            devices = await self._api.fetch_devices_with_statuses()
        except Exception:  # pragma: no cover
            _LOGGER.exception("Failed to update DayBetter light %s", self._attr_unique_id)
            return

        if not self.update_from_devices(devices):
            _LOGGER.debug(
                "No refreshed DayBetter state matched light %s; device keys=%s",
                self._attr_unique_id,
                list(self._device.keys()),
            )

    def _schedule_verify_state(self) -> None:
        """Refresh from DayBetter shortly after sending a command."""
        if self._verify_state_unsub is not None:
            self._verify_state_unsub()
            self._verify_state_unsub = None

        @callback
        def _verify(now: Any) -> None:
            self._verify_state_unsub = None
            self.async_schedule_update_ha_state(True)

        self._verify_state_unsub = async_call_later(self.hass, 1, _verify)

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
                    "lightSwitch",
                    "state",
                    "status",
                    "deviceState",
                ),
            )
        )
        if is_on is not None and is_on != self._is_on:
            _LOGGER.debug(
                "DayBetter light %s power changed from %s to %s",
                self._attr_unique_id,
                self._is_on,
                is_on,
            )
            self._is_on = is_on
            changed = True

        brightness = _brightness_to_ha(
            _read_first_existing(
                device,
                (
                    "brightness",
                    "bright",
                    "brightnessPercent",
                    "brightness_percent",
                    "brightnessValue",
                    "brightValue",
                    "bright_value",
                    "bri",
                    "dimming",
                    "luminance",
                    "lightness",
                ),
            )
        )
        if brightness is not None and brightness != self._brightness:
            _LOGGER.debug(
                "DayBetter light %s brightness changed from %s to %s",
                self._attr_unique_id,
                self._brightness,
                brightness,
            )
            self._brightness = brightness
            changed = True

        hue = _safe_float(_read_first_existing(device, ("hue", "h")))
        saturation = _safe_float(_read_first_existing(device, ("saturation", "sat", "s")))
        if hue is not None and saturation is not None:
            if saturation <= 1:
                saturation *= 100
            hs_color = (hue % 360, max(0, min(100, saturation)))
            if hs_color != self._hs_color:
                self._hs_color = hs_color
                changed = True
            if ColorMode.HS in self._attr_supported_color_modes:
                self._attr_color_mode = ColorMode.HS

        kelvin = _safe_float(_read_first_existing(device, ("kelvin", "colorTempKelvin")))
        if kelvin:
            color_temp = _kelvin_to_mired(kelvin)
            if color_temp != self._color_temp:
                self._color_temp = color_temp
                changed = True
            if ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
                self._attr_color_mode = ColorMode.COLOR_TEMP
        else:
            color_temp = _safe_float(_read_first_existing(device, ("color_temp", "colorTemp", "mireds")))
            if color_temp is not None and color_temp > 1000:
                color_temp = _kelvin_to_mired(color_temp)
            if color_temp is not None and round(color_temp) != self._color_temp:
                self._color_temp = round(color_temp)
                changed = True
            if color_temp is not None and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
                self._attr_color_mode = ColorMode.COLOR_TEMP

        return changed

    def update_from_devices(self, devices: list[dict[str, Any]]) -> bool:
        """Update this light from a refreshed list of cloud devices."""
        for device in devices:
            if self._matches_device(device):
                return self._apply_device_update(device)
        return False

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self._is_on
    
    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light."""
        # 当 supported_color_modes 包含 HS/COLOR_TEMP 时，亮度仍然应该可用。
        if (
            self._attr_supported_color_modes
            and (
                ColorMode.BRIGHTNESS in self._attr_supported_color_modes
                or ColorMode.HS in self._attr_supported_color_modes
                or ColorMode.COLOR_TEMP in self._attr_supported_color_modes
            )
        ):
            return self._brightness
        return None

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return the hue and saturation color value."""
        if self._attr_supported_color_modes and ColorMode.HS in self._attr_supported_color_modes:
            return self._hs_color
        return None
    
    @property
    def color_temp(self) -> int | None:
        """Return the color temperature."""
        if self._attr_supported_color_modes and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            return self._color_temp
        return None

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature in Kelvin."""
        if self._attr_supported_color_modes and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            return _mired_to_kelvin(self._color_temp)
        return None
    
    @property
    def min_mireds(self) -> int:
        """Return the coldest color temp that this light supports."""
        if self._attr_supported_color_modes and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            return getattr(self, '_min_mireds', 153)
        return 153
    
    @property
    def max_mireds(self) -> int:
        """Return the warmest color temp that this light supports."""
        if self._attr_supported_color_modes and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            return getattr(self, '_max_mireds', 500)
        return 500

    @property
    def min_color_temp_kelvin(self) -> int:
        """Return the warmest supported color temperature in Kelvin."""
        if self._attr_supported_color_modes and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            return _mired_to_kelvin(getattr(self, '_max_mireds', 500))
        return _mired_to_kelvin(500)

    @property
    def max_color_temp_kelvin(self) -> int:
        """Return the coldest supported color temperature in Kelvin."""
        if self._attr_supported_color_modes and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            return _mired_to_kelvin(getattr(self, '_min_mireds', 153))
        return _mired_to_kelvin(153)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        # Get the brightness value set by the user
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        has_brightness = (
            self._attr_supported_color_modes
            and (
                ColorMode.BRIGHTNESS in self._attr_supported_color_modes
                or ColorMode.HS in self._attr_supported_color_modes
                or ColorMode.COLOR_TEMP in self._attr_supported_color_modes
            )
        )
        if brightness is not None and has_brightness:
            self._brightness = brightness

        # Processing color
        hs_color = kwargs.get(ATTR_HS_COLOR)
        if hs_color is not None and self._attr_supported_color_modes and ColorMode.HS in self._attr_supported_color_modes:
            self._hs_color = hs_color
            self._attr_color_mode = ColorMode.HS

        # Handle color temperature
        # Home Assistant 2026.3.x may not expose ATTR_COLOR_TEMP constant anymore,
        # but the service/kwargs key is still "color_temp".
        color_temp = kwargs.get("color_temp")
        color_temp_kelvin = kwargs.get("color_temp_kelvin")
        if color_temp is None and color_temp_kelvin is not None:
            color_temp = _kelvin_to_mired(color_temp_kelvin)
        if color_temp is not None and self._attr_supported_color_modes and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            self._color_temp = color_temp
            self._attr_color_mode = ColorMode.COLOR_TEMP

        supports_hs = self._attr_supported_color_modes and ColorMode.HS in self._attr_supported_color_modes
        supports_color_temp = (
            self._attr_supported_color_modes
            and ColorMode.COLOR_TEMP in self._attr_supported_color_modes
        )

        effective_brightness = self._brightness if has_brightness else None
        result = await self._api.control_device(
            self._device["deviceName"],
            True,
            effective_brightness if hs_color is not None and supports_hs else brightness,
            hs_color if supports_hs else None,
            color_temp if supports_color_temp else None,
        )

        # DayBetter exposes color temperature and brightness as separate command
        # types, so a HA call containing both needs a follow-up brightness command.
        if (
            result.get("code", 1)
            and color_temp is not None
            and brightness is not None
            and has_brightness
            and supports_color_temp
        ):
            result = await self._api.control_device(
                self._device["deviceName"],
                True,
                brightness,
                None,
                None,
            )
        
        # Update status based on control results
        if result.get("code", 1):
            self._is_on = True
            self.async_write_ha_state()
            self._schedule_verify_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        # Control equipment
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
            self._schedule_verify_state()
