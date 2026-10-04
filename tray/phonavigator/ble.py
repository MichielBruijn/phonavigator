"""BLE link to the phone (bleak: Linux, Windows and macOS).

Runs its own asyncio loop in a thread; every packet is passed to on_sample,
status changes go out through the Qt signal `status`.
"""

import asyncio
import threading
import time

from bleak import BleakClient, BleakScanner
from PySide6.QtCore import QObject, Signal

from . import bluez, protocol

SILENT_AFTER = 2.0  # s without packets while connected = broken subscription
SILENCE_CHECK = 0.5  # s


class BleLink(QObject):
    status = Signal(str)
    connected = Signal(bool)

    def __init__(self, on_sample):
        super().__init__()
        self._on_sample = on_sample
        self._loop = None
        self._stop = None
        self._wake = None
        self._last_packet = 0.0
        self._found = False

    def start(self):
        threading.Thread(target=self._run, name="ble", daemon=True).start()

    def stop(self):
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)

    def rescan(self):
        """Cut the wait short and scan right away."""
        if self._loop and self._wake:
            self._loop.call_soon_threadsafe(self._wake.set)

    def _run(self):
        asyncio.run(self._main())

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        idle = 0
        while not self._stop.is_set():
            self._found = False
            try:
                await self._session()
            except Exception as ex:  # bleak/BlueZ raise all sorts of things; just retry
                self.status.emit(f"Error: {ex}")
            found = self._found
            self.connected.emit(False)
            # Continuous scanning makes Bluetooth mice stutter: back off between fruitless scans.
            idle = 0 if found else idle + 1
            pause = 1 if found else min(30, 5 * idle)
            if not found:
                self.status.emit(f"Phone not found, retrying in {pause} s")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), pause)
            except asyncio.TimeoutError:
                pass

    async def _session(self):
        self.status.emit("Looking for phone…")
        dev = await BleakScanner.find_device_by_filter(
            lambda d, ad: protocol.advertises(ad.service_uuids, ad.service_data),
            timeout=10,
        )
        if dev is None:
            return
        self._found = True
        warning = await bluez.prefer_le(dev)
        self.status.emit(f"Connecting to {dev.name or dev.address}…")
        gone = asyncio.Event()
        async with BleakClient(dev, disconnected_callback=lambda _c: gone.set()) as client:
            if client.services.get_characteristic(protocol.ORIENTATION_UUID) is None:
                raise RuntimeError(warning or "Phonavigator service not found on phone")
            self._last_packet = time.monotonic()
            await client.start_notify(protocol.ORIENTATION_UUID, self._notify)
            self.status.emit(f"Connected to {dev.name or dev.address}")
            self.connected.emit(True)
            await self._watch(client, gone)
        self.status.emit("Connection lost")

    async def _watch(self, client, gone):
        """Stay connected until stopped or disconnected; repair a silent subscription.

        When the phone app restarts while the link stays up, the new app instance has no
        subscribers, but BlueZ keeps its CCCD state for a paired phone and thinks it is still
        subscribed. Data then simply stops: subscribe again, and reconnect if that fails.
        """
        resubscribed = False
        while not (self._stop.is_set() or gone.is_set()):
            try:
                await asyncio.wait_for(gone.wait(), SILENCE_CHECK)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set() or gone.is_set():
                return
            if time.monotonic() - self._last_packet < SILENT_AFTER:
                resubscribed = False
                continue
            if resubscribed:
                self.status.emit("Phone sends no data, reconnecting")
                return  # leaving the async-with disconnects
            self.status.emit("Phone sends no data, subscribing again")
            try:
                await client.stop_notify(protocol.ORIENTATION_UUID)
            except Exception:
                pass
            await client.start_notify(protocol.ORIENTATION_UUID, self._notify)
            self._last_packet = time.monotonic()
            resubscribed = True

    def _notify(self, _char, data: bytearray):
        pkt = protocol.parse(bytes(data))
        if pkt:
            self._last_packet = time.monotonic()
            self._on_sample(pkt[1], pkt[3], time.monotonic())
