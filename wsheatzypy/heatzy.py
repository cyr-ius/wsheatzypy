"""Heatzy API."""
from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable
from typing import Any, Self, cast

import aiohttp  # pylint: disable=import-error
from aiohttp import ClientSession  # pylint: disable=import-error
from yarl import URL  # pylint: disable=import-error

from .auth import Auth
from .const import HEATZY_APPLICATION_ID, WS_HOST, WS_PORT
from .exception import ConnectionClose, ConnectionFailed, WebsocketError

_LOGGER = logging.getLogger(__name__)
HEARTBEAT_INTERVAL = 30
RETRY = 3
_LOGGER = logging.getLogger(__name__)


class HeatzyClient:
    """Heatzy Client data."""

    def __init__(
        self,
        username: str,
        password: str,
        session: ClientSession = ClientSession(),
        time_out: int = 120,
    ) -> None:
        """Load parameters."""
        self._auth = Auth(session, username, password, time_out)
        self.session = session
        self.request = self._auth.request

        self._client: aiohttp.ClientWebSocketResponse = cast(
            aiohttp.ClientWebSocketResponse, None
        )
        self._devices: dict[str, Any] = {}
        self._retry = RETRY

    @property
    def is_connected(self) -> bool:
        """Return if we are connect to the WebSocket."""
        return self._client is not None and not self._client.closed

    async def async_bindings(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch all configured devices."""
        return await self.request("bindings")

    async def async_control_device(
        self, device_id: str, payload: dict[str, Any]
    ) -> None:
        """Control state of device with given id."""
        await self.request(f"control/{device_id}", method="POST", json=payload)

    async def async_ws_listen(self, callback: Callable[..., None]) -> None:
        """Listen for events on the WebSocket.

        Args:
        ----
            callback: Method to call when a state update is received from the device.

        """
        if not self._client or not self.is_connected:
            _LOGGER.debug("Connect to the %s Websocket", WS_HOST)
            await self.async_connect()

        if not self._devices:
            await self.async_get_devices()

        asyncio.create_task(self._async_heartbeat())

        while not self._client.closed:
            message = await self._client.receive()

            if message.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionFailed(self._client.exception())

            if message.type == aiohttp.WSMsgType.TEXT:
                message_data = message.json()
                _LOGGER.debug(message_data)
                data = message_data.get("data")
                if isinstance(data, dict):
                    if error_code := data.get("error_code"):
                        _LOGGER.error("Websocket encounters errors (%s)", error_code)
                        if self._retry > 0:
                            self._retry -= 1
                            await self.async_disconnect()
                            return await self.async_ws_listen(callback)
                        raise WebsocketError(
                            "Websocket encountered too many errors. It was interrupted"
                        )
                    self._retry = 3
                    if did := data.get("did"):
                        self._devices[did]["attrs"] = data.get("attrs", {})
                        callback(self._devices)

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
                    await self.async_ws_listen(callback)
                raise WebsocketError("Connection to the WebSocket has been closed")

    async def async_get_devices(self) -> dict[str, Any]:
        """Fetch all data."""
        if not self._client or not self.is_connected:
            _LOGGER.debug("Login to the %s Websocket", WS_HOST)
            await self.async_connect()

        bindings = await self.async_bindings()
        self._devices = {
            did: device
            for device in bindings.get("devices", [])
            if isinstance(device, dict) and (did := device.get("did"))
        }

        for did, device in self._devices.items():
            read_data = {"cmd": "c2s_read", "data": {"did": did}}
            await self._client.send_json(read_data)
            message = await self._client.receive()

            if message.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionFailed(self._client.exception())

            if message.type == aiohttp.WSMsgType.TEXT:
                message_data = message.json()
                if message_data.get("cmd") == "s2c_noti":
                    _LOGGER.debug("=> %s: %s", device.get("dev_alias"), message_data)
                    device["attrs"] = message_data.get("data", {}).get("attrs", {})

            if message.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
            ):
                msg = f"Connection to the WebSocket on {WS_HOST} has been closed"
                raise WebsocketError(msg)

        return self._devices

    async def async_get_device(self, device_id: str) -> dict[str, Any] | None:
        """Fetch device with given id."""
        devices = await self.async_get_devices()
        return devices.get(device_id)

    async def async_disconnect(self) -> None:
        """Disconnect from the WebSocket of a device."""
        if not self._client or not self.is_connected:
            return

        await self._client.close()

    async def async_close(self) -> None:
        """Close open client (WebSocket) session."""
        await self.async_disconnect()
        if self.session:
            await self.session.close()

    async def __aenter__(self) -> Self:
        """Async enter."""
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        """Async exit."""
        await self.async_close()

    async def _async_heartbeat(self) -> None:
        while not self._client.closed:
            data = {"cmd": "ping"}
            _LOGGER.debug(data)
            await self._client.send_json(data)
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def async_connect(self) -> None:
        """Connect to the WebSocket."""
        if self.is_connected:
            return

        if not self.session:
            msg = f"The device at {WS_HOST} does not support WebSockets"
            raise WebsocketError(msg)

        url = URL.build(scheme="ws", host=WS_HOST, port=WS_PORT, path="/ws/app/v1")

        try:
            self._client = await self.session.ws_connect(url=url)
            _LOGGER.debug("Connected to a %s Websocket", WS_HOST)
        except (
            aiohttp.WSServerHandshakeError,
            aiohttp.ClientConnectionError,
            socket.gaierror,
        ) as exception:
            msg = f"Error occurred while communicating with device on WebSocket at {WS_HOST}"
            raise ConnectionFailed(msg) from exception

        try:
            await self.async_login()
        except WebsocketError as error:
            raise WebsocketError("Error occurred while authentication (%s)", error)

    async def async_login(self) -> None:
        if not self._client or not self.is_connected:
            msg = "Not connected to a Heatzy WebSocket"
            raise WebsocketError(msg)

        token_data = await self._auth.async_get_token()

        login = {
            "cmd": "login_req",
            "data": {
                "appid": HEATZY_APPLICATION_ID,
                "uid": token_data.get("uid"),
                "token": token_data.get("token"),
                "p0_type": "attrs_v4",
                "heartbeat_interval": HEARTBEAT_INTERVAL,
                "auto_subscribe": True,
            },
        }
        await self._client.send_json(login)
        message = await self._client.receive()

        if message.type == aiohttp.WSMsgType.ERROR:
            raise ConnectionFailed(self._client.exception())

        if message.type == aiohttp.WSMsgType.TEXT:
            message_data = message.json()
            if message_data.get("data", {}).get("success") is False:
                raise WebsocketError(message_data)
            _LOGGER.debug("Successfully authenticated to %s Websocket", WS_HOST)

        if message.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.CLOSING,
        ):
            msg = f"Connection to the WebSocket on {WS_HOST} has been closed"
            raise ConnectionClose(msg)

    async def async_get_event_device(self, device_id) -> None:
        """Return device data while listen connection."""
        if not self._client or not self.is_connected:
            msg = "Not connected to a Heatzy WebSocket"
            raise WebsocketError(msg)

        c2s = {"cmd": "c2s_read", "data": {"did": device_id}}
        _LOGGER.debug(c2s)
        await self._client.send_json(c2s)

    async def async_get_event_devices(self) -> None:
        """Return all devices data while listen connection."""
        if not self._client or not self.is_connected:
            msg = "Not connected to a Heatzy WebSocket"
            raise WebsocketError(msg)

        for device_id in self._devices:
            c2s = {"cmd": "c2s_read", "data": {"did": device_id}}
            _LOGGER.debug(c2s)
            await self._client.send_json(c2s)

    async def async_control_event(
        self, device_id: str, payload: dict[str, Any]
    ) -> None:
        if not self._client or not self.is_connected:
            msg = "Not connected to a Heatzy WebSocket"
            raise WebsocketError(msg)

        cmd = "c2s_raw" if payload.get("raw") else "c2s_write"
        c2s = {"cmd": cmd, "data": {"did": device_id, **payload}}
        _LOGGER.debug(c2s)
        await self._client.send_json(c2s)
