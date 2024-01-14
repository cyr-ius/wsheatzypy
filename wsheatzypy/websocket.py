"""Class for websocket."""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import TYPE_CHECKING, Any, Callable, cast

import aiohttp
from yarl import URL as yurl

from .const import APPLICATION_ID, WS_PING_INTERVAL, WS_PORT, WSS_PORT
from .device import Device
from .exception import AuthenticationFailed, ConnectionFailed, WebsocketError

if TYPE_CHECKING:
    from .auth import Auth

logger = logging.getLogger(__name__)


class Websocket:
    """Heatzy websocket."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: Auth,
        host: str,
        use_tls: bool = True,
    ) -> None:
        """Initialize."""
        self.session = session
        self._auth = auth
        self._ws: aiohttp.ClientWebSocketResponse = cast(
            aiohttp.ClientWebSocketResponse, None
        )
        self.devices: dict[str, Device] = {}
        self._return_all: bool = False
        self._host = host
        self._scheme = "wss" if use_tls else "ws"
        self._port = WSS_PORT if use_tls else WS_PORT

        self.logged_in: bool = False
        self.subscribed_devices: list[str] = []
        self.last_invalid_msg: dict[str, Any] | None = None

        self._callback: Callable[..., None] | None = None
        self._event: asyncio.Event | None = None

    @property
    def is_connected(self) -> bool:
        """Return if we are connect to the WebSocket."""
        return self._ws is not None and not self._ws.closed

    async def async_fetch_binding_devices(self) -> None:
        """Return bindings devices."""
        bindings = await self._auth.request("bindings")
        for info in bindings.get("devices", {}):
            self.devices.update({info["did"]: Device(data=info, websocket=self)})

    async def async_get_device(self, device_id: str) -> Device | None:
        """Return device data while listen connection."""
        payload = {"cmd": "c2s_read", "data": {"did": device_id}}
        await self._send_cmd(payload)
        return self.devices.get(device_id)

    async def async_get_devices(self) -> dict[str, Device]:
        """Return all devices data while listen connection."""
        for did in self.devices:
            payload = {"cmd": "c2s_read", "data": {"did": did}}
            await self._send_cmd(payload)
        return self.devices

    async def async_control_device(
        self, device_id: str, payload: dict[str, dict[str, Any]]
    ) -> None:
        """Send command to device.

        Args:
        ----
            - payload: raw or attrs dictionary containing the actions dictionary
             {"raw": [1,1,3]} or {"attrs": {"mode": "cft"} }
        """
        cmd = "c2s_raw" if payload.get("raw") else "c2s_write"
        control = {"cmd": cmd, "data": {"did": device_id, **payload}}
        await self._send_cmd(control)

    async def _async_heartbeat(self) -> None:
        """Heatbeat websocket."""
        while not self._ws.closed:
            await self.async_ping()
            await asyncio.sleep(WS_PING_INTERVAL)

    async def async_ping(self) -> None:
        """Send ping."""
        await self._send_cmd({"cmd": "ping"})

    async def async_connect(
        self,
        auto_subscribe: bool = True,
        all_devices: bool = False,
        callback: Callable[..., None] | None = None,
        event: asyncio.Event | None = None,
    ) -> None:
        """Connect to the WebSocket.

        Args:
        ---
            - auto_subscribe set True the server automatically subscribes to all the bound devices
              if false, you need to select the devices to be subscribed to through the following async_subscribe
            - callback: Method to call when a state update is received from the device.
            - all_devices: set True , returns devices object dictionary dict[did, Device] in the callback instead of the Device object
            - event: trigger Event.set()
        """
        self._return_all = all_devices is True
        self._callback = callback
        self._event = event

        if self.is_connected:
            return

        if not self.devices:
            await self.async_fetch_binding_devices()

        if not self.session:
            raise WebsocketError("Session not found")

        try:
            url = yurl.build(
                scheme=self._scheme, host=self._host, port=self._port, path="/ws/app/v1"
            )
            self._ws = await self.session.ws_connect(url=url)
            logger.debug("WEBSOCKET Connected to a %s Websocket", url)

            # Create a background task to receive messages
            asyncio.ensure_future(self.async_listen(self._ws))

        except (
            aiohttp.WSServerHandshakeError,
            aiohttp.ClientConnectionError,
            socket.gaierror,
        ) as exception:
            raise ConnectionFailed(
                f"Error occurred while communicating to websocket at {url}"
            ) from exception

        try:
            await self.async_login(auto_subscribe)
        except WebsocketError as error:
            raise AuthenticationFailed(error) from error

        try:
            if self._return_all:
                await self.async_get_devices()
        except WebsocketError as error:
            raise AuthenticationFailed(error) from error

    async def async_login(self, auto_subscribe: bool = True) -> None:
        """Login to websocket."""

        token_data = await self._auth.async_get_token()

        payload = {
            "cmd": "login_req",
            "data": {
                "appid": APPLICATION_ID,
                "uid": token_data.get("uid"),
                "token": token_data.get("token"),
                "p0_type": "attrs_v4",
                "heartbeat_interval": WS_PING_INTERVAL,
                "auto_subscribe": auto_subscribe,
            },
        }
        await self._send_cmd(payload)
        asyncio.create_task(self._async_heartbeat())

    async def async_listen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Listen for events on the WebSocket.

        Args:
        ----
            callback: Method to call when a state update is received from the device.
            callbackChange: Method to call when the device is bound or unbound by the user.
            callbackStatus: Method to call when the device goes online or offline.
            all_devices: set True , returns all devices in the callback
            instead of the device that performed the update
            event: trigger Event.set()
        """
        while not ws.closed:
            message = await ws.receive()

            if self._event:
                self._event.set()

            if message.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionFailed(self._ws.exception())

            if message.type == aiohttp.WSMsgType.BINARY:
                pass

            if message.type == aiohttp.WSMsgType.TEXT:
                try:
                    message_data = message.json()
                    logger.debug(f"WEBSOCKET <<< {message_data}")
                    data = message_data.get("data")
                    cmd = message_data.get("cmd")
                    if data and cmd:
                        self.last_invalid_msg = None
                        match cmd:
                            case "login_res":
                                await self._handle_login(data)
                            case "subscribe_res":
                                await self._handle_subscription(data)
                            case "s2c_invalid_msg":
                                await self._handle_invalid_msg(data)
                            case "s2c_noti":
                                await self._handle_notification(data)
                            case "s2c_binding_changed":
                                await self._handle_binding_change(data)
                            case "s2c_online_status":
                                await self._handle_status_change(data)
                    elif cmd == "pong":
                        await self._hand_pong(message_data)
                    else:
                        logger.warn(f"Received invalid message: {message}")
                except json.JSONDecodeError:
                    logger.error("Invalid JSON format for the received message.")

            if message.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
            ):
                raise WebsocketError("Connection to the WebSocket has been closed")

    async def async_disconnect(self) -> None:
        """Disconnect from the WebSocket of a device."""
        if not self._ws or not self.is_connected:
            return

        await self._ws.close()

    async def async_subscribe(self, device_ids: list[str]) -> None:
        """Subscribed to the bound device.

        This API only applies to scenarios where the connect or login parameter auto_subscribe is set to false

        Args:
        ----
            - device_ids : Array of did
        """
        dids = [{"did": did} for did in device_ids]
        payload = {"cmd": "subscribe_req", "data": dids}
        await self._send_cmd(payload)

    async def _hand_pong(self, data: dict[str, Any]) -> None:
        """Handle ping receive."""
        pass

    async def _handle_login(self, data: dict[str, Any]) -> None:
        """Handle login response."""
        if data.get("success") is False:
            raise AuthenticationFailed(data)
        self.logged_in = True

    async def _handle_subscription(self, data: dict[str, Any]) -> None:
        """Handle the response of subscription."""
        devices = cast(list[Any], data.get("success"))
        for device in devices:
            if (did := device["did"]) not in self.subscribed_devices:
                self.subscribed_devices.append(did)

    async def _handle_notification(self, data: dict[str, Any]) -> None:
        """Handle a notification receive by client."""
        if did := data.get("did"):
            device = self.devices.get(did)
            if device:
                await device.async_update(data)
                if fn := self._callback:
                    if self._return_all:
                        if self.check_full(self.devices):
                            fn(self.devices)
                    else:
                        fn(device)

    async def _handle_invalid_msg(self, data: dict[str, Any]) -> None:
        """Handle a notification receive by client."""
        logger.warn("Received invalid message: %s", data)
        self.last_invalid_msg = data

    async def _handle_binding_change(self, data: dict[str, Any]) -> None:
        """Handle a new binding status."""
        if (did := data.get("did")) and (data.get("bind") is False):
            self.devices.pop(did, None)
        elif did:
            bindings = await self._auth.request("bindings")
            if bindings and (data := bindings.get(did, {})):
                self.devices.update({did: Device(data=data, websocket=self)})

    async def _handle_status_change(self, data: dict[str, Any]) -> None:
        """Handle a new status."""
        if device := self.devices.get(data.get("did", "")):
            await device.async_update(data)

    async def _send_cmd(self, payload: dict[str, Any]) -> None:
        """Send command to websocket."""
        if not self._ws or not self.is_connected:
            raise WebsocketError("Not connected to a Heatzy WebSocket")

        logger.debug("WEBSOCKET >>> %s", payload)
        await self._ws.send_json(payload)

    @staticmethod
    def check_full(devices: dict[str, Any]) -> bool:
        """Merge data."""
        attrs_fills = [device.did for device in devices.values() if device.attrs]
        return list(devices.keys()) == attrs_fills
