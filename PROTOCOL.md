# Phonavigator BLE protocol (v2)

The phone is the GATT **server** (peripheral); the computer is the central and
subscribes to notifications.

| | UUID |
|---|---|
| Service | `7f3a0001-5c1e-4b8e-9d2a-6e0f1c9b4a10` |
| Orientation (notify, read) | `7f3a0002-5c1e-4b8e-9d2a-6e0f1c9b4a10` |

Advertisement: **service data** under the service UUID (1 byte: protocol
version, currently 2); the scan response carries the service UUID as a UUID
list. Look for both: BlueZ ignores advertised UUID lists of a paired device
whose services it already knows, but always updates service data.

## Orientation packet — 20 bytes, little-endian

| Offset | Type | Field |
|---|---|---|
| 0 | u16 | sequence number (wraps) |
| 2 | f32 ×4 | quaternion x, y, z, w |
| 18 | u8 | flags: bit 0 = magnetometer used |
| 19 | u8 | peak high-pass acceleration since the previous packet, 0.5 m/s² per step (tap detection) |

v1 packets (19 bytes, no peak byte) are still accepted.

The quaternion rotates phone coordinates into world coordinates (Android
`GAME_ROTATION_VECTOR` convention): phone X to the right, Y up along the screen,
Z out of the screen; world Z up. Yaw has an arbitrary reference and may drift.

The orientation is a **state**, not an event: only the newest one matters and
missed packets do no harm. The peak byte is accumulated on the phone until a
packet is actually sent, so a short tap is never lost. The sender sends at
~50 Hz; a packet fits the default ATT MTU, so no MTU negotiation is needed.

Another sender (e.g. iOS) only has to implement this.
