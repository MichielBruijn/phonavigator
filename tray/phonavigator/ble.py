"""BLE-verbinding met de telefoon (bleak: Linux, Windows en macOS).

Draait een eigen asyncio-loop in een thread; het nieuwste pakket wordt via
on_sample doorgegeven, statuswijzigingen via de Qt-signal `status`.
"""

import asyncio
import threading
import time

from bleak import BleakClient, BleakScanner
from PySide6.QtCore import QObject, Signal

from . import protocol


class BleLink(QObject):
    status = Signal(str)
    connected = Signal(bool)

    def __init__(self, on_sample):
        super().__init__()
        self._on_sample = on_sample
        self._loop = None
        self._stop = None

    def start(self):
        threading.Thread(target=self._run, name="ble", daemon=True).start()

    def stop(self):
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)

    def _run(self):
        asyncio.run(self._main())

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        while not self._stop.is_set():
            try:
                await self._session()
            except Exception as ex:  # bleak/BlueZ gooit van alles; gewoon opnieuw proberen
                self.status.emit(f"Fout: {ex}")
            self.connected.emit(False)
            if not self._stop.is_set():
                await asyncio.sleep(2)

    async def _session(self):
        self.status.emit("Zoeken naar telefoon…")
        dev = await BleakScanner.find_device_by_filter(
            lambda d, ad: protocol.SERVICE_UUID in (u.lower() for u in ad.service_uuids),
            timeout=10,
        )
        if dev is None:
            return
        self.status.emit(f"Verbinden met {dev.name or dev.address}…")
        gone = asyncio.Event()
        async with BleakClient(dev, disconnected_callback=lambda _c: gone.set()) as client:
            await client.start_notify(protocol.ORIENTATION_UUID, self._notify)
            self.status.emit(f"Verbonden met {dev.name or dev.address}")
            self.connected.emit(True)
            stop = asyncio.ensure_future(self._stop.wait())
            lost = asyncio.ensure_future(gone.wait())
            await asyncio.wait({stop, lost}, return_when=asyncio.FIRST_COMPLETED)
            stop.cancel()
            lost.cancel()
        self.status.emit("Verbinding verbroken")

    def _notify(self, _char, data: bytearray):
        pkt = protocol.parse(bytes(data))
        if pkt:
            self._on_sample(pkt[1], time.monotonic())
