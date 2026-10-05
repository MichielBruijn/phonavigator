"""Render tray/phonavigator/icon.svg to build/phonavigator.ico for the executable."""

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

here = os.path.dirname(os.path.abspath(__file__))
app = QGuiApplication(sys.argv)
img = QImage(256, 256, QImage.Format_ARGB32)
img.fill(Qt.transparent)
p = QPainter(img)
QSvgRenderer(os.path.join(here, "..", "tray", "phonavigator", "icon.svg")).render(p)
p.end()
os.makedirs(os.path.join(here, "build"), exist_ok=True)
if not img.save(os.path.join(here, "build", "phonavigator.ico")):
    sys.exit("could not write the icon")
