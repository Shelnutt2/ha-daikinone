"""DaikinOne orchestration: holds transport + thermostat cache, exposes the public API."""

import copy
import logging
from typing import Any

from custom_components.daikinone.client.mapping import (
    is_split_payload,
    map_split_thermostat,
    map_thermostat,
)
from custom_components.daikinone.client.models import (
    DaikinThermostat,
    DaikinThermostatFanMode,
    DaikinThermostatFanSpeed,
    DaikinThermostatMode,
    DaikinUserCredentials,
)
from custom_components.daikinone.client.transport import DaikinTransport
from custom_components.daikinone.client.wire import (
    DAIKIN_API_URL_DEVICE_DATA,
    DaikinDeviceDataResponse,
)
from custom_components.daikinone.utils import Temperature

log = logging.getLogger(__name__)


class DaikinOne:
    """Manages connection to Daikin API and fetching device data."""

    __thermostats: dict[str, DaikinThermostat] = dict()
    # device ids that are P1/P2 mini splits (need split-specific control writes)
    __split_ids: set[str] = set()

    def __init__(self, creds: DaikinUserCredentials) -> None:
        self._transport = DaikinTransport(creds)

    async def login(self) -> bool:
        return await self._transport.login()

    async def get_all_raw_device_data(self) -> list[dict[str, Any]]:
        """Get raw device data"""
        return await self._transport.request(DAIKIN_API_URL_DEVICE_DATA)

    async def get_raw_device_data(self, device_id: str) -> dict[str, Any]:
        """Get raw device data"""
        return await self._transport.request(f"{DAIKIN_API_URL_DEVICE_DATA}/{device_id}")

    async def update(self) -> None:
        raw = await self._transport.request(DAIKIN_API_URL_DEVICE_DATA)
        responses = [DaikinDeviceDataResponse(**d) for d in raw]

        for r in responses:
            if not r.online and len(r.data) == 0:
                log.warning(f"Skipping offline device with no data: {r.name} ({r.id})")
                continue
            try:
                if is_split_payload(r.data):
                    self.__thermostats[r.id] = map_split_thermostat(r)
                    self.__split_ids.add(r.id)
                else:
                    self.__thermostats[r.id] = map_thermostat(r)
            except Exception:
                log.exception(f"Failed to map device {r.name} ({r.id}); skipping")

        log.info(f"Cached {len(self.__thermostats)} thermostats")

    def is_split(self, device_id: str) -> bool:
        """Whether a device is a P1/P2 mini split (vs a One+ thermostat)."""
        return device_id in self.__split_ids

    def get_thermostat(self, thermostat_id: str) -> DaikinThermostat:
        return copy.deepcopy(self.__thermostats[thermostat_id])

    def get_thermostats(self) -> dict[str, DaikinThermostat]:
        return copy.deepcopy(self.__thermostats)

    async def set_thermostat_mode(self, thermostat_id: str, mode: DaikinThermostatMode) -> None:
        """Set thermostat mode"""
        await self._transport.request(
            url=f"{DAIKIN_API_URL_DEVICE_DATA}/{thermostat_id}",
            method="PUT",
            body={"mode": mode.value},
        )

    async def set_thermostat_home_set_points(
        self,
        thermostat_id: str,
        heat: Temperature | None = None,
        cool: Temperature | None = None,
        override_schedule: bool = False,
    ) -> None:
        """Set thermostat home set points"""
        if not heat and not cool:
            raise ValueError("At least one of heat or cool set points must be set")

        payload: dict[str, Any] = {}
        if heat:
            payload["hspHome"] = heat.celsius
        if cool:
            payload["cspHome"] = cool.celsius
        if override_schedule:
            payload["schedOverride"] = 1

        await self._transport.request(
            url=f"{DAIKIN_API_URL_DEVICE_DATA}/{thermostat_id}",
            method="PUT",
            body=payload,
        )

    async def set_thermostat_fan_mode(self, thermostat_id: str, fan_mode: DaikinThermostatFanMode) -> None:
        """Set thermostat fan mode"""
        await self._transport.request(
            url=f"{DAIKIN_API_URL_DEVICE_DATA}/{thermostat_id}",
            method="PUT",
            body={"fanCirculate": fan_mode.value},
        )

    async def set_thermostat_fan_speed(self, thermostat_id: str, fan_speed: DaikinThermostatFanSpeed) -> None:
        """Set thermostat fan speed"""
        await self._transport.request(
            url=f"{DAIKIN_API_URL_DEVICE_DATA}/{thermostat_id}",
            method="PUT",
            body={"fanCirculateSpeed": fan_speed.value},
        )
