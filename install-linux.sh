#!/bin/sh
# Installs the Phonavigator tray for the current user (Debian/Ubuntu).
# Root is only needed for packages, the udev rule and the BlueZ setting.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"

sudo apt-get install -y python3-pyside6.qtwidgets python3-bleak python3-evdev spacenavd

# Let the logged-in user open /dev/uinput (same approach as Steam Input).
RULE=/etc/udev/rules.d/70-phonavigator-uinput.rules
echo 'KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"' | sudo tee "$RULE" >/dev/null
sudo udevadm control --reload
sudo udevadm trigger --name-match=uinput

# A phone that is also paired over classic Bluetooth is otherwise connected over BR/EDR,
# where the GATT service is missing; the tray then sets PreferredBearer=le, which BlueZ
# only exposes with Experimental enabled.
CONF=/etc/bluetooth/main.conf
if [ -f "$CONF" ] && ! grep -q '^Experimental *= *true' "$CONF"; then
    sudo cp -a "$CONF" "$CONF.bak-$(date +%Y%m%d-%H%M%S)"
    if grep -q '^#\?Experimental *=' "$CONF"; then
        sudo sed -i 's/^#\?Experimental *=.*/Experimental = true/' "$CONF"
    else
        sudo sed -i 's/^\[General\]/[General]\nExperimental = true/' "$CONF"
    fi
    echo "Restarting bluetooth (Bluetooth devices reconnect in a few seconds)"
    sudo systemctl restart bluetooth
fi

mkdir -p ~/.local/bin ~/.local/share/applications ~/.local/share/icons/hicolor/scalable/apps ~/.config/autostart
cat > ~/.local/bin/phonavigator <<EOS
#!/bin/sh
PYTHONPATH="$HERE/tray\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m phonavigator "\$@"
EOS
chmod +x ~/.local/bin/phonavigator
cp "$HERE/tray/phonavigator/icon.svg" ~/.local/share/icons/hicolor/scalable/apps/phonavigator.svg

cat > ~/.local/share/applications/phonavigator.desktop <<EOS
[Desktop Entry]
Type=Application
Name=Phonavigator
Comment=Phone as a 3D mouse
Exec=$HOME/.local/bin/phonavigator --settings
Icon=phonavigator
Categories=Utility;
EOS
sed 's/ --settings//' ~/.local/share/applications/phonavigator.desktop > ~/.config/autostart/phonavigator.desktop

systemctl is-active --quiet spacenavd || echo "Note: spacenavd is not running (sudo apt install spacenavd)"
echo "Done. Start with: phonavigator --settings"
