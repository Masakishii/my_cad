
import math
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtCore import Qt, pyqtSignal

print(" -> canvas.py の読み込み成功")

class CADCanvas(QGraphicsView):
    mode_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.scene.setSceneRect(0, 0, 1200, 800)
        self.mode = "SELECT"
        self.current_color = QColor(0, 0, 0)
        self.current_thickness = 2
        self.current_style = Qt.PenStyle.SolidLine
        self.layers = {"0": {"color": QColor(0, 0, 0), "thickness": 2, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True}}
        self.active_layer = "0"
        self.shapes = []

    def set_mode(self, mode):
        self.mode = mode
        self.mode_changed.emit(mode)

    def zoom_in(self): self.scale(1.15, 1.15)
    def zoom_out(self): self.scale(1/1.15, 1/1.15)
    def zoom_fit(self): self.resetTransform()
    def load_project_json(self): pass
    def save_project_json(self): pass
    def save_to_dxf(self): pass