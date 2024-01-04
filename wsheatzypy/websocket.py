"""Class for websocket."""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING, Any, Callable, cast

import aiohttp
from yarl import URL

from .const import HEATZY_APPLICATION_ID, WS_HOST, WS_PING_INTERVAL, WS_PORT
from .exception import AuthenticationFailed, ConnectionFailed, WebsocketError

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .auth import Auth


class Websocket:
    """Heatzy websocket."""

    def __init__(self, session: aiohttp.ClientSession, auth: Auth, retry: int) -> None:
        """Initialize."""
        self.session = session
        self._auth = auth
        self._client: aiohttp.ClientWebSocketResponse = cast(
            aiohttp.ClientWebSocketResponse, None
        )
        self.bindings: dict[str, Any] = {}
        self.devices: dict[str, Any] = {}
        self._retry: int = retry
        self._return_all: bool = False

    @property
    def is_connected(self) -> bool:
        """Return if we are connect to the WebSocket."""
        return self._client is not None and not self._client.closed

    async def async_bindings(self) -> None:
        """Return bindings devices."""
        bindings = await self._auth.request("bindings")
        self.bindings = {
            device["did"]: device for device in bindings.get("devices", {})
        }

    async def async_get_device(self, device_id) -> None:
        """Return device data while listen connection."""
        if not self._client or not self.is_connected:
            msg = "Not connected to a Heatzy WebSocket"
            raise WebsocketError(msg)

        c2s = {"cmd": "c2s_read", "data": {"did": device_id}}
        _LOGGER.debug("WEBSOCKET >>> %s", c2s)
        await asyncio.wait_for(self._client.send_json(c2s), 60)

        return self.bindings.get(device_id)

    async def async_get_devices(self) -> None:
        """Return all devices data while listen connection."""
        if not self._client or not self.is_connected:
            msg = "Not connected to a Heatzy WebSocket"
            raise WebsocketError(msg)

        for did in self.bindings:
            c2s = {"cmd": "c2s_read", "data": {"did": did}}
            _LOGGER.debug("WEBSOCKET >>> %s", c2s)
            await asyncio.wait_for(self._client.send_json(c2s), 60)

        return self.bindings

    async def async_control(
        self, device_id: str, payload: dict[str, dict[str, Any]]
    ) -> None:
        """Send command to device.

        Args:
        ----
            - payload: raw or attrs dictionary containing the actions dictionary
             {"raw": {"mode":[1,1,3]}} or {"attrs": {"mode": "cft"} }
        """
        if not self._client or not self.is_connected:
            msg = "Not connected to a Heatzy WebSocket"
            raise WebsocketError(msg)

        cmd = "c2s_raw" if payload.get("raw") else "c2s_write"
        c2s = {"cmd": cmd, "data": {"did": device_id, **payload}}
        _LOGGER.debug("WEBSOCKET >>> %s", c2s)
        await self._client.send_json(c2s)

    async def _async_heartbeat(self) -> None:
        """Heatbeat websocket."""
        while not self._client.closed:
            c2s = {"cmd": "ping"}
            _LOGGER.debug("WEBSOCKET >>> %s", c2s)
            await self._client.send_json(c2s)
            await asyncio.sleep(WS_PING_INTERVAL)

    async def async_connect(self, auto_subscribe=True) -> None:
        """Connect to the WebSocket.

        Args:
        ---
            - auto_subscribe set True the server automatically subscribes to all the bound devices
            if false, you need to select the devices to be subscribed to through the following async_subscribe
        """
        if self.is_connected:
            return

        if not self.bindings:
            await self.async_bindings()

        if not self.session:
            msg = f"The device at {WS_HOST} does not support WebSockets"
            raise WebsocketError(msg)

        url = URL.build(scheme="ws", host=WS_HOST, port=WS_PORT, path="/ws/app/v1")

        try:
            self._client = await self.session.ws_connect(url=url)
            _LOGGER.debug("WEBSOCKET Connected to a %s Websocket", WS_HOST)
        except (
            aiohttp.WSServerHandshakeError,
            aiohttp.ClientConnectionError,
            socket.gaierror,
        ) as exception:
            msg = f"Error occurred while communicating with device on WebSocket at {WS_HOST}"
            raise ConnectionFailed(msg) from exception

        try:
            await self.async_login(auto_subscribe)
        except WebsocketError as error:
            raise AuthenticationFailed(error) from error

    async def async_login(self, auto_subscribe=True) -> None:
        """Login to websocket."""
        if not self._client or not self.is_connected:
            msg = "Not connected to a Heatzy WebSocket"
            raise WebsocketError(msg)

        token_data = await self._auth.async_get_token()

        c2s = {
            "cmd": "login_req",
            "data": {
                "appid": HEATZY_APPLICATION_ID,
                "uid": token_data.get("uid"),
                "token": token_data.get("token"),
                "p0_type": "attrs_v4",
                "heartbeat_interval": WS_PING_INTERVAL,
                "auto_subscribe": auto_subscribe,
            },
        }
        _LOGGER.debug("WEBSOCKET >>> %s", c2s)
        await asyncio.wait_for(self._client.send_json(c2s), 60)

    async def async_listen(
        self,
        callback: Callable[..., None] | None = None,
        callbackChange: Callable[..., None] | None = None,
        callbackStatus: Callable[..., None] | None = None,
        all_devices=False,
    ) -> None:
        """Listen for events on the WebSocket.

        Args:
        ----
            callback: Method to call when a state update is received from the device.
            callbackChange: Method to call when the device is bound or unbound by the user.
            callbackStatus: Method to call when the device goes online or offline.
            all_devices: set True , returns all devices in the callback
            instead of the device that performed the update
        """
        if not self._client or not self.is_connected:
            _LOGGER.debug("WEBSOCKET Connect to the %s Websocket", WS_HOST)
            await self.async_connect()

        try:
            if all_devices:
                self._return_all = all_devices
                await self.async_get_devices()
        except WebsocketError as error:
            raise WebsocketError("Fetch all devices failed (%s)", error) from error

        asyncio.create_task(self._async_heartbeat())

        while not self._client.closed:
            message = await self._client.receive()

            if message.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionFailed(self._client.exception())

            if message.type == aiohttp.WSMsgType.TEXT:
                message_data = message.json()
                _LOGGER.debug("WEBSOCKET <<< %s", message_data)
                data = message_data.get("data")
                if isinstance(data, dict):
                    match message_data.get("cmd"):
                        case "s2c_invalid_msg":
                            if data.get("error_code") == 1003:
                                await self.async_login()
                                continue
                            _LOGGER.error(
                                "WEBSOCKET encounters errors (%s)", data.get("msg")
                            )
                            if self._retry > 0:
                                self._retry -= 1
                                await self.async_disconnect()
                                return await self.async_listen(callback)
                            raise WebsocketError(
                                "WEBSOCKET encountered too many errors. It was interrupted"
                            )
                        case "login_res":
                            self._retry = 3
                            if message_data.get("data", {}).get("success") is False:
                                raise AuthenticationFailed(message_data)
                            _LOGGER.debug(
                                "WEBSOCKET Successfully authenticated to %s", WS_HOST
                            )
                        case "s2c_noti":
                            self._retry = 3
                            if callback:
                                if self._return_all is False:
                                    callback(data)
                                elif self.merge_data(self.bindings, data):
                                    callback(self.bindings)
                        case "s2c_binding_changed":
                            self._retry = 3
                            await self.async_bindings()
                            if (did := data.get("did")) and data.get("bind"):
                                await self.async_get_device(did)
                            if (did := data.get("did")) and (data.get("bind") is False):
                                await self.bindings.pop(did, None)
                            if callbackChange:
                                callbackChange(data)
                        case "s2c_online_status":
                            self._retry = 3
                            if did := data.get("did"):
                                await self.async_get_device(did)
                            if callbackStatus:
                                callbackStatus(data)
                        case "pong":
                            self._retry = 3

            if message.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
            ):
                if self._retry > 0:
                    self._retry -= 1
                    _LOGGER.error(
                        "Connection to the WebSocket has been closed, retry (%s)",
                        self._retry,
                    )
                    await self.async_connect()
                    await self.async_listen(callback, callbackChange, callbackStatus)
                raise WebsocketError("Connection to the WebSocket has been closed")

    async def async_disconnect(self) -> None:
        """Disconnect from the WebSocket of a device."""
        if not self._client or not self.is_connected:
            return

        await self._client.close()

    async def async_subscribe(self, device_ids: list[str]) -> None:
        """Subscribed to the bound device.

        This API only applies to scenarios where the connect or login parameter auto_subscribe is set to false

        Args:
        ----
            - device_ids : Array of did
        """
        if not self._client or not self.is_connected:
            msg = "Not connected to a Heatzy WebSocket"
            raise WebsocketError(msg)

        dids = [{"did": did} for did in device_ids]

        c2s = {"cmd": "subscribe_req", "data": dids}
        _LOGGER.debug("WEBSOCKET >>> %s", c2s)
        await self._client.send_json(c2s)

    @staticmethod
    def merge_data(bindings, data) -> bool:
        """Merge data."""
        if bindings and (did := data.get("did")) and (device := bindings.get(did)):
            device["attrs"] = data.get("attrs", {})

        n_devices = len(bindings.keys())
        for binding in bindings.values():
            if binding.get("attrs"):
                n_devices -= 1

        return n_devices == 0
