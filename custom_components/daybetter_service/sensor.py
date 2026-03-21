"""Support for DayBetter temperature & humidity sensors."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Literal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN

_LOGGER = logging.getLogger("custom_components.daybetter_services")

SCAN_INTERVAL = timedelta(seconds=300)

SensorKind = Literal["temperature", "humidity", "battery"]


def _safe_div(value: Any, divisor: float) -> float | None:
    """Divide a raw value if possible."""
    if value is None:
        return None
    try:
        return int(value) / divisor
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    """Cast a raw value to int if possible."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _read_first_existing(dev: dict[str, Any], keys: list[str]) -> Any | None:
    """Try to read value from multiple possible keys (top-level or nested)."""
    for key in keys:
        value = dev.get(key)
        if value is not None:
            return value

    # 常见嵌套字段（不同接口/设备固件可能会不同）
    for container_key in ("deviceData", "data", "sensorData", "status", "extend", "extra"):
        container = dev.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value is not None:
                return value

    return None


def _convert_temp(raw: Any) -> float | None:
    """Convert raw temperature to Celsius.

    官方实现里 raw 通常是“十分之一摄氏度”，这里做个简单兼容：
    - 如果绝对值 > 100，按 /10
    - 否则按原值
    """
    raw_i = _safe_int(raw)
    if raw_i is None:
        return None
    if abs(raw_i) > 100:
        return raw_i / 10
    return float(raw_i)


def _convert_humidity(raw: Any) -> float | None:
    """Convert raw humidity to %RH (with simple compatibility)."""
    raw_i = _safe_int(raw)
    if raw_i is None:
        return None
    if abs(raw_i) > 100:
        return raw_i / 10
    return float(raw_i)


def _convert_battery(raw: Any) -> int | None:
    """Convert raw battery to % (with simple compatibility)."""
    raw_i = _safe_int(raw)
    if raw_i is None:
        return None
    # 有些返回会是十分之一电量
    if raw_i > 100 and raw_i <= 1000:
        return int(raw_i / 10)
    return raw_i


class DayBetterSensor(SensorEntity):
    """Representation of a DayBetter temperature/humidity/battery sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    # 每次轮询都写入状态，便于 Recorder 记录历史曲线（数值不变时默认可能不记新点）
    _attr_force_update = True

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        api: Any,
        entry_id: str,
        device: dict[str, Any],
        device_id: str,
        kind: SensorKind,
    ) -> None:
        self.hass = hass
        self._api = api
        self._entry_id = entry_id
        self._device_id = device_id
        self._kind = kind
        self._device_name = device.get("deviceGroupName") or device.get("deviceName") or "DayBetter"
        self._device_info_model = device.get("deviceClass") or "Sensor"

        if kind == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_name = f"{self._device_name} 温度"
            self._attr_suggested_display_precision = 1
        elif kind == "humidity":
            self._attr_device_class = SensorDeviceClass.HUMIDITY
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_name = f"{self._device_name} 湿度"
            self._attr_suggested_display_precision = 0
        else:
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_name = f"{self._device_name} 电量"
            self._attr_suggested_display_precision = 0

        self._attr_unique_id = f"{self._device_id}_{self._kind}"
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._device_name,
            manufacturer="DayBetter",
            model=self._device_info_model,
        )

    def _get_device(self) -> dict[str, Any] | None:
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        devices: list[dict[str, Any]] = data.get("sensor_devices") or []

        for dev in devices:
            if str(dev.get("deviceId")) == self._device_id:
                return dev
            # 兼容：如果 API 返回字段名变化/缺失 deviceId
            if dev.get("deviceName") and str(dev.get("deviceName")) == self._device_id:
                return dev
            if dev.get("deviceGroupName") and str(dev.get("deviceGroupName")) == self._device_id:
                return dev
        return None

    @property
    def native_value(self) -> float | int | None:
        dev = self._get_device()
        if not dev:
            return None

        if self._kind == "temperature":
            raw = _read_first_existing(dev, ["temp", "temperature", "tempC", "temp_c", "t"])
            return _convert_temp(raw)
        if self._kind == "humidity":
            raw = _read_first_existing(dev, ["humi", "humidity", "hum", "rh", "h"])
            return _convert_humidity(raw)
        raw = _read_first_existing(dev, ["battery", "bat", "batteryLevel", "bettery"])
        return _convert_battery(raw)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Any,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DayBetter sensors from a config entry."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    api = runtime["api"]

    # 温湿度需要实时状态（官方使用 fetch_sensor_data()，其中会合并 hass/status）
    devices: list[dict[str, Any]] = await api.fetch_sensor_data()
    runtime["sensor_devices"] = devices
    _LOGGER.debug("DayBetter sensor devices: %d", len(devices))
    sensors: list[DayBetterSensor] = []

    if _LOGGER.isEnabledFor(logging.DEBUG):
        for dev in devices:
            name = dev.get("deviceGroupName") or dev.get("deviceName") or "unknown"
            keys = sorted(dev.keys())
            _LOGGER.debug("DayBetter sensor device keys: %s keys=%s", name, keys)

    for dev in devices:
        device_id_raw = dev.get("deviceId") or dev.get("deviceName") or dev.get("deviceGroupName")
        if not device_id_raw:
            continue
        device_id = str(device_id_raw)

        temp_raw = _read_first_existing(dev, ["temp", "temperature", "tempC", "temp_c", "t"])
        hum_raw = _read_first_existing(dev, ["humi", "humidity", "hum", "rh", "h"])
        bat_raw = _read_first_existing(dev, ["battery", "bat", "batteryLevel", "bettery"])

        if temp_raw is not None:
            sensors.append(
                DayBetterSensor(
                    hass=hass,
                    api=api,
                    entry_id=entry.entry_id,
                    device=dev,
                    device_id=device_id,
                    kind="temperature",
                )
            )
        if hum_raw is not None:
            sensors.append(
                DayBetterSensor(
                    hass=hass,
                    api=api,
                    entry_id=entry.entry_id,
                    device=dev,
                    device_id=device_id,
                    kind="humidity",
                )
            )
        if bat_raw is not None:
            sensors.append(
                DayBetterSensor(
                    hass=hass,
                    api=api,
                    entry_id=entry.entry_id,
                    device=dev,
                    device_id=device_id,
                    kind="battery",
                )
            )

        # 已通过 PID 识别为传感器，但合并状态后仍无温湿度字段时仍创建实体（状态为 unknown，轮询后可能更新）
        if temp_raw is None and hum_raw is None and bat_raw is None:
            _LOGGER.debug(
                "Sensor device has no temp/humi/battery after merge; "
                "creating placeholder temperature & humidity entities: %s",
                dev.get("deviceGroupName") or dev.get("deviceName"),
            )
            sensors.extend(
                [
                    DayBetterSensor(
                        hass=hass,
                        api=api,
                        entry_id=entry.entry_id,
                        device=dev,
                        device_id=device_id,
                        kind="temperature",
                    ),
                    DayBetterSensor(
                        hass=hass,
                        api=api,
                        entry_id=entry.entry_id,
                        device=dev,
                        device_id=device_id,
                        kind="humidity",
                    ),
                ]
            )

    async_add_entities(sensors)

    # 统一轮询一次即可，传感器读取的是 hass.data 中最新 devices
    if runtime.get("sensor_poll_unsub") is None:

        async def _poll(now: Any) -> None:
            try:
                new_devices = await api.fetch_sensor_data()
            except Exception:  # pragma: no cover
                _LOGGER.exception("Failed to fetch DayBetter devices")
                return

            runtime["sensor_devices"] = new_devices
            # 通知同一入口下已创建的传感器刷新状态
            for sensor in sensors:
                sensor.async_write_ha_state()

        runtime["sensor_poll_unsub"] = async_track_time_interval(hass, _poll, SCAN_INTERVAL)


async def async_unload_entry(hass: HomeAssistant, entry: Any) -> bool:
    """Unload a config entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}
    unsub = runtime.get("sensor_poll_unsub")
    if unsub is not None:
        unsub()
        runtime["sensor_poll_unsub"] = None
    return True

