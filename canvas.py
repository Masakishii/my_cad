from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene
from PyQt6.QtGui import QPen, QColor, QPixmap
from PyQt6.QtCore import Qt

class CADCanvas(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.scene.setSceneRect(0, 0, 1200, 800)
        
        self.current_color = QColor(0, 0, 0)
        self.current_thickness = 2
        self.start_point = None

    def set_color(self, color):
        """選択された描画色を設定"""
        self.current_color = color

    def set_thickness(self, thickness):
        """選択された線の太さを設定"""
        self.current_thickness = thickness

    def set_background_image(self, file_path):
        pixmap = QPixmap(file_path)
        self.scene.addPixmap(pixmap)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_point = self.mapToScene(event.pos())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.start_point:
            end_point = self.mapToScene(event.pos())
            pen = QPen(self.current_color, self.current_thickness)
            self.scene.addLine(
                self.start_point.x(), self.start_point.y(),
                end_point.x(), end_point.y(), pen
            )
            self.start_point = None