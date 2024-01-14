"""Heatzy device."""
from dataclasses import dataclass, field
from typing import Any, cast

import aiohttp


@dataclass
class Device:
    """Device representation."""

    data: dict[str, Any]
    websocket: aiohttp.ClientWebSocketResponse
    attrs: dict[str, Any] = field(default_factory=lambda: dict())

    @property
    def is_online(self) -> bool:
        return self.data.get("is_online") is True

    @property
    def is_locked(self) -> bool:
        return self.attrs.get("lock_switch") is True

    @property
    def is_boost(self) -> bool:
        return self.attrs.get("boost_switch") is True

    @property
    def is_timer(self) -> bool:
        return self.attrs.get("timer_switch") is True

    @property
    def name(self) -> str:
        return cast(str, self.data.get("dev_alias"))

    @property
    def did(self) -> str:
        return cast(str, self.data.get("did"))

    @property
    def mode(self) -> str:
        return cast(str, self.attrs.get("mode"))

    @property
    def product_name(self) -> str:
        return cast(str, self.data.get("product_name"))

    @property
    def product_key(self) -> str:
        return cast(str, self.data.get("product_key"))

    async def async_update(self, data: dict[str, Any]) -> None:
        """Update attributes."""
        self.attrs = data.get("attrs", {})

    async def async_control(self, payload: dict[str, Any]) -> None:
        """Send command to device."""
        await self.websocket.async_control_device(self.did, payload)
