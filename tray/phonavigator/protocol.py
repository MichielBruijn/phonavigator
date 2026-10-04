"""BLE protocol, see PROTOCOL.md. Must match Protocol in the Android app."""

import struct

SERVICE_UUID = "7f3a0001-5c1e-4b8e-9d2a-6e0f1c9b4a10"
ORIENTATION_UUID = "7f3a0002-5c1e-4b8e-9d2a-6e0f1c9b4a10"

_V1 = struct.Struct("<H4fB")
_V2 = struct.Struct("<H4fBB")
FLAG_MAGNETOMETER = 0x01
PEAK_UNIT = 0.5  # m/s² per step of the peak byte


def advertises(service_uuids, service_data) -> bool:
    """Recognises a Phonavigator advertisement.

    Matching on service data is required: BlueZ ignores advertised UUIDs of a
    paired device whose services it already knows.
    """
    return SERVICE_UUID in (u.lower() for u in service_uuids) or SERVICE_UUID in (
        k.lower() for k in service_data)


def parse(data: bytes):
    """Returns (seq, (x, y, z, w), flags, peak_accel m/s²) or None for an unknown packet."""
    if len(data) >= _V2.size:
        seq, x, y, z, w, flags, peak = _V2.unpack_from(data)
        return seq, (x, y, z, w), flags, peak * PEAK_UNIT
    if len(data) >= _V1.size:
        seq, x, y, z, w, flags = _V1.unpack_from(data)
        return seq, (x, y, z, w), flags, 0.0
    return None
