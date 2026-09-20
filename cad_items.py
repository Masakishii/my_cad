import math
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsItem
from PyQt6.QtGui import QPen, QColor, QBrush
from PyQt6.QtCore import Qt, QPointF, QRectF

class CustomPixmapItem(QGraphicsPixmapItem):
    """アフィン変換（QTransform）対応の完全同期型 PixmapItem"""
    def __init__(self, pixmap, parent=None):
        super().__init__(pixmap, parent)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.handle_size = 14.0
        self.active_handle = None
        self.setTransformOriginPoint(0, 0)

    def setPixmap(self, pixmap):
        super().setPixmap(pixmap)
        self.setTransformOriginPoint(0, 0)

    def boundingRect(self):
        rect = super().boundingRect()
        margin = self.handle_size + 30.0
        return rect.adjusted(-margin, -margin, margin, margin)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.save()
            rect = super().boundingRect()
            sc = max(0.001, self.scale())
            
            pen = QPen(QColor(0, 120, 215), 2.0 / sc, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            s = self.handle_size / sc
            painter.setBrush(QBrush(QColor(0, 120, 215)))
            painter.setPen(QPen(QColor(255, 255, 255), 1.0 / sc))

            for h_rect in self._get_handle_rects(rect, s).values():
                painter.drawRect(h_rect)

            rot_pt = QPointF(rect.center().x(), rect.top() - s * 2.5)
            painter.drawLine(QPointF(rect.center().x(), rect.top()), rot_pt)
            painter.drawEllipse(rot_pt, s / 2.0, s / 2.0)
            painter.restore()

    def _get_handle_rects(self, rect, s):
        return {
            "TL": QRectF(rect.left() - s/2, rect.top() - s/2, s, s),
            "TR": QRectF(rect.right() - s/2, rect.top() - s/2, s, s),
            "BL": QRectF(rect.left() - s/2, rect.bottom() - s/2, s, s),
            "BR": QRectF(rect.right() - s/2, rect.bottom() - s/2, s, s),
        }

    def _get_rotation_handle_pos(self, rect, s):
        return QPointF(rect.center().x(), rect.top() - s * 2.5)

    def hoverMoveEvent(self, event):
        if self.isSelected():
            rect = super().boundingRect()
            s = self.handle_size / max(0.001, self.scale())
            rot_pos = self._get_rotation_handle_pos(rect, s)
            pos = event.pos()

            if math.hypot(pos.x() - rot_pos.x(), pos.y() - rot_pos.y()) <= s:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                return
            for h_rect in self._get_handle_rects(rect, s).values():
                if h_rect.contains(pos):
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    return
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if self.isSelected() and event.button() == Qt.MouseButton.LeftButton:
            rect = super().boundingRect()
            s = self.handle_size / max(0.001, self.scale())
            handles = self._get_handle_rects(rect, s)
            rot_pos = self._get_rotation_handle_pos(rect, s)
            pos = event.pos()

            if math.hypot(pos.x() - rot_pos.x(), pos.y() - rot_pos.y()) <= s:
                self.active_handle = "ROTATE"
                event.accept()
                return

            for h_name, h_rect in handles.items():
                if h_rect.contains(pos):
                    self.active_handle = h_name
                    self.initial_scale = self.scale()
                    self.center_scene_pos = self.scenePos()
                    self.initial_dist = math.hypot(
                        event.scenePos().x() - self.center_scene_pos.x(),
                        event.scenePos().y() - self.center_scene_pos.y()
                    )
                    event.accept()
                    return

        self.active_handle = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.active_handle == "ROTATE":
            center_scene = self.mapToScene(super().boundingRect().center())
            curr_pos = event.scenePos()
            angle = math.degrees(math.atan2(curr_pos.y() - center_scene.y(), curr_pos.x() - center_scene.x())) + 90.0
            self.setRotation(angle)
            return

        elif self.active_handle in ["TL", "TR", "BL", "BR"]:
            curr_dist = math.hypot(event.scenePos().x() - self.center_scene_pos.x(), event.scenePos().y() - self.center_scene_pos.y())
            if getattr(self, "initial_dist", 0) > 0:
                self.setScale(max(0.01, self.initial_scale * (curr_dist / self.initial_dist)))
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.active_handle = None
        super().mouseReleaseEvent(event)