"""BLE-protocol, zie PROTOCOL.md. Moet gelijk blijven aan Protocol in de Android-app."""

import struct

SERVICE_UUID = "7f3a0001-5c1e-4b8e-9d2a-6e0f1c9b4a10"
ORIENTATION_UUID = "7f3a0002-5c1e-4b8e-9d2a-6e0f1c9b4a10"

_PACKET = struct.Struct("<H4fB")
FLAG_MAGNETOMETER = 0x01


def advertises(service_uuids, service_data) -> bool:
    """Herkent een Phonavigator-advertentie.

    Op service data matchen is nodig: BlueZ negeert geadverteerde UUID's van een
    gekoppeld apparaat waarvan de services al bekend zijn.
    """
    return SERVICE_UUID in (u.lower() for u in service_uuids) or SERVICE_UUID in (
        k.lower() for k in service_data)


def parse(data: bytes):
    """Geeft (seq, (x, y, z, w), flags) terug, of None bij een onbekend pakket."""
    if len(data) < _PACKET.size:
        return None
    seq, x, y, z, w, flags = _PACKET.unpack_from(data)
    return seq, (x, y, z, w), flags
