#!/bin/sh
# Installeert de Phonavigator-tray voor de huidige gebruiker (Debian/Ubuntu).
# Root is alleen nodig voor de udev-regel en de pakketten.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"

sudo apt-get install -y python3-pyside6.qtwidgets python3-bleak python3-evdev

# Ingelogde gebruiker mag /dev/uinput openen (zelfde aanpak als Steam Input).
RULE=/etc/udev/rules.d/70-phonavigator-uinput.rules
echo 'KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"' | sudo tee "$RULE" >/dev/null
sudo udevadm control --reload
sudo udevadm trigger --name-match=uinput

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
Comment=Telefoon als 3D-muis
Exec=$HOME/.local/bin/phonavigator --settings
Icon=phonavigator
Categories=Utility;
EOS
sed 's/ --settings//' ~/.local/share/applications/phonavigator.desktop > ~/.config/autostart/phonavigator.desktop

systemctl is-active --quiet spacenavd || echo "Let op: spacenavd draait niet (sudo apt install spacenavd)"
echo "Klaar. Start met: phonavigator --settings"
