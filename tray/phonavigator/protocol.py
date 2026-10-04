"""BLE-protocol, zie PROTOCOL.md. Moet gelijk blijven aan Protocol in de Android-app."""

import struct

SERVICE_UUID = "7f3a0001-5c1e-4b8e-9d2a-6e0f1c9b4a10"
ORIENTATION_UUID = "7f3a0002-5c1e-4b8e-9d2a-6e0f1c9b4a10"

_PACKET = struct.Struct("<H4fB")
FLAG_MAGNETOMETER = 0x01


def parse(data: bytes):
    """Geeft (seq, (x, y, z, w), flags) terug, of None bij een onbekend pakket."""
    if len(data) < _PACKET.size:
        return None
    seq, x, y, z, w, flags = _PACKET.unpack_from(data)
    return seq, (x, y, z, w), flags
