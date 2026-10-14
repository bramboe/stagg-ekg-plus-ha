"""Session-local Bricky intent; the firmware does not expose a game-state bit."""
from __future__ import annotations

import asyncio

from .protocol import UnsupportedCapability


class BrickySession:
    def __init__(self, kettle, update):
        self.kettle = kettle
        self.update = update
        self.state = "unknown"
        self.busy = False
        self.generation = None
        self.receipt = None

    def set_state(self, state):
        self.state = state
        self.update()

    def observe(self, data):
        if self.busy or self.state != "assumed_active":
            return
        if data.get("lifted") is False:
            self.state = "assumed_inactive"
        elif self.kettle.ble._generation != self.generation:
            self.state = "unknown"

    async def run(self, action):
        if self.busy:
            raise UnsupportedCapability("A Bricky action is already running")
        if action not in ("start", "exit", "left", "right", "refresh"):
            raise ValueError("Unknown Bricky action")
        if action in ("exit", "left", "right") and self.state != "assumed_active":
            raise UnsupportedCapability("Start Bricky through this integration first; its screen cannot be read back")
        if action == "start" and self.state == "assumed_active":
            raise UnsupportedCapability("Bricky was already started in this session")
        self.busy = True
        self.receipt = None
        try:
            if action in ("start", "exit"):
                self.set_state("starting" if action == "start" else "stopping")
                method = self.kettle.async_launch_bricky if action == "start" else self.kettle.async_exit_bricky
                self.receipt = await method()
                self.set_state("reconnecting")
                status = await self.kettle.async_reconnect_bricky()
                self.generation = self.kettle.ble._generation
                self.set_state("assumed_active" if action == "start" and status.get("lifted") is True else "assumed_inactive")
            else:
                self.update()
                self.receipt = await self.kettle.async_bricky_input(action)
        except (Exception, asyncio.CancelledError):
            self.set_state("uncertain")
            raise
        finally:
            self.busy = False
            self.update()
