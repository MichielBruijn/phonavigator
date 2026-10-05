#!/bin/sh
# Builds build/phonavigator_<version>_all.deb (the tray for Debian/Ubuntu).
set -e
umask 022
HERE="$(cd "$(dirname "$0")/.." && pwd)"
VER=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$HERE/tray/phonavigator/__init__.py")
ROOT="$HERE/build/deb/phonavigator_${VER}_all"
rm -rf "$ROOT"
mkdir -p "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT/usr/lib/python3/dist-packages" \
    "$ROOT/usr/share/applications" "$ROOT/usr/share/icons/hicolor/scalable/apps" \
    "$ROOT/usr/lib/udev/rules.d" \
    "$ROOT/usr/share/doc/phonavigator"

(cd "$HERE/tray" && find phonavigator -name '*.py' -o -name '*.svg') | while read -r f; do
    install -D -m 644 "$HERE/tray/$f" "$ROOT/usr/lib/python3/dist-packages/$f"
done
printf '#!/bin/sh\nexec python3 -m phonavigator "$@"\n' > "$ROOT/usr/bin/phonavigator"
chmod 755 "$ROOT/usr/bin/phonavigator"
install -m 644 "$HERE/tray/phonavigator/icon.svg" "$ROOT/usr/share/icons/hicolor/scalable/apps/phonavigator.svg"
cat > "$ROOT/usr/share/applications/phonavigator.desktop" <<EOS
[Desktop Entry]
Type=Application
Name=Phonavigator
Comment=Phone as a 3D mouse
Exec=phonavigator --settings
Icon=phonavigator
Categories=Utility;
StartupNotify=false
EOS

# Let the logged-in user open /dev/uinput (as Steam Input does), and the hidraw nodes of real
# SpaceMice, read when spacenavd grabs their input device.
echo 'KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"' \
    > "$ROOT/usr/lib/udev/rules.d/70-phonavigator-uinput.rules"
cat > "$ROOT/usr/lib/udev/rules.d/70-phonavigator-spacemouse.rules" <<'EOS'
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="256f", TAG+="uaccess"
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="046d", ATTRS{idProduct}=="c6[0-2]?", TAG+="uaccess"
EOS
install -m 644 "$HERE/README.md" "$ROOT/usr/share/doc/phonavigator/README.md"
install -m 644 "$HERE/LICENSE" "$ROOT/usr/share/doc/phonavigator/copyright"

cat > "$ROOT/DEBIAN/control" <<EOS
Package: phonavigator
Version: $VER
Architecture: all
Maintainer: Michiel Bruijn <michiel@bruijn.nu>
Depends: python3 (>= 3.10), python3-pyside6.qtwidgets, python3-pyside6.qtnetwork, python3-bleak, python3-evdev, bluez
Recommends: spacenavd
Section: utils
Priority: optional
Homepage: https://github.com/MichielBruijn/phonavigator
Description: phone as a 3D mouse (SpaceMouse) for CAD
 Tray app that receives the orientation of a phone running the Phonavigator
 Android app over Bluetooth LE and turns it into a virtual SpaceMouse for
 spacenavd (FreeCAD, Blender, TopSolid under Wine, ...).
EOS
cat > "$ROOT/DEBIAN/postinst" <<'EOS'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    udevadm control --reload || true
    udevadm trigger --name-match=uinput || true
    udevadm trigger --subsystem-match=hidraw || true
    # BlueZ experimental interfaces (PreferredBearer, see README): a drop-in that adds
    # --experimental to whatever ExecStart this distribution uses, unless main.conf has it.
    DROPIN=/etc/systemd/system/bluetooth.service.d/phonavigator.conf
    if [ ! -e "$DROPIN" ] && ! grep -qs '^Experimental *= *true' /etc/bluetooth/main.conf; then
        EXEC=$(systemctl cat bluetooth.service 2>/dev/null | sed -n 's/^ExecStart=\(.*bluetoothd.*\)/\1/p' | tail -n 1)
        if [ -n "$EXEC" ]; then
            mkdir -p "${DROPIN%/*}"
            printf '[Service]\nExecStart=\nExecStart=%s --experimental\n' "$EXEC" > "$DROPIN"
            systemctl daemon-reload || true
            if systemctl is-active --quiet bluetooth; then
                echo "Restarting bluetooth for experimental mode (devices reconnect in a few seconds)"
                systemctl restart bluetooth || true
            fi
        fi
    fi
fi
EOS
cat > "$ROOT/DEBIAN/postrm" <<'EOS'
#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = purge ]; then
    rm -f /etc/systemd/system/bluetooth.service.d/phonavigator.conf
    rmdir --ignore-fail-on-non-empty /etc/systemd/system/bluetooth.service.d 2>/dev/null || true
    systemctl daemon-reload || true
    udevadm control --reload || true
fi
EOS
chmod 755 "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/postrm"
dpkg-deb --root-owner-group --build "$ROOT" "$HERE/build/phonavigator_${VER}_all.deb"
