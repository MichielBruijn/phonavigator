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


async def connected_phone():
    """A phone running Phonavigator that BlueZ is already connected to, or None.

    BlueZ often keeps a paired phone connected (the phone reconnects for audio, BlueZ keeps
    the LE link), and a connected device shows up in no scan. Asking BlueZ costs no radio
    time, so this can be polled freely.
    """
    if not sys.platform.startswith("linux"):
        return None
    from bleak.backends.device import BLEDevice
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    from . import protocol

    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    except OSError:
        return None
    try:
        reply = await bus.call(Message(
            destination="org.bluez", path="/",
            interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects",
        ))
    finally:
        bus.disconnect()
    if reply.message_type == MessageType.ERROR:
        return None
    for path, ifaces in reply.body[0].items():
        dev = ifaces.get("org.bluez.Device1")
        if not dev:
            continue
        props = {k: v.value for k, v in dev.items()}
        if props.get("Connected") and protocol.advertises(props.get("UUIDs", []), props.get("ServiceData", {})):
            return BLEDevice(props["Address"], props.get("Name"), {"path": path, "props": props})
    return None
