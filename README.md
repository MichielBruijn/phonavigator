# Phonavigator

A poor man's 3D mouse: your phone, standing upright on its charging-port edge on the desk, in
your hand. **Tilt and twist to rotate the model** (rate control, like a SpaceMouse); the scroll
wheel keeps zooming. Lay the phone down to pause; optionally, double-tap it on the desk to make the
current position neutral. The screen may be off.

```
Android app ──BLE GATT──▶ tray app ──uinput──▶ spacenavd ──▶ FreeCAD / Blender / TopSolid (Wine) / …   Linux
(quaternion, 50 Hz)       (calibration,  ─pipe──▶ TDxNavLib.dll in FreeCAD / TopSolid / …            Windows
                           deadzone, curve, axis mapping, taps)
```

## Parts

- `android/` — Kotlin app, platform APIs only. A foreground service reads
  `GAME_ROTATION_VECTOR` (gyro + accelerometer, no magnetometer) and the accelerometer peak, and
  sends them as BLE notifications. Deliberately dumb: all logic lives in the tray.
- `tray/` — Python + PySide6 + bleak (all three cross-platform). Calibration, deadzone, response
  curve, axis mapping, drift correction and tap detection, writing to an output backend.
  Backends live in `tray/phonavigator/outputs/`: Linux (a virtual SpaceMouse Compact via
  uinput, which spacenavd picks up by itself) and Windows (a named pipe, see below).
- `windows/` — Windows build: `navlib/` is a replacement for 3Dconnexion's navigation library
  (`TDxNavLib.dll`, the interface FreeCAD, TopSolid and others use) that reads the tray's pipe and
  moves the application's camera; plus the PyInstaller/Inno Setup scripts.
- `PROTOCOL.md` — the BLE protocol, for e.g. an iOS sender.

## Install

**Phone:** install the APK (only "allow unknown apps" is needed, no developer options; Play
Protect may want *Install anyway*), open it, *Start*, and once *Disable battery optimization*.

**Ubuntu:** `./install-linux.sh` (packages, udev rule for `/dev/uinput`, BlueZ setting,
launcher and autostart), then `phonavigator --settings`.

**Windows:** run `Phonavigator-x.y.z-setup.exe`. It installs the tray (with *Start at login*)
and puts its `TDxNavLib.dll` in System32/SysWOW64, where applications look for 3Dconnexion's.
Because of that it cannot be installed next to 3Dconnexion's 3DxWare driver. Navigation speed
and axes of the dll: `HKEY_CURRENT_USER\Software\Phonavigator\NavLib` (`TranslationSpeed`,
`RotationSpeed`, `AxisMap`, `AxisSigns`, strings).

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

Both sides start by themselves: the tray at login (*Start at login* in the settings), the phone
app when the phone starts (*Start when the phone starts* in the app). Both are on by default.
While no computer is connected the phone only advertises; sensors and wake lock run only while
streaming.

Charging: hold the phone upside down (charging port up, screen facing the same way) and it feels
the same. *Charging port* in the settings: *Auto* (default, decided each time the phone is picked
up), *Down* or *Up (charging)*.

The defaults assume the phone in the left hand with the screen facing right, and a Z-up CAD
program: sideways tilt rotates about X, forward/back tilt about Y.

Axis options in `/etc/spnavrc` (`swap-yz`, `invert-rot`, `invert-trans`, `axismapN`) are undone
by the tray, so the same settings behave the same on every machine; spnavrc can stay tuned for a
real SpaceMouse. Sensitivity and dead zone from spnavrc still apply.

A real SpaceMouse keeps working next to the phone. spacenavd 1.3 only passes on the motion of
its newest device, so the tray reads real SpaceMice itself and merges them into its own
virtual one (which it recreates when you plug one in, to stay the newest). It reads them
through evdev, or through hidraw when spacenavd grabs them (`grab = true`, the default); the
installer gives access to both. Model-specific button remapping of spacenavd (SpaceMouse
Pro, Enterprise) is lost while the tray runs.

Twisting about the vertical axis has no absolute reference: its zero is reset every time the
phone is picked up and slowly follows the phone inside the deadzone to remove drift.

## Build

```sh
cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ./gradlew assembleRelease
cd tray && python3 -m unittest discover -s tests -t .
```

Windows: `make -C windows/navlib` (MinGW-w64, on Linux) builds the dll; then, on Windows with
Python 3.12+ and Inno Setup 6, `windows\build.ps1` builds the installer.

Release signing uses `~/.android-keystores/phonavigator-release.jks` when it exists.

## License

MIT, see [LICENSE](LICENSE).
