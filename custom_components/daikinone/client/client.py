"""DaikinOne orchestration: holds transport + thermostat cache, exposes the public API."""

import copy
import logging
from typing import Any

from custom_components.daikinone.client.mapping import (
    SPLIT_SWING_FIXED,
    SPLIT_SWING_OSCILLATE,
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

    # P1/P2 split control writes. iduOperatingMode uses the read encoding
    # (1=heat, 2=cool, 3=auto); power is a separate iduOnOff flag.
    _SPLIT_MODE_WRITE: dict[DaikinThermostatMode, dict[str, object]] = {
        DaikinThermostatMode.OFF: {"iduOnOff": False},
        DaikinThermostatMode.HEAT: {"iduOnOff": True, "iduOperatingMode": 1},
        DaikinThermostatMode.COOL: {"iduOnOff": True, "iduOperatingMode": 2},
        DaikinThermostatMode.AUTO: {"iduOnOff": True, "iduOperatingMode": 3},
    }

    async def set_thermostat_mode(self, thermostat_id: str, mode: DaikinThermostatMode) -> None:
        """Set thermostat mode"""
        if self.is_split(thermostat_id):
            body = self._SPLIT_MODE_WRITE.get(mode)
            if body is None:
                raise ValueError(f"Unsupported mode for mini split: {mode}")
            await self._transport.request(
                url=f"{DAIKIN_API_URL_DEVICE_DATA}/{thermostat_id}",
                method="PUT",
                body=dict(body),
            )
            return
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

        if self.is_split(thermostat_id):
            # Mini splits take per-mode setpoints on the raw idu* fields, in
            # Celsius at 0.5 resolution. iduTargetTemp is the computed active
            # value (read-only); iduHeat/CoolSetpoint are the settable ones.
            body: dict[str, Any] = {}
            if heat:
                body["iduHeatSetpoint"] = round(heat.celsius * 2) / 2
            if cool:
                body["iduCoolSetpoint"] = round(cool.celsius * 2) / 2
            await self._transport.request(
                url=f"{DAIKIN_API_URL_DEVICE_DATA}/{thermostat_id}",
                method="PUT",
                body=body,
            )
            return

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
        if self.is_split(thermostat_id):
            # P1/P2 mini splits do not support fan-circulation control (fan runs
            # at the unit's own speed); ignore rather than sending a bad write.
            log.debug("Ignoring fan mode set for mini split %s (unsupported)", thermostat_id)
            return
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

    async def set_split_swing(self, thermostat_id: str, oscillate: bool) -> None:
        """Set vertical louver oscillation on a mini split.

        The louver position is stored per operating mode; set all of them
        together so the setting is consistent regardless of the current mode
        (0 = fixed, 15 = oscillate).
        """
        value = SPLIT_SWING_OSCILLATE if oscillate else SPLIT_SWING_FIXED
        await self._transport.request(
            url=f"{DAIKIN_API_URL_DEVICE_DATA}/{thermostat_id}",
            method="PUT",
            body={
                "iduHeatAirDirectionUpDown": value,
                "iduCoolAirDirectionUpDown": value,
                "iduAutoAirDirectionUpDown": value,
            },
        )
