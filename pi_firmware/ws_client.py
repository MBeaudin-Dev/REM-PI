"""WebSocket client for Server<->FieldNode communication

Disables the hardware if the server does not respond to a heartbeat within HEARTBEAT_TIMEOUT_SECONDS
"""

import asyncio
import json
import time

import websockets

HEARTBEAT_INTERVAL_SECONDS = 5
# Generous so a slow LAN doesn't trip the fail-safe between matches.
HEARTBEAT_TIMEOUT_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 4
RECONNECT_BACKOFF_SECONDS = 2
# After this long with no successful connection, assume server_url is stale
# and let main.py fall back to auto-discovery.
GIVE_UP_AFTER_SECONDS = 60


class ServerUnreachable(Exception):
    """Raised by run() once server_url hasn't been reachable for
    GIVE_UP_AFTER_SECONDS."""


def _to_ws_url(server_url):
    # http -> ws, https -> wss; assume ws if no scheme is given.
    if server_url.startswith("https://"):
        return "wss://" + server_url[len("https://"):]
    if server_url.startswith("http://"):
        return "ws://" + server_url[len("http://"):]
    return "ws://" + server_url


class FieldNodeClient:
    def __init__(self, server_url, cookie, field_name, hardware):
        self._url = _to_ws_url(server_url) + "/api/ws/field-node"
        self._cookie = cookie
        self._field_name = field_name
        self._hardware = hardware
        self._last_server_contact = time.monotonic()
        # When the current run of failed connection attempts started; None
        # while connected.
        self._disconnected_since = None

    async def run(self):
        """Reconnects forever, raising ServerUnreachable only after
        GIVE_UP_AFTER_SECONDS of continuous failure."""
        while True:
            try:
                await self._connect_and_serve()
            except Exception as e:
                print("Lost connection to server:", e)
                if self._disconnected_since is None:
                    self._disconnected_since = time.monotonic()
            # Never leave robots enabled while disconnected.
            self._hardware.disable()
            if (
                self._disconnected_since is not None
                and time.monotonic() - self._disconnected_since > GIVE_UP_AFTER_SECONDS
            ):
                raise ServerUnreachable(
                    "no successful connection to {} in over {}s".format(
                        self._url, GIVE_UP_AFTER_SECONDS
                    )
                )
            await asyncio.sleep(RECONNECT_BACKOFF_SECONDS)

    async def _connect_and_serve(self):
        async with websockets.connect(
            self._url, additional_headers=[("Cookie", self._cookie)]
        ) as ws:
            print("Connected to", self._url)
            self._last_server_contact = time.monotonic()
            self._disconnected_since = None
            # Run all three together. If any one stops or fails, close the
            # connection and let run() reconnect.
            tasks = [
                asyncio.ensure_future(self._heartbeat_loop(ws)),
                asyncio.ensure_future(self._receive_loop(ws)),
                asyncio.ensure_future(self._watchdog_loop()),
            ]
            try:
                done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    exc = task.exception()
                    if exc is not None:
                        raise exc
            finally:
                for task in tasks:
                    task.cancel()
                # Wait for cancellation to finish so no task is left pending
                # mid hardware write.
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _heartbeat_loop(self, ws):
        while True:
            await ws.send(
                json.dumps(
                    {
                        "type": "heartbeat",
                        "field": self._field_name,
                        "timestamp": time.time(),
                    }
                )
            )
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    async def _receive_loop(self, ws):
        async for raw in ws:
            # Any message counts as proof the server is alive.
            self._last_server_contact = time.monotonic()
            try:
                message = json.loads(raw)
            except ValueError:
                print("Malformed message from server:", raw)
                continue
            self._handle_message(message)

    async def _watchdog_loop(self):
        while True:
            await asyncio.sleep(1)
            if time.monotonic() - self._last_server_contact > HEARTBEAT_TIMEOUT_SECONDS:
                print("Lost contact with server -- disabling")
                self._hardware.disable()

    def _handle_message(self, message):
        msg_type = message.get("type")
        if msg_type == "phase_change":
            phase = message.get("phase")
            if phase == "autonomous":
                self._hardware.enable_autonomous()
            elif phase == "driver":
                self._hardware.enable_driver()
            else:
                # Any other phase (e.g. disabled, ended) is treated as disabled.
                self._hardware.disable()
        elif msg_type == "estop":
            print("E-stop:", message.get("reason"))
            self._hardware.disable()
        elif msg_type == "match_start":
            print("Match", message.get("match_id"), "starting")
        elif msg_type == "match_assign":
            print("Assigned match", message.get("match_id"), "on field", message.get("field"))
        elif msg_type == "heartbeat_ack":
            pass
        else:
            print("Unhandled message type:", msg_type)

