# Phonavigator

Een telefoon als 3D-muis voor arme mensen: telefoon rechtop op de laadpoortkant op het bureau,
in je hand. **Kantelen en draaien = model draaien** (rate-besturing, zoals een SpaceMouse);
scrollwiel blijft gewoon zoomen. Telefoon plat neerleggen = pauze. Scherm mag uit.

```
Android-app ──BLE GATT──▶ tray-app ──uinput──▶ spacenavd ──▶ FreeCAD / Blender / …
(quaternion, 50 Hz)       (kalibratie, deadzone,
                           curve, asmapping)
```

## Onderdelen

- `android/` — Kotlin-app, alleen platform-API's. Foreground-service leest
  `GAME_ROTATION_VECTOR` (gyro + accelerometer, geen magnetometer) en stuurt de quaternion als
  BLE-notify. Bewust dom: alle logica zit in de tray.
- `tray/` — Python + PySide6 + bleak (alle drie cross-platform). Doet kalibratie, deadzone,
  responscurve, as-toewijzing en drift-correctie, en schrijft naar een uitvoer-backend.
  Backends staan in `tray/phonavigator/outputs/`; nu alleen Linux (virtuele SpaceMouse Compact
  via uinput, die spacenavd vanzelf oppakt).
- `PROTOCOL.md` — het BLE-protocol, voor een eventuele iOS-zender.

## Installeren

**Telefoon:** APK installeren (alleen "onbekende bronnen toestaan", geen developer options),
openen, *Starten*, en eenmalig *Accubeperking uitzetten*.

**Ubuntu:** `./install-linux.sh` (pakketten, udev-regel voor `/dev/uinput`, starter en
autostart), daarna `phonavigator --settings`. spacenavd moet draaien.

## Gebruik

1. Telefoon rechtop in de hand, tray-app maakt zelf verbinding.
2. *Nulstand kalibreren*: 2 s later wordt de huidige stand de nul.
3. Per beweging instelbaar: doel-as, omdraaien, deadzone, hoek voor vol gas, curve.

Draaien om de staande as heeft geen absolute referentie: die nul wordt bij elke keer oppakken
opnieuw gezet en schuift binnen de deadzone langzaam mee om drift weg te werken.

## Bouwen

```sh
cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ./gradlew assembleRelease
cd tray && python3 -m unittest discover -s tests -t .
```

Release-signing gebruikt `~/.android-keystores/phonavigator-release.jks` als die bestaat.
