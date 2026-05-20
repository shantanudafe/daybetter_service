"""DayBetter API client."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.color import color_hs_to_RGB

_LOGGER = logging.getLogger("custom_components.daybetter_services")

class DayBetterApi:
    """DayBetter API client."""

    def __init__(self, hass, token: str) -> None:
        """Initialize the API client."""
        self.hass = hass
        self.token = token

    async def fetch_devices(self) -> list[dict[str, Any]]:
        """Get list of devices."""
        session = async_get_clientsession(self.hass)
        url = "https://a.dbiot.org/daybetter/hass/api/v1.0/hass/devices"
        headers = {"Authorization": f"Bearer {self.token}"}
        async with session.post(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("data", [])
            else:
                _LOGGER.error("Failed to fetch devices: %s", await resp.text())
                return []

    async def fetch_pids(self) -> dict[str, Any]:
        """Get list of PIDs for different device types."""
        session = async_get_clientsession(self.hass)
        url = "https://a.dbiot.org/daybetter/hass/api/v1.0/hass/pids"
        headers = {"Authorization": f"Bearer {self.token}"}
        async with session.post(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("data", {})
            else:
                _LOGGER.error("Failed to fetch PIDs: %s", await resp.text())
                return {}

    async def fetch_device_statuses(self) -> list[dict[str, Any]]:
        """Fetch device statuses (includes temp/humi for sensors)."""
        session = async_get_clientsession(self.hass)
        url = "https://a.dbiot.org/daybetter/hass/api/v1.0/hass/status"
        headers = {"Authorization": f"Bearer {self.token}"}
        async with session.post(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                statuses = self._normalize_status_payload(data.get("data"))
                # 若解析后无 temp/humi，尝试其他顶层 key（如 sensorData、sensors）
                if statuses and not any(
                    s.get("temp") is not None or s.get("humi") is not None
                    for s in statuses
                ):
                    _LOGGER.debug("fetch_device_statuses: raw response top keys=%s", list(data.keys()))
                    for key in ("sensorData", "sensors", "sensor", "thData", "extData"):
                        extra = data.get(key)
                        if isinstance(extra, list) and extra:
                            statuses = self._normalize_status_payload(extra)
                            if statuses:
                                _LOGGER.debug("fetch_device_statuses: using data from key %s", key)
                                break
                        elif isinstance(extra, dict):
                            vals = [v for v in extra.values() if isinstance(v, dict)]
                            if vals and any(v.get("temp") is not None or v.get("humi") is not None for v in vals):
                                statuses = vals
                                _LOGGER.debug("fetch_device_statuses: using data from key %s", key)
                                break
                return statuses
            else:
                _LOGGER.error("Failed to fetch device statuses: %s", await resp.text())
                return []

    async def fetch_devices_with_statuses(self) -> list[dict[str, Any]]:
        """Fetch devices and merge matching live status rows when available."""
        devices = await self.fetch_devices()
        statuses = await self.fetch_device_statuses()
        if not devices or not statuses:
            return devices

        status_by_name: dict[str, dict[str, Any]] = {}
        for status in statuses:
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
                value = status.get(key)
                if value is None:
                    continue
                normalized = str(value).strip()
                status_by_name[normalized] = status
                status_by_name[normalized.casefold()] = status

        merged: list[dict[str, Any]] = []
        for device in devices:
            merged_device = device.copy()
            status = self._find_status_for_device(device, status_by_name)
            if status:
                merged_device.update(status)
            merged.append(merged_device)
        return merged

    @staticmethod
    def _normalize_status_payload(raw: Any) -> list[dict[str, Any]]:
        """兼容 hass/status 返回 list 或 单 dict 或 {id: {...}} 映射."""
        if raw is None:
            return []
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            vals = list(raw.values())
            if vals and all(isinstance(v, dict) for v in vals):
                normalized = []
                for key, value in raw.items():
                    status = value.copy()
                    status.setdefault("deviceName", key)
                    status.setdefault("deviceId", key)
                    normalized.append(status)
                return normalized
            return [raw]
        return []

    @staticmethod
    def _find_status_for_device(
        device: dict[str, Any],
        status_by_name: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Find a status row matching a device by the common identifiers."""
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
            value = device.get(key)
            if value is None:
                continue
            normalized = str(value).strip()
            if normalized in status_by_name:
                return status_by_name[normalized]
            folded = normalized.casefold()
            if folded in status_by_name:
                return status_by_name[folded]
        return None

    async def fetch_sensor_data(self) -> list[dict[str, Any]]:
        """Fetch and merge sensor devices with their latest status.

        Mirrors the official approach:
        1) fetch_device_statuses() -> temp/humi/battery fields
        2) fetch_devices() + fetch_pids()
        3) filter devices by PIDs "sensor"
        4) merge status into filtered devices
        """
        statuses = await self.fetch_device_statuses()
        devices = await self.fetch_devices()
        pids_data = await self.fetch_pids()

        _LOGGER.debug("fetch_sensor_data: pids_data keys=%s, devices=%d, statuses=%d",
                      list(pids_data.keys()), len(devices), len(statuses))
        for dev in devices:
            _LOGGER.debug("  device: name=%s deviceMoldPid=%s",
                          dev.get("deviceGroupName") or dev.get("deviceName"),
                          dev.get("deviceMoldPid"))

        # 尝试多种可能的 PID 键名（不同接口/固件可能不同）
        sensor_pids_str = (
            pids_data.get("sensor", "")
            or pids_data.get("th", "")
            or pids_data.get("thermometer", "")
            or pids_data.get("humidity", "")
        )
        sensor_pids = {pid.strip() for pid in sensor_pids_str.split(",") if pid.strip()}
        _LOGGER.debug("fetch_sensor_data: sensor_pids=%s", sensor_pids)

        if not sensor_pids:
            _LOGGER.debug("fetch_sensor_data: no sensor PIDs in pids_data=%s", pids_data)
            return []

        sensor_devices = [
            dev
            for dev in devices
            if dev.get("deviceMoldPid", "") in sensor_pids
        ]

        # 诊断：hass/status 无 temp/humi 时，打印传感器设备完整结构（含嵌套）以便排查
        for dev in sensor_devices:
            if self._merged_has_temp_or_humi(dev):
                _LOGGER.debug("Sensor device has temp/humi from hass/devices")
                break
        else:
            # 无温湿度时，打印首台传感器设备的 keys 及嵌套对象 keys
            if sensor_devices:
                d = sensor_devices[0]
                nested = {k: list(v.keys()) if isinstance(v, dict) else type(v).__name__
                          for k, v in d.items() if isinstance(v, (dict, list)) and k not in ("deviceFeatures",)}
                _LOGGER.debug(
                    "fetch_sensor_data: thermometer device keys=%s, nested=%s",
                    list(d.keys()), nested,
                )

        # 按 deviceName 索引（含 strip / 小写 便于匹配）
        status_by_name: dict[str, Any] = {}
        for st in statuses:
            key = st.get("deviceName")
            if key is None:
                continue
            ks = str(key).strip()
            status_by_name[ks] = st
            status_by_name[ks.casefold()] = st

        merged: list[dict[str, Any]] = []
        for dev in sensor_devices:
            merged_device = dev.copy()
            status = self._find_status_for_sensor(dev, statuses, status_by_name)
            if status:
                merged_device.update(status)
            else:
                _LOGGER.debug(
                    "fetch_sensor_data: no status row matched for sensor "
                    "deviceName=%s deviceId=%s deviceGroupName=%s "
                    "(status sample keys: %s)",
                    dev.get("deviceName"),
                    dev.get("deviceId"),
                    dev.get("deviceGroupName"),
                    list(statuses[0].keys()) if statuses else [],
                )
            merged.append(merged_device)

        self._attach_orphan_sensor_statuses(merged, statuses)

        return merged

    @staticmethod
    def _merged_has_temp_or_humi(d: dict[str, Any]) -> bool:
        """合并后的设备是否已有温湿度可读字段."""
        keys = ("temp", "humi", "temperature", "humidity")
        for k in keys:
            v = d.get(k)
            if v is not None and v != "":
                return True
        for ck in ("deviceData", "data", "sensorData"):
            inner = d.get(ck)
            if isinstance(inner, dict):
                for k in keys:
                    v = inner.get(k)
                    if v is not None and v != "":
                        return True
        return False

    @staticmethod
    def _status_has_sensor_payload(st: dict[str, Any]) -> bool:
        keys = ("temp", "humi", "temperature", "humidity", "battery", "bettery")
        for k in keys:
            v = st.get(k)
            if v is not None and v != "":
                return True
        for ck in ("deviceData", "data", "sensorData", "extData", "extend", "ext"):
            inner = st.get(ck)
            if isinstance(inner, dict):
                for k in keys:
                    v = inner.get(k)
                    if v is not None and v != "":
                        return True
        return False

    def _attach_orphan_sensor_statuses(
        self,
        merged: list[dict[str, Any]],
        statuses: list[dict[str, Any]],
    ) -> None:
        """若仅一台传感器且主匹配未带上温湿度，则合并任意一条含读数的状态行（云端 deviceName 常不一致）。"""
        if len(merged) != 1:
            return
        if self._merged_has_temp_or_humi(merged[0]):
            return
        candidates = [s for s in statuses if self._status_has_sensor_payload(s)]
        if not candidates:
            _LOGGER.debug(
                "fetch_sensor_data: no status row contains temp/humi among %d statuses; "
                "keys per row: %s",
                len(statuses),
                [list(s.keys()) for s in statuses],
            )
            return
        best = max(
            candidates,
            key=lambda s: sum(
                1
                for k in ("temp", "humi", "temperature", "humidity", "battery", "bettery")
                if s.get(k) not in (None, "")
            ),
        )
        merged[0].update(best)
        _LOGGER.debug(
            "fetch_sensor_data: merged orphan status row (deviceName=%s) for single sensor",
            best.get("deviceName"),
        )

    @staticmethod
    def _find_status_for_sensor(
        dev: dict[str, Any],
        statuses: list[dict[str, Any]],
        status_by_name: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Match hass/status row to hass/devices row (官方仅用 deviceName，这里多几种兼容)."""
        device_name = dev.get("deviceName")
        device_id = dev.get("deviceId")
        group_name = dev.get("deviceGroupName")

        if device_name:
            dn = str(device_name).strip()
            if dn in status_by_name:
                return status_by_name[dn]
            dnc = dn.casefold()
            if dnc in status_by_name:
                return status_by_name[dnc]

        for st in statuses:
            st_name = st.get("deviceName")
            st_id = st.get("deviceId")
            if device_name and st_name is not None:
                if str(device_name).strip() == str(st_name).strip():
                    return st
                if str(device_name).strip().casefold() == str(st_name).strip().casefold():
                    return st
            if device_id is not None and str(st_id) == str(device_id):
                return st
            if device_id is not None and st_name is not None and str(st_name).strip() == str(device_id):
                return st
            if group_name and st_name is not None:
                if str(group_name).strip() == str(st_name).strip():
                    return st
                if str(group_name).strip().casefold() == str(st_name).strip().casefold():
                    return st
        return None

    @staticmethod
    def _ha_brightness_to_percent(brightness: int) -> int:
        """Convert Home Assistant brightness (0-255) to DayBetter percent (0-100)."""
        if brightness <= 0:
            return 0
        return max(1, min(100, round(brightness * 100 / 255)))
            
    async def control_device(
        self, 
        device_name: str, 
        action: bool, 
        brightness: int | None, 
        hs_color: tuple[float, float] | None, 
        color_temp: int | None
    ) -> dict[str, Any]:
        """Control a device."""
        session = async_get_clientsession(self.hass)
        url = "https://a.dbiot.org/daybetter/hass/api/v1.0/hass/control"
        headers = {"Authorization": f"Bearer {self.token}"}

        # Priority: color temperature > color > brightness > switch
        if color_temp is not None:
            # Convert mireds to Kelvin
            kelvin = int(1000000 / color_temp)
            payload = {
                "deviceName": device_name, 
                "type": 4,  # Type 4 is color temperature control
                "kelvin": kelvin
            }
        elif hs_color is not None:
            h, s = hs_color
            v = (brightness / 255) if brightness is not None else 1.0
            payload = {
                "deviceName": device_name, 
                "type": 3, 
                "hue": h,
                "saturation": s / 100,
                "brightness": v
            }
        elif brightness is not None:
            payload = {
                "deviceName": device_name, 
                "type": 2, 
                "brightness": self._ha_brightness_to_percent(brightness)
            }
        else:
            # Type 1 control switch is used by default
            payload = {"deviceName": device_name, "type": 1, "on": action}
            
        async with session.post(url, headers=headers, json=payload) as resp:
            return await resp.json()
