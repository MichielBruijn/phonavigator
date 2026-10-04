# Phonavigator

A poor man's 3D mouse: your phone, standing upright on its charging-port edge on the desk, in
your hand. **Tilt and twist to rotate the model** (rate control, like a SpaceMouse); the scroll
wheel keeps zooming. Lay the phone down to pause; optionally, double-tap it on the desk to make the
current position neutral. The screen may be off.

```
Android app ──BLE GATT──▶ tray app ──uinput──▶ spacenavd ──▶ FreeCAD / Blender / TopSolid (Wine) / …
(quaternion, 50 Hz)       (calibration, deadzone,
                           curve, axis mapping, taps)
```

## Parts

- `android/` — Kotlin app, platform APIs only. A foreground service reads
  `GAME_ROTATION_VECTOR` (gyro + accelerometer, no magnetometer) and the accelerometer peak, and
  sends them as BLE notifications. Deliberately dumb: all logic lives in the tray.
- `tray/` — Python + PySide6 + bleak (all three cross-platform). Calibration, deadzone, response
  curve, axis mapping, drift correction and tap detection, writing to an output backend.
  Backends live in `tray/phonavigator/outputs/`; for now only Linux (a virtual SpaceMouse Compact
  via uinput, which spacenavd picks up by itself).
- `PROTOCOL.md` — the BLE protocol, for e.g. an iOS sender.

## Install

**Phone:** install the APK (only "allow unknown apps" is needed, no developer options; Play
Protect may want *Install anyway*), open it, *Start*, and once *Disable battery optimization*.

**Ubuntu:** `./install-linux.sh` (packages, udev rule for `/dev/uinput`, BlueZ setting,
launcher and autostart), then `phonavigator --settings`.

If the phone is also paired with the computer over classic Bluetooth, BlueZ would connect over
BR/EDR, where the service is missing. The tray then asks BlueZ for LE (`PreferredBearer`), which
needs `Experimental = true` in `/etc/bluetooth/main.conf`; the install script sets it (with a
backup) and restarts Bluetooth.

## Use

1. Hold the phone upright; the tray connects by itself.
2. *Calibrate neutral position* (tray menu or settings), or, when enabled in the settings,
   double-tap the phone on the desk.
3. Per movement: target axis, invert, deadzone, angle for full speed, curve. Changes apply
   immediately.

The defaults assume the phone in the left hand with the screen facing right, and a Z-up CAD
program: sideways tilt rotates about X, forward/back tilt about Y.

Twisting about the vertical axis has no absolute reference: its zero is reset every time the
phone is picked up and slowly follows the phone inside the deadzone to remove drift.

## Build

```sh
cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ./gradlew assembleRelease
cd tray && python3 -m unittest discover -s tests -t .
```

Release signing uses `~/.android-keystores/phonavigator-release.jks` when it exists.
