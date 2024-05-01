"""Heatzy device."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

import aiohttp


class Model(Enum):
    PILOTE_V1 = "9420ae048da545c88fc6274d204dd25f"
    PILOTE2 = "51d16c22a5f74280bc3cfe9ebcdc6402"
    PILOTE_SOC = "b9a67b6ce24b437d9794103fd317e627"
    PILOTE2_ELEC_PRO = "4fc968a21e7243b390e9ede6f1c6465d"
    ELEC_PRO_SOC = "b8c6657b66c34148b4dee64d615cefc7"
    ELEC_PRO_BLE = "9dacde7ef459421eaf8dc4bea9385634"
    GLOW_SIMPLE = "2fd622e45283470f9e27e8e6167d7533"
    ONYX = "bb10d064f8de409db633b750faa22a52"
    BLOOM = "480253852d574f11b2d7fbf4460d7a41"
    PILOTE_SOC_C3 = "46409c7f29d4411c85a3a46e5ee3703e"
    FLAM = "f71ee820660f4f358db8b8a474689726"
    GLOW = "51c35c204f854cebbc780bf9785db409"
    GLOW_BLU = "cffa0df68a52449085c5d1e72c2f6bb0"
    INEA = "fc89066ee74c4149a9beb37d4ea93604"
    ONY = "bb10d064f8de409db633b750faa22a52"
    ROCKET = "d60c58d724b845068bf4652a10883c92"
    SHINE_BLU = "2884feb88e0b4f30b75ea5572276a102"
    SOCKET = "0a000341206048a0be23e2cbf9d8f29a"
    VULCANE = "e5d5766484db4b4d9b9fa7c08cd65481"


class TD:
    V1: list[Enum] = [Model.PILOTE_V1]
    V2: list[Enum] = [
        Model.PILOTE2,
        Model.PILOTE2,
        Model.PILOTE_SOC,
        Model.ELEC_PRO_SOC,
        Model.ELEC_PRO_BLE,
    ]
    GLOW: list[Enum] = [Model.GLOW_SIMPLE, Model.ONYX]


@dataclass
class Device:
    """Device representation."""

    data: dict[str, Any]
    websocket: aiohttp.ClientWebSocketResponse
    attrs: dict[str, Any] = field(default_factory=lambda: dict())

    @property
    def is_online(self) -> bool:
        """Is online."""
        return self.data.get("is_online") is True

    @property
    def has_lock(self) -> bool:
        """Has lock."""
        return self.model not in TD.V1

    @property
    def is_locked(self) -> bool:
        """Is locked."""
        return self.attrs.get("lock_switch") is True

    @property
    def is_boost(self) -> bool:
        """Is boost enabled."""
        return self.attrs.get("boost_switch") is True

    @property
    def is_timer(self) -> bool:
        """Is timer enabled."""
        return self.attrs.get("timer_switch") is True

    @property
    def name(self) -> str:
        """Return name."""
        return cast(str, self.data.get("dev_alias"))

    @property
    def did(self) -> str:
        """Return device id."""
        return cast(str, self.data.get("did"))

    @property
    def mode(self) -> str:
        """Return mode."""
        return cast(str, self.attrs.get("mode"))

    @property
    def product_name(self) -> str:
        """Return product name."""
        return cast(str, self.data.get("product_name"))

    @property
    def product_key(self) -> str:
        """Return product key."""
        return cast(str, self.data.get("product_key"))

    @property
    def model(self) -> str | None:
        """Return model."""
        for model in list(Model):
            if self.product_key == model.value:
                return model

    async def async_update(self, data: dict[str, Any]) -> None:
        """Update attributes."""
        self.attrs = data.get("attrs", {})

    async def async_control(self, payload: dict[str, Any]) -> None:
        """Send command to device."""
        payload = {"raw": payload} if self.model in TD.V1 else {"attrs": payload}
        await self.websocket.async_control_device(self.did, payload)

    async def async_turn_on(self) -> None:
        """Turn on device."""
        attrs = (
            {"raw": [0, 0, 0, 3, 6, 0, 0, 144, 1, 1, 0]}
            if self.model in TD.V1
            else {"mode": "cft"}
        )
        await self.async_control(attrs)

    async def async_turn_off(self) -> None:
        """Turn on device."""
        attrs = (
            {"raw": [0, 0, 0, 3, 6, 0, 0, 144, 1, 1, 3]}
            if self.model in TD.V1
            else {"mode": "stop"}
        )
        await self.async_control(attrs)

    async def async_turn_auto(self) -> None:
        """Turn on device."""
        await self.async_control({"timer_switch": 1, "derog_mode": 0, "derog_time": 0})

    async def async_lock(self) -> None:
        """Lock device."""
        if not self.has_lock:
            raise NotImplementedError
        await self.async_control({"lock_switch": 0})

    async def async_unlock(self) -> None:
        """Unlock device."""
        if not self.has_lock:
            raise NotImplementedError
        await self.async_control({"lock_switch": 1})

    async def async_derogation(self, state: bool, duration: int = 0) -> None:
        """Set derogation."""
        attrs = {
            "timer_switch": 0,
            "derog_mode": 1 if state else 0,
            "derog_time": duration,
        }
        await self.async_control(attrs)
