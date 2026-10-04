"""BlueZ quirk: a phone that is also paired over classic Bluetooth gets connected over
BR/EDR, where our GATT service is not available. Ask BlueZ to use LE instead.

Device1.PreferredBearer is only exposed with `Experimental = true` in
/etc/bluetooth/main.conf (install-linux.sh sets that).
"""

import sys


async def prefer_le(device) -> str | None:
    """Returns a warning for the user, or None if all is well (or not applicable)."""
    if not sys.platform.startswith("linux"):
        return None
    details = getattr(device, "details", None)
    path = details.get("path") if isinstance(details, dict) else None
    props = details.get("props", {}) if isinstance(details, dict) else {}
    if not path or not props.get("Paired"):
        return None

    from dbus_fast import BusType, Message, MessageType, Variant
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(Message(
            destination="org.bluez", path=path,
            interface="org.freedesktop.DBus.Properties", member="Set", signature="ssv",
            body=["org.bluez.Device1", "PreferredBearer", Variant("s", "le")],
        ))
    finally:
        bus.disconnect()
    if reply.message_type == MessageType.ERROR:
        return ("Phone is paired with this computer: set 'Experimental = true' in "
                "/etc/bluetooth/main.conf (or unpair the phone)")
    return None
