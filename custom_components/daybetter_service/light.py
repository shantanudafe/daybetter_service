"""Support for DayBetter lights."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up DayBetter lights from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    devices = data["devices"]

    # Get light PIDs list
    pids_data = await api.fetch_pids()
    light_pids_str = pids_data.get("light", "")
    light_pids = set(light_pids_str.split(",")) if light_pids_str else set()
    
    lights = [
        DayBetterLight(api, dev) 
        for dev in devices 
        if dev.get("deviceMoldPid") in light_pids
    ]    
    async_add_entities(lights)

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
        
        device_features = device.get("deviceFeatures", [])
        
        # Home Assistant 2026.3.x 会对 supported_color_modes 做组合校验。
        # 为了确保实体能注册成功，这里采用“只声明一个最具体模式”的策略：
        # - 只要支持 HS，就只声明 HS（不再额外声明 BRIGHTNESS）
        # - 否则只声明 COLOR_TEMP
        # - 否则只声明 BRIGHTNESS
        supported_modes: set[ColorMode] = set()
        if 3 in device_features:
            supported_modes = {ColorMode.HS}
        elif 4 in device_features:
            supported_modes = {ColorMode.COLOR_TEMP}
        elif 2 in device_features:
            supported_modes = {ColorMode.BRIGHTNESS}
        else:
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
            
        self._device_features = device_features

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

        # Handle color temperature
        # Home Assistant 2026.3.x may not expose ATTR_COLOR_TEMP constant anymore,
        # but the service/kwargs key is still "color_temp".
        color_temp = kwargs.get("color_temp")
        if color_temp is not None and self._attr_supported_color_modes and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            self._color_temp = color_temp

        # Control equipment
        result = await self._api.control_device(
            self._device["deviceName"], 
            True, 
            brightness if has_brightness else None,
            hs_color if self._attr_supported_color_modes and ColorMode.HS in self._attr_supported_color_modes else None,
            color_temp if self._attr_supported_color_modes and ColorMode.COLOR_TEMP in self._attr_supported_color_modes else None
        )
        
        # Update status based on control results
        if result.get("code", 1):
            self._is_on = True
            self.async_write_ha_state()

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