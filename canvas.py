import os
import math
import json
import pymupdf
import ezdxf
import copy
from shapely.geometry import LineString, Point, Polygon, MultiPoint, GeometryCollection, MultiPolygon
from shapely.ops import split, snap, unary_union, polygonize

from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QInputDialog, QMessageBox, 
                             QFileDialog, QGraphicsItem, QGraphicsEllipseItem, 
                             QGraphicsLineItem, QGraphicsRectItem, QGraphicsPolygonItem, 
                             QGraphicsPathItem, QGraphicsTextItem, QGraphicsPixmapItem, 
                             QGraphicsItemGroup, QApplication, QMenu, QDialog,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QComboBox, QDoubleSpinBox, QSpinBox, QTextEdit, QColorDialog)
from PyQt6.QtGui import (QPen, QColor, QPixmap, QPolygonF, QBrush, QFont, QImage, 
                         QPainterPath, QPainter, QPageSize, QPageLayout, QTransform)
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal


def _clean_for_json(obj):
    """JSONエンコード可能な型へ安全にシリアライズ"""
    if isinstance(obj, QColor):
        return obj.name()
    elif hasattr(obj, "value"):
        return obj.value
    elif isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items() if k not in ("item", "head_items")}
    elif isinstance(obj, (list, tuple)):
        return [_clean_for_json(i) for i in obj]
    return obj


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


class TextEditDialog(QDialog):
    """文字編集ダイアログ"""
    def __init__(self, parent=None, text="", font_size=12, color=None):
        super().__init__(parent)
        self.setWindowTitle("文章の入力・編集")
        self.resize(420, 350)
        self.selected_color = QColor(color) if color else QColor(255, 0, 0)

        layout = QVBoxLayout(self)
        cfg_layout = QHBoxLayout()
        cfg_layout.addWidget(QLabel("文字サイズ(pt):"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 500)
        self.size_spin.setValue(int(font_size))
        cfg_layout.addWidget(self.size_spin)

        cfg_layout.addWidget(QLabel("文字色:"))
        self.color_btn = QPushButton(" 色を選択 ")
        self.update_color_button_style()
        self.color_btn.clicked.connect(self.choose_color)
        cfg_layout.addWidget(self.color_btn)
        cfg_layout.addStretch()
        layout.addLayout(cfg_layout)

        layout.addWidget(QLabel("テキスト:"))
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(text)
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def choose_color(self):
        col = QColorDialog.getColor(self.selected_color, self, "文字色の選択")
        if col.isValid():
            self.selected_color = col
            self.update_color_button_style()

    def update_color_button_style(self):
        txt_col = "#000000" if (self.selected_color.red()*0.299 + self.selected_color.green()*0.587 + self.selected_color.blue()*0.114) > 180 else "#FFFFFF"
        self.color_btn.setStyleSheet(f"background-color: {self.selected_color.name()}; color: {txt_col}; font-weight: bold; border: 1px solid #888;")

    def get_result(self):
        return self.text_edit.toPlainText(), self.size_spin.value(), self.selected_color


class TableEditDialog(QDialog):
    """表データ編集ダイアログ"""
    def __init__(self, parent=None, grid_data=None, cell_w=100, cell_h=30, align="CENTER", font_size=12, color=None):
        super().__init__(parent)
        self.setWindowTitle("表データの再編集")
        self.resize(650, 480)
        self.grid_data = copy.deepcopy(grid_data) if grid_data else [[""]]
        self.selected_color = QColor(color) if color else QColor(0, 0, 0)

        main_layout = QVBoxLayout(self)
        cfg_layout = QHBoxLayout()
        self.w_spin = QDoubleSpinBox()
        self.w_spin.setRange(10.0, 2000.0); self.w_spin.setValue(float(cell_w))
        cfg_layout.addWidget(QLabel("セル幅:"))
        cfg_layout.addWidget(self.w_spin)

        self.h_spin = QDoubleSpinBox()
        self.h_spin.setRange(5.0, 1000.0); self.h_spin.setValue(float(cell_h))
        cfg_layout.addWidget(QLabel("セル高:"))
        cfg_layout.addWidget(self.h_spin)

        self.font_spin = QSpinBox()
        self.font_spin.setRange(6, 200); self.font_spin.setValue(int(font_size))
        cfg_layout.addWidget(QLabel("文字サイズ:"))
        cfg_layout.addWidget(self.font_spin)

        self.color_btn = QPushButton(" 色を選択 ")
        self.update_color_button_style()
        self.color_btn.clicked.connect(self.choose_color)
        cfg_layout.addWidget(self.color_btn)

        self.align_combo = QComboBox()
        self.align_combo.addItems(["中央 (CENTER)", "左寄せ (LEFT)", "右寄せ (RIGHT)"])
        align_map = {"CENTER": 0, "LEFT": 1, "RIGHT": 2}
        self.align_combo.setCurrentIndex(align_map.get(str(align).upper(), 0))
        cfg_layout.addWidget(self.align_combo)
        main_layout.addLayout(cfg_layout)

        btn_layout = QHBoxLayout()
        add_r = QPushButton("＋ 行追加"); add_r.clicked.connect(lambda: self.table_widget.insertRow(self.table_widget.rowCount()))
        del_r = QPushButton("－ 行削除"); del_r.clicked.connect(lambda: self.table_widget.removeRow(max(0, self.table_widget.currentRow())))
        add_c = QPushButton("＋ 列追加"); add_c.clicked.connect(lambda: self.table_widget.insertColumn(self.table_widget.columnCount()))
        del_c = QPushButton("－ 列削除"); del_c.clicked.connect(lambda: self.table_widget.removeColumn(max(0, self.table_widget.currentColumn())))
        btn_layout.addWidget(add_r); btn_layout.addWidget(del_r); btn_layout.addWidget(add_c); btn_layout.addWidget(del_c)
        main_layout.addLayout(btn_layout)

        self.table_widget = QTableWidget()
        self.populate_table()
        main_layout.addWidget(self.table_widget)

        dlg_btns = QHBoxLayout()
        ok_btn = QPushButton("OK"); ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("キャンセル"); cancel_btn.clicked.connect(self.reject)
        dlg_btns.addStretch(); dlg_btns.addWidget(ok_btn); dlg_btns.addWidget(cancel_btn)
        main_layout.addLayout(dlg_btns)

    def choose_color(self):
        col = QColorDialog.getColor(self.selected_color, self, "文字色の選択")
        if col.isValid():
            self.selected_color = col
            self.update_color_button_style()

    def update_color_button_style(self):
        txt_col = "#000000" if (self.selected_color.red()*0.299 + self.selected_color.green()*0.587 + self.selected_color.blue()*0.114) > 180 else "#FFFFFF"
        self.color_btn.setStyleSheet(f"background-color: {self.selected_color.name()}; color: {txt_col}; font-weight: bold; border: 1px solid #888;")

    def populate_table(self):
        rows, cols = len(self.grid_data), max(len(r) for r in self.grid_data) if self.grid_data else 1
        self.table_widget.setRowCount(rows); self.table_widget.setColumnCount(cols)
        for r in range(rows):
            for c in range(cols):
                val = self.grid_data[r][c] if c < len(self.grid_data[r]) else ""
                self.table_widget.setItem(r, c, QTableWidgetItem(str(val)))

    def get_result(self):
        rows, cols = self.table_widget.rowCount(), self.table_widget.columnCount()
        res_grid = [[self.table_widget.item(r, c).text() if self.table_widget.item(r, c) else "" for c in range(cols)] for r in range(rows)]
        align_list = ["CENTER", "LEFT", "RIGHT"]
        return res_grid, self.w_spin.value(), self.h_spin.value(), align_list[self.align_combo.currentIndex()], self.font_spin.value(), self.selected_color


class CADCanvas(QGraphicsView):
    """建築・施工用 朱書きCADシステム 統合キャンバスクラス"""
    mode_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.scene.setSceneRect(-100000, -100000, 200000, 200000)
        self.setBackgroundBrush(QBrush(QColor(255, 255, 255)))
        self.setAcceptDrops(True)
        
        self.layers = {
            "0": {"color": QColor(0, 0, 0), "thickness": 2, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True},
            "背景図面": {"color": QColor(120, 120, 120), "thickness": 1, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": True, "printable": True},
            "朱書き": {"color": QColor(255, 0, 0), "thickness": 3, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True},
            "寸法・文字": {"color": QColor(0, 120, 215), "thickness": 1, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True},
            "中心線": {"color": QColor(255, 100, 0), "thickness": 1, "style": Qt.PenStyle.DashDotLine, "visible": True, "locked": False, "printable": True},
            "下書き": {"color": QColor(128, 128, 128), "thickness": 1, "style": Qt.PenStyle.DashLine, "visible": True, "locked": False, "printable": False},
        }
        self.active_layer = "朱書き"

        active_props = self.layers[self.active_layer]
        self.current_color = active_props["color"]
        self.current_thickness = active_props["thickness"]
        self.current_style = active_props["style"]
        self.mode = "SELECT"
        self.scale_factor = 1.0
        self.is_dark_mode = False

        self.undo_stack, self.redo_stack, self.copied_shapes_buffer = [], [], []
        self._before_items = set()
        self._before_shapes_len = 0

        self.arrow_head_type, self.arrow_direction = "FILLED", "END"
        self.preset_line_length, self.offset_dist = 0.0, 20.0
        self.cloud_pitch, self.cloud_arc_height = 20.0, 8.0
        self.preset_rect_w, self.preset_rect_h = 100.0, 50.0
        self.preset_circle_r = 40.0
        self.preset_arc_r, self.preset_arc_start, self.preset_arc_span = 40.0, 0.0, 90.0
        self.preset_poly_sides, self.preset_poly_r, self.preset_poly_angle = 6, 40.0, 0.0
        self.rotate_angle, self.scale_factor_val = 45.0, 1.5
        self.array_rows, self.array_cols, self.array_row_gap, self.array_col_gap = 3, 3, 50.0, 50.0
        self.hatch_angle, self.hatch_spacing, self.hatch_color = 45.0, 15.0, QColor(255, 0, 0)
        self.hatch_thickness, self.hatch_style = 1, Qt.PenStyle.SolidLine

        self.show_paper_guide = False
        self.paper_size_id = QPageSize.PageSizeId.A4
        self.paper_orientation = QPageLayout.Orientation.Landscape
        self.paper_scale = 100
        self.paper_guide_item = None
        self.custom_print_rect_item = None

        self.otrack_enabled = True
        self.tracking_items = []
        self.grid_snap_enabled = False
        self.grid_size = 50.0
        self.snap_threshold = 15.0

        self.concentric_center = None
        self.break_first_pt = None
        self.break_target_shape = None
        self.calibrate_target_item = None
        self.angle_dim_first_line = None
        self.move_base_pt = None
        self.trim_preview_item = None

        self.blocks = {}
        self.init_preset_blocks()

        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.zoom_factor = 1.15
        self._is_panning = False
        self.angle_snap_enabled = True
        self.start_point = None
        self.temp_item = None
        self.click_points, self.poly_points, self.poly_temp_items = [], [], []
        self.snap_marker = None
        self.shapes = []

    def init_preset_blocks(self):
        self.blocks["方位記号 (N)"] = {
            "category": "記号・注釈", "base_pt": (0, 0),
            "shapes": [
                {"type": "circle", "center": (0, 0), "radius": 200, "color": QColor(0, 0, 0)},
                {"type": "polyline", "points": [(0, -200), (-50, 0), (0, 0)], "is_closed": True, "color": QColor(0, 0, 0)},
                {"type": "polyline", "points": [(0, -200), (50, 0), (0, 0)], "is_closed": True, "color": QColor(0, 0, 0)},
                {"type": "text", "text": "N", "pos": (-20, -350), "font_size": 20, "color": QColor(0, 0, 0)}
            ]
        }

    def _sync_item_transforms(self):
        """画面上で移動・変形されたQGraphicsItemの実際の座標・変形行列情報をshape辞書に同期"""
        for shape in self.shapes:
            item = shape.get("item")
            if not item: continue
            stype = shape.get("type")
            if stype in ["image", "text", "table", "block_ref", "point"]:
                spos = item.scenePos()
                shape["pos"] = (spos.x(), spos.y())
                t = item.transform()
                shape["transform"] = [t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy()]
                if hasattr(item, "scale"): shape["scale"] = item.scale()
                if hasattr(item, "rotation"): shape["rotation"] = item.rotation()
                if hasattr(item, "opacity"): shape["opacity"] = item.opacity()

    def safe_remove_item(self, item):
        """グラフィックス要素（単体およびリスト）を安全にシーンから削除"""
        if isinstance(item, list):
            for i in item:
                if i and i.scene() == self.scene:
                    self.scene.removeItem(i)
        elif item and item.scene() == self.scene:
            self.scene.removeItem(item)

    # --- 線の結合 & 中心線生成 (コンテキストメニュー用) ---
    def join_selected_lines(self):
        selected = self.scene.selectedItems()
        target_shapes = [s for s in self.shapes if s.get("item") in selected and s.get("type") in ["line", "polyline"]]
        if len(target_shapes) < 2:
            QMessageBox.warning(self, "通知", "結合するには2本以上の線分を選択してください。")
            return
        self.start_history_record()
        all_pts = []
        for s in target_shapes:
            if s["type"] == "line": all_pts.extend([s["p1"], s["p2"]])
            elif s["type"] == "polyline": all_pts.extend(s["points"])
            self.safe_remove_item(s.get("item"))
            if s in self.shapes: self.shapes.remove(s)
            
        if all_pts:
            self.scene.clearSelection()
            pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
            path = QPainterPath(); path.moveTo(QPointF(all_pts[0][0], all_pts[0][1]))
            for p in all_pts[1:]: path.lineTo(QPointF(p[0], p[1]))
            j_item = self.scene.addPath(path, pen)
            self.shapes.append({"type": "polyline", "points": all_pts, "is_closed": False, "layer": self.active_layer, "color": self.current_color, "item": j_item})
            self.commit_history_record()
            QMessageBox.information(self, "結合完了", f"{len(target_shapes)}本の線を結合しました。")

    def generate_centerlines(self):
        selected = self.scene.selectedItems()
        if not selected: return
        self.start_history_record()
        pen = QPen(self.get_display_color(QColor(255, 100, 0)), 1, Qt.PenStyle.DashDotLine)
        count = 0
        for item in selected:
            rect = item.sceneTransform().mapRect(item.boundingRect())
            cx, cy = rect.center().x(), rect.center().y()
            ext = max(rect.width(), rect.height()) / 2 * 1.2
            l1 = self.scene.addLine(cx - ext, cy, cx + ext, cy, pen)
            l2 = self.scene.addLine(cx, cy - ext, cx, cy + ext, pen)
            self.shapes.append({"type": "line", "p1": (cx - ext, cy), "p2": (cx + ext, cy), "layer": "中心線", "color": QColor(255, 100, 0), "item": l1})
            self.shapes.append({"type": "line", "p1": (cx, cy - ext), "p2": (cx, cy + ext), "layer": "中心線", "color": QColor(255, 100, 0), "item": l2})
            count += 1
        self.commit_history_record()
        if count: QMessageBox.information(self, "完了", f"{count}個のオブジェクトに中心線を生成しました。")

    # --- 雲マーク変換機能 ---
    def convert_selected_to_cloud(self):
        selected = self.scene.selectedItems()
        if not selected:
            QMessageBox.warning(self, "通知", "雲マークに変換するオブジェクトを選択してください。")
            return
        self.start_history_record()
        pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
        step, arc_height = max(5.0, self.cloud_pitch), self.cloud_arc_height

        for item in selected:
            rect = item.sceneTransform().mapRect(item.boundingRect())
            pts = []
            if isinstance(item, QGraphicsEllipseItem):
                cx, cy, rx, ry = rect.center().x(), rect.center().y(), rect.width() / 2.0, rect.height() / 2.0
                perimeter = math.pi * (3 * (rx + ry) - math.sqrt((3 * rx + ry) * (rx + 3 * ry)))
                num_pts = max(4, int(perimeter / step))
                for i in range(num_pts): pts.append((cx + rx * math.cos(2 * math.pi * i / num_pts), cy + ry * math.sin(2 * math.pi * i / num_pts)))
                pts.append(pts[0])
            else:
                x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
                corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
                for i in range(4):
                    p1, p2 = corners[i], corners[(i + 1) % 4]
                    length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                    n = max(1, int(length / step))
                    for k in range(n): pts.append((p1[0] + (k / n) * (p2[0] - p1[0]), p1[1] + (k / n) * (p2[1] - p1[1])))
                pts.append(corners[0])

            if len(pts) < 3: continue
            cloud_path = QPainterPath(); cloud_path.moveTo(QPointF(pts[0][0], pts[0][1]))
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                mx, my, dx, dy = (p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0, p2[0] - p1[0], p2[1] - p1[1]
                dist = math.hypot(dx, dy)
                if dist > 0: cloud_path.quadTo(QPointF(mx + (dy / dist) * arc_height, my + (-dx / dist) * arc_height), QPointF(p2[0], p2[1]))
            c_item = self.scene.addPath(cloud_path, pen)
            self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color, "item": c_item})
            self.safe_remove_item(item)
        self.commit_history_record()

    # --- クリップボード・複製機能 ---
    def copy_selected_to_clipboard(self):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        self.copied_shapes_buffer.clear()
        for shape in self.shapes:
            item = shape.get("item")
            if item and item.isSelected():
                s_copy = copy.deepcopy({k: v for k, v in shape.items() if k != "item"})
                self.copied_shapes_buffer.append(s_copy)

    def paste_from_clipboard(self):
        if not self.copied_shapes_buffer: return
        self.start_history_record()
        self.scene.clearSelection()
        cursor_pos = self.mapToScene(self.mapFromGlobal(self.cursor().pos()))
        
        xs, ys = [], []
        for s in self.copied_shapes_buffer:
            stype = s.get("type")
            if stype in ["line", "dimension", "arrow", "leader", "rect"]:
                xs.extend([s["p1"][0], s["p2"][0]]); ys.extend([s["p1"][1], s["p2"][1]])
            elif stype in ["circle", "ellipse", "arc"]:
                xs.append(s["center"][0]); ys.append(s["center"][1])
            elif stype in ["polyline", "spline"]:
                xs.extend([p[0] for p in s["points"]]); ys.extend([p[1] for p in s["points"]])
            elif stype in ["point", "text", "block_ref", "table", "image"]:
                xs.append(s["pos"][0]); ys.append(s["pos"][1])

        center_x = sum(xs) / len(xs) if xs else 0
        center_y = sum(ys) / len(ys) if ys else 0
        dx, dy = cursor_pos.x() - center_x, cursor_pos.y() - center_y

        for s in self.copied_shapes_buffer:
            new_s = copy.deepcopy(s)
            self._translate_shape(new_s, dx, dy)
            item = self._recreate_shape_item(new_s)
            if item:
                item.setSelected(True)
                new_s["item"] = item
                self.shapes.append(new_s)
        self.commit_history_record()

    def duplicate_selected(self):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        self.start_history_record()
        self.scene.clearSelection()
        for shape in list(self.shapes):
            item = shape.get("item")
            if item and item in selected_items:
                s_copy = copy.deepcopy({k: v for k, v in shape.items() if k != "item"})
                self._translate_shape(s_copy, 20.0, 20.0)
                new_item = self._recreate_shape_item(s_copy)
                if new_item:
                    new_item.setSelected(True)
                    s_copy["item"] = new_item
                    self.shapes.append(s_copy)
        self.commit_history_record()

    def delete_selected(self):
        deleted_items = self.scene.selectedItems()
        if deleted_items:
            self.start_history_record()
            for item in deleted_items:
                self.safe_remove_item(item)
                for shape in list(self.shapes):
                    if shape.get("item") == item:
                        self.shapes.remove(shape)
            self.commit_history_record()

    def _clone_item(self, item):
        pen = item.pen() if hasattr(item, "pen") else QPen()
        brush = item.brush() if hasattr(item, "brush") else QBrush()
        new_item = None
        if isinstance(item, QGraphicsLineItem): new_item = self.scene.addLine(item.line(), pen)
        elif isinstance(item, QGraphicsRectItem): new_item = self.scene.addRect(item.rect(), pen, brush)
        elif isinstance(item, QGraphicsEllipseItem): new_item = self.scene.addEllipse(item.rect(), pen, brush)
        elif isinstance(item, QGraphicsPolygonItem): new_item = self.scene.addPolygon(item.polygon(), pen, brush)
        elif isinstance(item, QGraphicsPathItem): new_item = self.scene.addPath(item.path(), pen)
        elif isinstance(item, QGraphicsTextItem): new_item = self.scene.addText(item.toPlainText(), item.font()); new_item.setDefaultTextColor(item.defaultTextColor())
        elif isinstance(item, (QGraphicsPixmapItem, CustomPixmapItem)): new_item = CustomPixmapItem(item.pixmap())
        if new_item:
            new_item.setPos(item.pos()); new_item.setRotation(item.rotation()); new_item.setScale(item.scale())
            new_item.setTransformOriginPoint(item.transformOriginPoint())
            new_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            new_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        return new_item

    def _recreate_shape_item(self, shape):
        stype = shape.get("type")
        color = self.get_display_color(shape.get("color", self.current_color))
        thickness = shape.get("thickness", self.current_thickness)
        style = shape.get("style", self.current_style)
        pen = QPen(color, thickness, style)
        item = None

        if stype == "image":
            fpath = shape.get("file_path", "")
            if fpath and os.path.exists(fpath):
                pixmap = None
                if fpath.lower().endswith('.pdf'):
                    try:
                        doc_pdf = pymupdf.open(fpath)
                        pix = doc_pdf[0].get_pixmap(dpi=150)
                        fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
                        pixmap = QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt))
                    except Exception: pass
                else:
                    pixmap = QPixmap(fpath)

                if pixmap and not pixmap.isNull():
                    item = CustomPixmapItem(pixmap)
                    pos_val = shape.get("pos", (0, 0))
                    item.setPos(pos_val[0], pos_val[1])
                    if "transform" in shape:
                        m = shape["transform"]; item.setTransform(QTransform(m[0], m[1], m[2], m[3], m[4], m[5]))
                    else:
                        item.setScale(shape.get("scale", 1.0)); item.setRotation(shape.get("rotation", 0.0))
                    item.setOpacity(shape.get("opacity", 1.0))
                    if shape.get("layer") == "背景図面": item.setZValue(-100)
                    self.scene.addItem(item); shape["item"] = item
        elif stype == "line": item = self.scene.addLine(shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1], pen)
        elif stype == "arrow": item = self.scene.addLine(shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1], pen); self._update_arrow_shape_graphics(shape)
        elif stype == "rect": item = self.scene.addRect(min(shape["p1"][0], shape["p2"][0]), min(shape["p1"][1], shape["p2"][1]), abs(shape["p1"][0] - shape["p2"][0]), abs(shape["p1"][1] - shape["p2"][1]), pen)
        elif stype == "circle": item = self.scene.addEllipse(shape["center"][0] - shape["radius"], shape["center"][1] - shape["radius"], 2 * shape["radius"], 2 * shape["radius"], pen)
        elif stype == "arc":
            path = QPainterPath(); path.arcMoveTo(shape["center"][0] - shape["radius"], shape["center"][1] - shape["radius"], 2 * shape["radius"], 2 * shape["radius"], shape["start_angle"])
            path.arcTo(shape["center"][0] - shape["radius"], shape["center"][1] - shape["radius"], 2 * shape["radius"], 2 * shape["radius"], shape["start_angle"], shape["span_angle"])
            item = self.scene.addPath(path, pen)
        elif stype in ["polyline", "spline"]:
            pts = [QPointF(pt[0], pt[1]) for pt in shape["points"]]
            if shape.get("is_closed"): item = self.scene.addPolygon(pts, pen)
            else:
                path = QPainterPath(); path.moveTo(pts[0]); [path.lineTo(pt) for pt in pts[1:]]
                item = self.scene.addPath(path, pen)
        elif stype == "text":
            item = self.scene.addText(shape["text"]); item.setDefaultTextColor(color); item.setFont(QFont("Meiryo", int(shape.get("font_size", 12)))); item.setPos(*shape["pos"])
        elif stype == "table":
            self.add_table_data(shape.get("grid_data", [[""]]), shape.get("cell_w", 100), shape.get("cell_h", 30), QPointF(*shape.get("pos", (0, 0))), align=shape.get("align", "CENTER"), font_size=shape.get("font_size", 12), color=shape.get("color", self.current_color), target_shape=shape)
            return shape.get("item")

        if item:
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        return item

    # --- 画像/PDFファイルの挿入 & 背景設定 (main.py連動) ---
    def insert_image_or_pdf(self, file_path, pos=None):
        self.start_history_record()
        pixmap = None
        if file_path.lower().endswith('.pdf'):
            try:
                doc = pymupdf.open(file_path)
                pix = doc[0].get_pixmap(dpi=150)
                fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
                pixmap = QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt))
            except Exception as e: 
                QMessageBox.critical(self, "エラー", f"PDF挿入失敗:\n{e}")
                return
        else:
            pixmap = QPixmap(file_path)
            if pixmap.isNull(): return

        pixmap_item = CustomPixmapItem(pixmap)
        is_select = (self.mode == "SELECT")
        pixmap_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_select)
        pixmap_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, is_select)

        if pos:
            p_x, p_y = pos.x(), pos.y()
        else:
            scene_center = self.mapToScene(self.viewport().rect().center())
            p_x, p_y = scene_center.x() - pixmap.width() / 2.0, scene_center.y() - pixmap.height() / 2.0
        
        pixmap_item.setPos(p_x, p_y)
        self.scene.addItem(pixmap_item)

        self.shapes.append({
            "type": "image",
            "file_path": file_path,
            "pos": (p_x, p_y),
            "scale": 1.0,
            "rotation": 0.0,
            "opacity": 1.0,
            "layer": self.active_layer,
            "item": pixmap_item
        })
        self.commit_history_record()

    def set_background_file(self, file_path):
        if not os.path.exists(file_path): return
        pixmap = None
        if file_path.lower().endswith('.pdf'):
            try:
                doc = pymupdf.open(file_path)
                pix = doc[0].get_pixmap(dpi=150)
                fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
                pixmap = QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt))
            except Exception as e: 
                QMessageBox.critical(self, "エラー", f"PDF読込失敗:\n{e}")
                return
        else:
            pixmap = QPixmap(file_path)
            if pixmap.isNull(): return

        pixmap_item = CustomPixmapItem(pixmap)
        pixmap_item.setPos(0, 0)
        pixmap_item.setZValue(-100)
        self.scene.addItem(pixmap_item)

        bg_layer = "背景図面" if "背景図面" in self.layers else self.active_layer
        self.shapes.append({
            "type": "image",
            "file_path": file_path,
            "pos": (0, 0),
            "scale": 1.0,
            "rotation": 0.0,
            "opacity": 1.0,
            "layer": bg_layer,
            "item": pixmap_item
        })
        self.scene.setSceneRect(-100000, -100000, 200000, 200000)

    # --- ブロック機能 (main.py連動) ---
    def create_block_from_selected(self, block_name, category="カスタム", base_point=None):
        selected_items = self.scene.selectedItems()
        if not selected_items: return False
        target_shapes = list(self.shapes)
        if not base_point:
            rect = self.scene.createItemGroup(selected_items).boundingRect()
            base_point = QPointF(rect.center().x(), rect.center().y())
        bx, by = base_point.x(), base_point.y()
        rel_shapes = []
        for s in target_shapes:
            s_copy = dict(s)
            stype = s_copy.get("type")
            if stype in ["line", "dimension", "arrow", "leader", "rect"]:
                s_copy["p1"] = (s_copy["p1"][0] - bx, s_copy["p1"][1] - by)
                s_copy["p2"] = (s_copy["p2"][0] - bx, s_copy["p2"][1] - by)
            elif stype == "circle": 
                s_copy["center"] = (s_copy["center"][0] - bx, s_copy["center"][1] - by)
            elif stype == "polyline": 
                s_copy["points"] = [(px - bx, py - by) for px, py in s_copy["points"]]
            elif stype in ["point", "text"]: 
                s_copy["pos"] = (s_copy["pos"][0] - bx, s_copy["pos"][1] - by)
            rel_shapes.append(s_copy)

        self.blocks[block_name] = {"category": category, "base_pt": (bx, by), "shapes": rel_shapes}
        self.start_history_record()
        for item in selected_items: 
            self.safe_remove_item(item)
        self.insert_block_ref(block_name, base_point)
        self.commit_history_record()
        return True

    def insert_block_ref(self, block_name, pos):
        if block_name not in self.blocks: return
        blk_def = self.blocks[block_name]
        px, py = pos.x(), pos.y()
        self.start_history_record()
        group_items = []
        for s in blk_def["shapes"]:
            stype = s.get("type")
            color = self.get_display_color(s.get("color", self.current_color))
            pen = QPen(color, s.get("thickness", self.current_thickness), s.get("style", self.current_style))
            if stype == "line": 
                item = self.scene.addLine(px + s["p1"][0], py + s["p1"][1], px + s["p2"][0], py + s["p2"][1], pen)
            elif stype == "rect": 
                item = self.scene.addRect(px + min(s["p1"][0], s["p2"][0]), py + min(s["p1"][1], s["p2"][1]), abs(s["p2"][0] - s["p1"][0]), abs(s["p2"][1] - s["p1"][1]), pen)
            elif stype == "circle": 
                item = self.scene.addEllipse(px + s["center"][0] - s["radius"], py + s["center"][1] - s["radius"], 2 * s["radius"], 2 * s["radius"], pen)
            else: 
                continue
            group_items.append(item)

        if group_items:
            group = self.scene.createItemGroup(group_items)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            self.shapes.append({
                "type": "block_ref", "block_name": block_name, "pos": (px, py), 
                "layer": self.active_layer, "color": self.current_color, 
                "thickness": self.current_thickness, "style": self.current_style, "item": group
            })
        self.commit_history_record()

    def explode_selected_block(self):
        selected = self.scene.selectedItems()
        if not selected: return
        self.start_history_record()
        for item in selected:
            if isinstance(item, QGraphicsItemGroup):
                items_in_group = item.childItems()
                self.scene.destroyItemGroup(item)
                for child in items_in_group:
                    child.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                    child.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.commit_history_record()

    # --- グループ化機能 (main.py連動) ---
    def group_selected_items(self):
        selected = self.scene.selectedItems()
        if len(selected) < 2:
            QMessageBox.warning(self, "通知", "グループ化するには2つ以上の要素を選択してください。")
            return False
        self.start_history_record()
        group_item = self.scene.createItemGroup(selected)
        group_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        group_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.shapes.append({
            "type": "group", "layer": self.active_layer, "color": self.current_color, 
            "thickness": self.current_thickness, "style": self.current_style, 
            "item_count": len(selected), "item": group_item
        })
        self.commit_history_record()
        return True

    def ungroup_selected_items(self):
        selected = self.scene.selectedItems()
        if not selected: return
        self.start_history_record()
        for item in selected:
            if isinstance(item, QGraphicsItemGroup):
                items_in_group = item.childItems()
                self.scene.destroyItemGroup(item)
                for child in items_in_group:
                    child.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                    child.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.commit_history_record()

    # --- レイヤー・集計制御 (main.py連動) ---
    def set_active_layer(self, layer_name):
        if layer_name in self.layers:
            self.active_layer = layer_name
            props = self.layers[layer_name]
            self.current_color = props["color"]
            self.current_thickness = props["thickness"]
            self.current_style = props["style"]

    def count_objects_by_layer(self):
        counts = {}
        for lyr in self.layers.keys(): counts[lyr] = {"total": 0, "basic": 0, "block_ref": 0, "group": 0, "blocks_detail": {}}
        for s in self.shapes:
            lyr = s.get("layer", "0")
            if lyr not in counts: counts[lyr] = {"total": 0, "basic": 0, "block_ref": 0, "group": 0, "blocks_detail": {}}
            stype = s.get("type")
            counts[lyr]["total"] += 1
            if stype == "block_ref":
                counts[lyr]["block_ref"] += 1
                bname = s.get("block_name", "未定義ブロック")
                counts[lyr]["blocks_detail"][bname] = counts[lyr]["blocks_detail"].get(bname, 0) + 1
            elif stype == "group": counts[lyr]["group"] += 1
            else: counts[lyr]["basic"] += 1
        return counts

    def auto_trace_region(self, rect):
        QMessageBox.information(self, "自動トレース", "選択領域の自動トレース機能を起動します。")

    # --- 印刷・PDF書き出し機能 (main.py連動) ---
    def update_paper_guide(self, size_id=None, orientation=None, scale=None, show=None, custom_rect=None):
        if size_id is not None: self.paper_size_id = size_id
        if orientation is not None: self.paper_orientation = orientation
        if scale is not None: self.paper_scale = scale
        if show is not None: self.show_paper_guide = show

        if self.paper_guide_item: 
            self.safe_remove_item(self.paper_guide_item)
            self.paper_guide_item = None
            
        if not self.show_paper_guide: return

        if custom_rect is not None:
            w_px, h_px, pos_x, pos_y = custom_rect.width(), custom_rect.height(), custom_rect.x(), custom_rect.y()
        else:
            mm_sizes = {
                QPageSize.PageSizeId.A4: (297, 210), QPageSize.PageSizeId.A3: (420, 297), 
                QPageSize.PageSizeId.A2: (594, 420), QPageSize.PageSizeId.B4: (364, 257), 
                QPageSize.PageSizeId.B5: (257, 182)
            }
            w_mm, h_mm = mm_sizes.get(self.paper_size_id, (297, 210))
            if self.paper_orientation == QPageLayout.Orientation.Landscape: 
                w_mm, h_mm = max(w_mm, h_mm), min(w_mm, h_mm)
            else: 
                w_mm, h_mm = min(w_mm, h_mm), max(w_mm, h_mm)
            w_px, h_px, pos_x, pos_y = w_mm * self.paper_scale, h_mm * self.paper_scale, 0, 0

        pen = QPen(QColor(0, 120, 215), max(2, int(self.paper_scale * 0.05)) if custom_rect is None else 2, Qt.PenStyle.DashDotLine)
        self.paper_guide_item = self.scene.addRect(0, 0, w_px, h_px, pen)
        self.paper_guide_item.setPos(pos_x, pos_y)
        self.paper_guide_item.setZValue(-10)

    def fit_paper_guide_to_selected(self):
        selected = self.scene.selectedItems()
        target = selected[0] if selected else next((i for i in self.scene.items() if isinstance(i, (QGraphicsPixmapItem, CustomPixmapItem))), None)
        if target:
            self.update_paper_guide(show=True, custom_rect=target.sceneBoundingRect())
            return True
        return False

    def set_custom_print_rect(self, rect):
        if self.custom_print_rect_item: 
            self.safe_remove_item(self.custom_print_rect_item)
            self.custom_print_rect_item = None
        if rect and not rect.isEmpty():
            self.custom_print_rect_item = self.scene.addRect(rect, QPen(QColor(255, 102, 0), 2, Qt.PenStyle.DashDotDotLine))
            self.custom_print_rect_item.setZValue(99)

    def clear_custom_print_rect(self):
        if self.custom_print_rect_item: 
            self.safe_remove_item(self.custom_print_rect_item)
            self.custom_print_rect_item = None

    def _get_target_render_rect(self):
        if self.custom_print_rect_item: return self.custom_print_rect_item.sceneBoundingRect()
        if self.show_paper_guide and self.paper_guide_item: return self.paper_guide_item.sceneBoundingRect()
        rect = self.scene.itemsBoundingRect()
        return rect if not rect.isEmpty() else self.scene.sceneRect()

    def print_scene(self):
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QPageSize(self.paper_size_id if self.show_paper_guide else QPageSize.PageSizeId.A4))
        printer.setPageOrientation(self.paper_orientation if self.show_paper_guide else QPageLayout.Orientation.Landscape)

        if QPrintDialog(printer, self).exec() == QPrintDialog.DialogCode.Accepted:
            self.apply_layer_states(is_export=True)

            if self.paper_guide_item: self.paper_guide_item.setVisible(False)
            if self.custom_print_rect_item: self.custom_print_rect_item.setVisible(False)

            painter = QPainter(printer)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.scene.render(painter, QRectF(printer.pageLayout().paintRectPixels(printer.resolution())), self._get_target_render_rect())
            painter.end()

            if self.paper_guide_item: self.paper_guide_item.setVisible(self.show_paper_guide)
            if self.custom_print_rect_item: self.custom_print_rect_item.setVisible(True)

            self.apply_layer_states(is_export=False)
            QMessageBox.information(self, "完了", "印刷処理が完了しました。")

    def export_to_pdf(self, page_size_id, orientation):
        file_path, _ = QFileDialog.getSaveFileName(self, "PDFファイルとして出力", "", "PDF Files (*.pdf)")
        if not file_path: return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(file_path)
        printer.setPageSize(QPageSize(page_size_id))
        printer.setPageOrientation(orientation)

        self.apply_layer_states(is_export=True)

        if self.paper_guide_item: self.paper_guide_item.setVisible(False)
        if self.custom_print_rect_item: self.custom_print_rect_item.setVisible(False)

        painter = QPainter(printer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.scene.render(painter, QRectF(printer.pageLayout().paintRectPixels(printer.resolution())), self._get_target_render_rect())
        painter.end()

        if self.paper_guide_item: self.paper_guide_item.setVisible(self.show_paper_guide)
        if self.custom_print_rect_item: self.custom_print_rect_item.setVisible(True)

        self.apply_layer_states(is_export=False)
        QMessageBox.information(self, "成功", f"PDFを出力しました:\n{file_path}")

    # --- プロパティ・レイヤー変更ヘルパー関数 ---
    def prompt_change_selected_layer(self):
        layer_names = list(self.layers.keys())
        current_idx = layer_names.index(self.active_layer) if self.active_layer in layer_names else 0
        layer_name, ok = QInputDialog.getItem(self, "レイヤー変更", "変更先のレイヤーを選択してください:", layer_names, current_idx, False)
        if ok and layer_name: self.change_selected_layer(layer_name)

    def change_selected_layer(self, new_layer_name):
        selected_items = self.scene.selectedItems()
        if not selected_items or new_layer_name not in self.layers: return
        self.start_history_record()
        layer_props = self.layers[new_layer_name]
        
        for shape in self.shapes:
            item = shape.get("item")
            if item and item.isSelected():
                shape["layer"] = new_layer_name
                if shape.get("type") == "image":
                    item.setZValue(-100 if new_layer_name == "背景図面" else 0)
                else:
                    shape["color"] = layer_props["color"]
                    shape["thickness"] = layer_props["thickness"]
                    shape["style"] = layer_props["style"]

        self.apply_layer_states()
        self.commit_history_record()

    def prompt_change_selected_color(self):
        selected = self.scene.selectedItems()
        if not selected: return
        current_color = self.current_color
        for s in self.shapes:
            if s.get("item") in selected and "color" in s:
                current_color = s["color"]; break
                
        col = QColorDialog.getColor(current_color, self, "選択した図形の色を個別に変更")
        if col.isValid(): self.apply_property_to_selected(color=col)

    def prompt_change_selected_style(self):
        selected = self.scene.selectedItems()
        if not selected: return
        
        current_thickness, current_style = self.current_thickness, self.current_style
        for s in self.shapes:
            if s.get("item") in selected:
                if "thickness" in s: current_thickness = s["thickness"]
                if "style" in s: current_style = s["style"]
                break

        thickness, ok1 = QInputDialog.getInt(self, "線の太さ", "線の太さ（1〜20）:", current_thickness, 1, 20, 1)
        if not ok1: return
        
        styles = {"実線 (Solid)": Qt.PenStyle.SolidLine, "破線 (Dash)": Qt.PenStyle.DashLine, "一点鎖線 (DashDot)": Qt.PenStyle.DashDotLine, "点線 (Dot)": Qt.PenStyle.DotLine}
        style_names = list(styles.keys())
        curr_style_idx = list(styles.values()).index(current_style) if current_style in styles.values() else 0

        s_name, ok2 = QInputDialog.getItem(self, "線種", "線種を選択:", style_names, curr_style_idx, False)
        if ok2: self.apply_property_to_selected(thickness=thickness, style=styles[s_name])

    def prompt_edit_layer_settings(self):
        layer_names = list(self.layers.keys())
        current_idx = layer_names.index(self.active_layer) if self.active_layer in layer_names else 0
        layer_name, ok = QInputDialog.getItem(self, "レイヤー設定の一括変更", "デフォルト設定を変更するレイヤーを選択:", layer_names, current_idx, False)
        if not ok or not layer_name: return

        props = self.layers[layer_name]
        col = QColorDialog.getColor(props["color"], self, f"[{layer_name}] レイヤーの基本色を選択")
        if not col.isValid(): return

        thickness, ok2 = QInputDialog.getInt(self, f"[{layer_name}] 線の太さ", "線の太さ（1〜20）:", props["thickness"], 1, 20, 1)
        if not ok2: return

        styles = {"実線 (Solid)": Qt.PenStyle.SolidLine, "破線 (Dash)": Qt.PenStyle.DashLine, "一点鎖線 (DashDot)": Qt.PenStyle.DashDotLine, "点線 (Dot)": Qt.PenStyle.DotLine}
        style_names = list(styles.keys())
        curr_style_idx = list(styles.values()).index(props["style"]) if props["style"] in styles.values() else 0

        s_name, ok3 = QInputDialog.getItem(self, f"[{layer_name}] 線種", "線種を選択:", style_names, curr_style_idx, False)
        if not ok3: return

        self.start_history_record()
        self.layers[layer_name]["color"] = col
        self.layers[layer_name]["thickness"] = thickness
        self.layers[layer_name]["style"] = styles[s_name]

        if self.active_layer == layer_name:
            self.current_color = col; self.current_thickness = thickness; self.current_style = styles[s_name]

        for shape in self.shapes:
            if shape.get("layer") == layer_name:
                shape["color"] = col; shape["thickness"] = thickness; shape["style"] = styles[s_name]

        self.apply_layer_states()
        self.commit_history_record()
        QMessageBox.information(self, "完了", f"レイヤー [{layer_name}] の設定を一括更新しました。")

    def set_selected_image_opacity(self):
        selected = [i for i in self.scene.selectedItems() if isinstance(i, (QGraphicsPixmapItem, CustomPixmapItem))]
        if not selected: return
        current_opacity = int(selected[0].opacity() * 100)
        val, ok = QInputDialog.getInt(self, "透明度の変更", "不透明度を入力してください (10〜100%):", current_opacity, 10, 100, 5)
        if ok:
            new_opacity = val / 100.0
            self.start_history_record()
            for item in selected:
                item.setOpacity(new_opacity)
                for shape in self.shapes:
                    if shape.get("item") == item:
                        shape["opacity"] = new_opacity
            self.commit_history_record()

    # --- 新規作成・保存・読み込み ---
    def new_project(self):
        if self.shapes or any(not isinstance(i, QGraphicsItemGroup) for i in self.scene.items()):
            res = QMessageBox.question(self, "新規作成", "現在の図面を全消去して新規作成しますか？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if res != QMessageBox.StandardButton.Yes: return False

        self.scene.clear(); self.shapes.clear(); self.undo_stack.clear(); self.redo_stack.clear()
        self.paper_guide_item, self.custom_print_rect_item = None, None
        self.set_mode("SELECT")
        return True

    def save_project_json(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "プロジェクトを保存", "", "CAD Project Files (*.json)")
        if not file_path: return
        try:
            self._sync_item_transforms()
            data = {"version": "1.0", "layers": _clean_for_json(self.layers), "blocks": _clean_for_json(self.blocks), "shapes": _clean_for_json(self.shapes), "paper_scale": self.paper_scale, "active_layer": self.active_layer}
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False, default=lambda obj: obj.name() if isinstance(obj, QColor) else getattr(obj, "value", str(obj)))
            QMessageBox.information(self, "成功", f"プロジェクトを保存しました:\n{file_path}")
        except Exception as e: QMessageBox.critical(self, "エラー", f"保存失敗:\n{e}")

    def load_project_json(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "プロジェクトを開く", "", "CAD Project Files (*.json)")
        if not file_path: return
        try:
            with open(file_path, "r", encoding="utf-8") as f: data = json.load(f)
            self.scene.clear(); self.shapes.clear(); self.layers.clear()

            for name, props in data.get("layers", {}).items():
                try: style = Qt.PenStyle(int(props.get("style", 1)))
                except: style = Qt.PenStyle.SolidLine
                self.layers[name] = {"color": QColor(props.get("color", "#000000")), "thickness": props.get("thickness", 2), "style": style, "visible": props.get("visible", True), "locked": props.get("locked", False), "printable": props.get("printable", True)}

            self.active_layer, self.paper_scale, self.blocks = data.get("active_layer", "0"), data.get("paper_scale", 100), data.get("blocks", {})

            for s in data.get("shapes", []):
                s["color"] = QColor(s.get("color", "#FF0000")) if isinstance(s.get("color"), str) else s.get("color")
                try: s["style"] = Qt.PenStyle(int(s.get("style", 1)))
                except: s["style"] = Qt.PenStyle.SolidLine

                stype = s.get("type")
                color = self.get_display_color(s["color"])
                thickness = s.get("thickness", self.current_thickness)
                pen = QPen(color, thickness, style)

                if stype == "image":
                    fpath = s.get("file_path", "")
                    if fpath and os.path.exists(fpath):
                        pixmap = pymupdf.open(fpath)[0].get_pixmap(dpi=150) if fpath.lower().endswith('.pdf') else None
                        pixmap = QPixmap.fromImage(QImage(pixmap.samples, pixmap.width, pixmap.height, pixmap.stride, QImage.Format.Format_RGBA8888 if pixmap.alpha else QImage.Format.Format_RGB888)) if pixmap else QPixmap(fpath)
                        if pixmap and not pixmap.isNull():
                            item = CustomPixmapItem(pixmap)
                            pos_val = s.get("pos", (0, 0))
                            item.setPos(pos_val[0], pos_val[1])
                            if "transform" in s:
                                m = s["transform"]; item.setTransform(QTransform(m[0], m[1], m[2], m[3], m[4], m[5]))
                            else:
                                item.setScale(s.get("scale", 1.0)); item.setRotation(s.get("rotation", 0.0))
                            item.setOpacity(s.get("opacity", 1.0))
                            if s.get("layer") == "背景図面": item.setZValue(-100)
                            self.scene.addItem(item); s["item"] = item; self.shapes.append(s)
                    continue

                elif stype == "dimension": self.create_autocad_dimension(QPointF(*s["p1"]), QPointF(*s["p2"])); continue
                elif stype == "arrow": item = self.scene.addLine(s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1], pen); s["item"] = item; self._update_arrow_shape_graphics(s); self.shapes.append(s); continue
                elif stype == "leader": self.add_leader_with_auto_measure(QPointF(*s["p1"]), QPointF(*s["p2"]), text=s.get("text")); continue
                elif stype == "rect": item = self.scene.addRect(min(s["p1"][0], s["p2"][0]), min(s["p1"][1], s["p2"][1]), abs(s["p1"][0] - s["p2"][0]), abs(s["p1"][1] - s["p2"][1]), pen)
                elif stype == "circle": item = self.scene.addEllipse(s["center"][0] - s["radius"], s["center"][1] - s["radius"], 2 * s["radius"], 2 * s["radius"], pen)
                elif stype == "arc":
                    path = QPainterPath(); path.arcMoveTo(s["center"][0] - s["radius"], s["center"][1] - s["radius"], 2 * s["radius"], 2 * s["radius"], s["start_angle"])
                    path.arcTo(s["center"][0] - s["radius"], s["center"][1] - s["radius"], 2 * s["radius"], 2 * s["radius"], s["start_angle"], s["span_angle"])
                    item = self.scene.addPath(path, pen)
                elif stype == "table": self.add_table_data(s.get("grid_data", [[""]]), s.get("cell_w", 100), s.get("cell_h", 30), QPointF(*s.get("pos", (0, 0))), align=s.get("align", "CENTER"), font_size=s.get("font_size", 12), color=s.get("color", self.current_color), target_shape=s); continue
                elif stype == "polyline":
                    pts = [QPointF(pt[0], pt[1]) for pt in s["points"]]
                    if s.get("is_closed"): item = self.scene.addPolygon(pts, pen)
                    else:
                        path = QPainterPath(); path.moveTo(pts[0])
                        for pt in pts[1:]: path.lineTo(pt)
                        item = self.scene.addPath(path, pen)
                elif stype == "text": item = self.scene.addText(s["text"]); item.setDefaultTextColor(color); item.setFont(QFont("Meiryo", int(s.get("font_size", 12)))); item.setPos(*s["pos"])
                elif stype == "line": item = self.scene.addLine(s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1], pen)

                s["item"] = item; self.shapes.append(s)

            self.apply_layer_states()
            QMessageBox.information(self, "成功", f"プロジェクトを読み込みました:\n{file_path}")
        except Exception as e: QMessageBox.critical(self, "エラー", f"読み込み失敗:\n{e}")

    # --- DXFインポート/エクスポート ---
    def import_dxf_file(self, file_path=None):
        if not file_path: file_path, _ = QFileDialog.getOpenFileName(self, "DXFファイルを開く", "", "DXF Files (*.dxf)")
        if not file_path or not os.path.exists(file_path): return
        try:
            doc = ezdxf.readfile(file_path); msp = doc.modelspace(); self.start_history_record()
            for entity in msp:
                dxftype = entity.dxftype()
                layer_name = entity.dxf.layer if hasattr(entity.dxf, 'layer') else "0"
                if layer_name not in self.layers: self.layers[layer_name] = {"color": QColor(0, 0, 0), "thickness": 2, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True}
                color = self.layers[layer_name]["color"]; pen = QPen(self.get_display_color(color), self.current_thickness, self.current_style)

                if dxftype == 'LINE':
                    p1, p2 = (entity.dxf.start.x, -entity.dxf.start.y), (entity.dxf.end.x, -entity.dxf.end.y)
                    item = self.scene.addLine(p1[0], p1[1], p2[0], p2[1], pen)
                    self.shapes.append({"type": "line", "p1": p1, "p2": p2, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
                elif dxftype == 'CIRCLE':
                    cx, cy, r = entity.dxf.center.x, -entity.dxf.center.y, entity.dxf.radius
                    item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
                    self.shapes.append({"type": "circle", "center": (cx, cy), "radius": r, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
                elif dxftype in ['LWPOLYLINE', 'POLYLINE']:
                    pts = [(p[0], -p[1]) for p in entity.get_points()]; is_closed = entity.is_closed
                    qpts = [QPointF(px, py) for px, py in pts]
                    if is_closed: item = self.scene.addPolygon(QPolygonF(qpts), pen)
                    else:
                        path = QPainterPath()
                        if qpts: path.moveTo(qpts[0]); [path.lineTo(pt) for pt in qpts[1:]]
                        item = self.scene.addPath(path, pen)
                    self.shapes.append({"type": "polyline", "points": pts, "is_closed": is_closed, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
                elif dxftype in ['TEXT', 'MTEXT']:
                    txt = entity.dxf.text if dxftype == 'TEXT' else entity.text
                    pos = (entity.dxf.insert.x, -entity.dxf.insert.y) if hasattr(entity.dxf, 'insert') else (0, 0)
                    t_item = self.scene.addText(txt); t_item.setDefaultTextColor(self.get_display_color(color)); t_item.setFont(QFont("Meiryo", 12)); t_item.setPos(pos[0], pos[1])
                    self.shapes.append({"type": "text", "text": txt, "pos": pos, "font_size": 12, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": t_item})
                elif dxftype == 'IMAGE':
                    image_def = entity.image_def
                    if image_def and image_def.dxf.filename and os.path.exists(image_def.dxf.filename):
                        pixmap = QPixmap(image_def.dxf.filename)
                        if not pixmap.isNull():
                            img_w, img_h = pixmap.width(), pixmap.height()
                            u_pix, v_pix = getattr(entity.dxf, 'u_pixel', (1.0, 0.0)), getattr(entity.dxf, 'v_pixel', (0.0, 1.0))
                            canvas_px, canvas_py = entity.dxf.insert.x + v_pix[0] * img_h, -(entity.dxf.insert.y + v_pix[1] * img_h)
                            m11, m12, m21, m22 = u_pix[0], -u_pix[1], -v_pix[0], v_pix[1]

                            item = CustomPixmapItem(pixmap); item.setPos(canvas_px, canvas_py); item.setTransform(QTransform(m11, m12, m21, m22, 0, 0))
                            if layer_name == "背景図面": item.setZValue(-100)
                            self.scene.addItem(item)
                            self.shapes.append({"type": "image", "file_path": image_def.dxf.filename, "pos": (canvas_px, canvas_py), "transform": [m11, m12, m21, m22, 0, 0], "scale": math.hypot(m11, m12), "rotation": math.degrees(math.atan2(-m12, m11)), "opacity": 1.0, "layer": layer_name, "item": item})

            self.apply_layer_states(); self.commit_history_record()
            QMessageBox.information(self, "成功", f"DXFファイルを読み込みました:\n{os.path.basename(file_path)}")
        except Exception as e: QMessageBox.critical(self, "エラー", f"DXFインポート失敗:\n{e}")

    def save_to_dxf(self):
        self.finish_polyline()
        file_path, _ = QFileDialog.getSaveFileName(self, "DXF形式で保存", "", "DXF Files (*.dxf)")
        if not file_path: return
        try:
            self._sync_item_transforms()
            doc = ezdxf.new('R2010'); msp = doc.modelspace()
            for l_name, l_props in self.layers.items():
                if not doc.layers.has_entry(l_name): doc.layers.add(name=l_name).rgb = (l_props["color"].red(), l_props["color"].green(), l_props["color"].blue())

            for shape in self.shapes:
                color, layer = shape.get("color", self.current_color), shape.get("layer", "0")
                attribs = {'true_color': ezdxf.rgb2int((color.red(), color.green(), color.blue())), 'layer': layer}
                stype = shape.get("type")

                if stype == "image":
                    fpath = shape.get("file_path", "")
                    if fpath and os.path.exists(fpath):
                        pixmap = QPixmap(fpath)
                        img_w, img_h = pixmap.width(), pixmap.height()
                        item = shape.get("item")
                        if item:
                            scene_tf = item.sceneTransform()
                            p_bl, p_br, p_tl = scene_tf.map(QPointF(0, img_h)), scene_tf.map(QPointF(img_w, img_h)), scene_tf.map(QPointF(0, 0))
                            u_vec, v_vec = (p_br.x() - p_bl.x(), -(p_br.y() - p_bl.y())), (p_tl.x() - p_bl.x(), -(p_tl.y() - p_bl.y()))
                            image_def = doc.add_image_def(filename=fpath, size_in_pixel=(img_w, img_h))
                            image_entity = msp.add_image(image_def=image_def, insert=(p_bl.x(), -p_bl.y()), size_in_units=(math.hypot(u_vec[0], u_vec[1]), math.hypot(v_vec[0], v_vec[1])), dxfattribs=attribs)
                            image_entity.dxf.u_pixel, image_entity.dxf.v_pixel = (u_vec[0] / img_w, u_vec[1] / img_w), (v_vec[0] / img_h, v_vec[1] / img_h)

                elif stype == "line": msp.add_line((shape["p1"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p2"][1]), dxfattribs=attribs)
                elif stype == "arrow": msp.add_line((shape["p1"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p2"][1]), dxfattribs=attribs)
                elif stype == "dimension":
                    msp.add_line((shape["p1"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p2"][1]), dxfattribs=attribs)
                    if shape.get("val_str"): msp.add_text(shape["val_str"], dxfattribs={'height': 12, 'insert': ((shape["p1"][0] + shape["p2"][0])/2, -(shape["p1"][1] + shape["p2"][1])/2)} | attribs)
                elif stype == "leader":
                    p1, p2 = shape["p1"], shape["p2"]
                    p3 = shape.get("p3", (p2[0] + (40 if p2[0] >= p1[0] else -40), p2[1]))
                    msp.add_line((p1[0], -p1[1]), (p2[0], -p2[1]), dxfattribs=attribs)
                    msp.add_line((p2[0], -p2[1]), (p3[0], -p3[1]), dxfattribs=attribs)
                    ang, size = math.atan2(p1[1] - p2[1], p1[0] - p2[0]), max(10, shape.get("thickness", self.current_thickness) * 3.5)
                    pa1 = (p1[0] - size * math.cos(ang - math.pi / 6), -(p1[1] - size * math.sin(ang - math.pi / 6)))
                    pa2 = (p1[0] - size * math.cos(ang + math.pi / 6), -(p1[1] - size * math.sin(ang + math.pi / 6)))
                    if shape.get("arrow_head_type", self.arrow_head_type) == "FILLED": msp.add_solid([(p1[0], -p1[1]), pa1, pa2], dxfattribs=attribs)
                    else: msp.add_line((p1[0], -p1[1]), pa1, dxfattribs=attribs); msp.add_line((p1[0], -p1[1]), pa2, dxfattribs=attribs)
                    if shape.get("text"): msp.add_text(shape["text"], dxfattribs={'height': int(shape.get("font_size", 12)), 'insert': (p2[0] if p3[0] >= p2[0] else p3[0], -p2[1] + 2)} | attribs)
                elif stype == "rect": msp.add_lwpolyline([(shape["p1"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p2"][1]), (shape["p1"][0], -shape["p2"][1])], close=True, dxfattribs=attribs)
                elif stype == "circle": msp.add_circle((shape["center"][0], -shape["center"][1]), shape["radius"], dxfattribs=attribs)
                elif stype == "arc": msp.add_arc((shape["center"][0], -shape["center"][1]), shape["radius"], -shape["start_angle"], -shape["start_angle"] - shape["span_angle"], dxfattribs=attribs)
                elif stype in ["polyline", "spline"]: msp.add_lwpolyline([(x, -y) for x, y in shape["points"]], close=shape.get("is_closed", False), dxfattribs=attribs)
                elif stype == "text": msp.add_text(shape.get("text", ""), dxfattribs={'height': int(shape.get("font_size", 12)), 'insert': (shape["pos"][0], -shape["pos"][1])} | attribs)

            doc.saveas(file_path); QMessageBox.information(self, "成功", f"DXFファイルを保存しました:\n{file_path}")
        except Exception as e: QMessageBox.critical(self, "エラー", f"DXF保存失敗:\n{e}")

    # --- 直線・オフセット関連コマンド ---
    def prompt_line_length_settings(self):
        val, ok = QInputDialog.getDouble(self, "水平・垂直線の長さ指定", "描画する線の長さ（mm）を入力してください\n(0 を指定すると画面端まで伸ばします):", self.preset_line_length, 0.0, 100000.0, 1)
        if ok: self.preset_line_length = val

    def prompt_offset_settings(self):
        val, ok = QInputDialog.getDouble(self, "平行線 (オフセット)", "オフセットのピッチ距離（mm）を入力してください:", self.offset_dist, 0.1, 100000.0, 1)
        if ok and val > 0: self.offset_dist = val; self.execute_exact_offset(val)

    def prompt_xy_offset_settings(self):
        dx_val, ok1 = QInputDialog.getDouble(self, "X軸方向ピッチ平行複写", "X軸方向（左右）のピッチ距離（mm）:", 50.0, -100000.0, 100000.0, 1)
        if not ok1: return
        dy_val, ok2 = QInputDialog.getDouble(self, "Y軸方向ピッチ平行複写", "Y軸方向（上下）のピッチ距離（mm）:", 0.0, -100000.0, 100000.0, 1)
        if ok2: self.execute_xy_pitch_offset(dx_val, dy_val)

    def execute_exact_offset(self, dist):
        selected = self.scene.selectedItems()
        if not selected: QMessageBox.warning(self, "通知", "オフセットする線を選択してください。"); return
        self.start_history_record(); pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)

        for item in selected:
            shape = next((s for s in self.shapes if s.get("item") == item), None)
            if not shape: continue
            if shape.get("type") == "line":
                p1, p2 = QPointF(*shape["p1"]), QPointF(*shape["p2"])
                length = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
                if length > 1e-4:
                    nx, ny = -(p2.y() - p1.y()) / length, (p2.x() - p1.x()) / length
                    new_p1, new_p2 = (p1.x() + nx * dist, p1.y() + ny * dist), (p2.x() + nx * dist, p2.y() + ny * dist)
                    o_item = self.scene.addLine(new_p1[0], new_p1[1], new_p2[0], new_p2[1], pen)
                    self.shapes.append({"type": "line", "p1": new_p1, "p2": new_p2, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": o_item})
            elif shape.get("type") == "polyline":
                ls = LineString(shape["points"]).parallel_offset(dist, 'left')
                if not ls.is_empty and hasattr(ls, 'coords'):
                    c_pts = list(ls.coords)
                    path = QPainterPath(); path.moveTo(QPointF(c_pts[0][0], c_pts[0][1]))
                    [path.lineTo(QPointF(pt[0], pt[1])) for pt in c_pts[1:]]
                    o_item = self.scene.addPath(path, pen)
                    self.shapes.append({"type": "polyline", "points": c_pts, "is_closed": False, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": o_item})
        self.commit_history_record()

    def execute_xy_pitch_offset(self, dx_pitch, dy_pitch):
        selected = self.scene.selectedItems()
        if not selected: QMessageBox.warning(self, "通知", "平行複写するオブジェクトを選択してください。"); return
        self.start_history_record()
        for item in selected:
            for shape in list(self.shapes):
                if shape.get("item") == item:
                    s_copy = copy.deepcopy({k: v for k, v in shape.items() if k != "item"})
                    self._translate_shape(s_copy, dx_pitch, dy_pitch)
                    new_item = self._recreate_shape_item(s_copy)
                    if new_item: new_item.setSelected(True); s_copy["item"] = new_item; self.shapes.append(s_copy)
        self.commit_history_record()

    # --- 各種編集・計測ヘルパー ---
    def calculate_shape_measurements(self, pos):
        click_pt = Point(pos.x(), pos.y())
        target_shape = next((s for s in self.shapes if self._shape_to_shapely(s) and self._shape_to_shapely(s).distance(click_pt) < 20.0), None)
        if not target_shape: return ""
        stype = target_shape.get("type")

        if stype in ["line", "dimension", "arrow"]:
            p1, p2 = target_shape["p1"], target_shape["p2"]
            length = math.hypot(p2[0] - p1[0], p2[1] - p1[1]) * self.scale_factor
            return f"L = {length:.1f} mm"
        elif stype == "rect":
            w = abs(target_shape["p2"][0] - target_shape["p1"][0]) * self.scale_factor
            h = abs(target_shape["p2"][1] - target_shape["p1"][1]) * self.scale_factor
            return f"A = {(w * h) / 1000000.0:.2f} m²\n(L = {2*(w+h):.1f} mm)"
        elif stype == "circle":
            r = target_shape["radius"] * self.scale_factor
            return f"A = {(math.pi * r * r) / 1000000.0:.2f} m²\n(φ = {2*r:.1f} mm)"
        elif stype == "polyline":
            pts = target_shape["points"]
            geom = LineString(pts + [pts[0]]) if target_shape.get("is_closed") else LineString(pts)
            length_mm = geom.length * self.scale_factor
            if target_shape.get("is_closed") and len(pts) >= 3:
                return f"A = {(Polygon(pts).area * (self.scale_factor ** 2)) / 1000000.0:.2f} m²\n(L = {length_mm:.1f} mm)"
            return f"L = {length_mm:.1f} mm"
        return ""

    def edit_text_shape(self, shape):
        if not shape or shape.get("type") != "text": return
        dlg = TextEditDialog(self, text=shape.get("text", ""), font_size=shape.get("font_size", 12), color=shape.get("color", self.current_color))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_txt, new_size, new_color = dlg.get_result()
            if new_txt.strip():
                self.start_history_record()
                shape["text"], shape["font_size"], shape["color"] = new_txt.strip(), new_size, new_color
                item = shape.get("item")
                if item and isinstance(item, QGraphicsTextItem):
                    item.setPlainText(new_txt.strip()); item.setFont(QFont("Meiryo", int(new_size))); item.setDefaultTextColor(self.get_display_color(new_color))
                self.commit_history_record()

    def edit_table_shape(self, shape):
        if not shape or shape.get("type") != "table": return
        pos_val = shape.get("pos", (0, 0))
        dlg = TableEditDialog(self, grid_data=shape.get("grid_data", [[""]]), cell_w=shape.get("cell_w", 100), cell_h=shape.get("cell_h", 30), align=shape.get("align", "CENTER"), font_size=shape.get("font_size", 12), color=shape.get("color", self.current_color))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_grid, new_w, new_h, new_align, new_fsize, new_color = dlg.get_result()
            self.add_table_data(new_grid, new_w, new_h, QPointF(pos_val[0], pos_val[1]), align=new_align, font_size=new_fsize, color=new_color, target_shape=shape)

    def add_table_data(self, grid_data, cell_w, cell_h, pos, align="CENTER", font_size=None, color=None, target_shape=None):
        rows, cols = len(grid_data), max(len(r) for r in grid_data) if grid_data else 0
        if rows == 0 or cols == 0: return
        align = str(align).upper()
        font_size = int(font_size) if font_size is not None else max(10, int(self.current_thickness * 3.5))
        table_color = QColor(color) if color else self.current_color
        font = QFont("Meiryo", font_size)

        self.start_history_record()
        disp_color = self.get_display_color(table_color)
        pen = QPen(disp_color, self.current_thickness, Qt.PenStyle.SolidLine)
        sx, sy = pos.x(), pos.y()
        table_items = []

        for r in range(rows + 1): table_items.append(self.scene.addLine(sx, sy + r * cell_h, sx + cols * cell_w, sy + r * cell_h, pen))
        for c in range(cols + 1): table_items.append(self.scene.addLine(sx + c * cell_w, sy, sx + c * cell_w, sy + rows * cell_h, pen))

        for r in range(rows):
            for c in range(len(grid_data[r])):
                val = str(grid_data[r][c]).strip()
                if val:
                    t_item = self.scene.addText(val, font); t_item.setDefaultTextColor(disp_color)
                    t_rect = t_item.boundingRect()
                    cell_x, cell_y = sx + c * cell_w, sy + r * cell_h
                    ty = cell_y + (cell_h - t_rect.height()) / 2.0
                    tx = cell_x + (cell_w - t_rect.width()) / 2.0 if align == "CENTER" else (cell_x + cell_w - t_rect.width() - 5.0 if align == "RIGHT" else cell_x + 5.0)
                    t_item.setPos(tx, ty); table_items.append(t_item)

        if table_items:
            group = self.scene.createItemGroup(table_items)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True); group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            if target_shape and target_shape in self.shapes:
                if target_shape.get("item"): self.safe_remove_item(target_shape["item"])
                target_shape.update({"grid_data": grid_data, "cell_w": cell_w, "cell_h": cell_h, "align": align, "font_size": font_size, "color": table_color, "pos": (sx, sy), "item": group})
            else:
                self.shapes.append({"type": "table", "grid_data": grid_data, "cell_w": cell_w, "cell_h": cell_h, "align": align, "font_size": font_size, "pos": (sx, sy), "layer": self.active_layer, "color": table_color, "thickness": self.current_thickness, "style": self.current_style, "item": group})
        self.commit_history_record()

    def create_autocad_dimension(self, p1, p2, offset=25.0):
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y(); dist = math.hypot(dx, dy)
        if dist < 1e-4: return
        angle_rad = math.atan2(dy, dx)
        nx, ny = -dy / dist, dx / dist
        p1_ext, p2_ext = QPointF(p1.x() + nx * offset, p1.y() + ny * offset), QPointF(p2.x() + nx * offset, p2.y() + ny * offset)

        disp_color = self.get_display_color(self.current_color)
        pen_dim, pen_ext = QPen(disp_color, self.current_thickness, Qt.PenStyle.SolidLine), QPen(disp_color, max(1, self.current_thickness - 1), Qt.PenStyle.SolidLine)

        items = [
            self.scene.addLine(p1.x() + nx * 2, p1.y() + ny * 2, p1_ext.x() + nx * 5, p1_ext.y() + ny * 5, pen_ext),
            self.scene.addLine(p2.x() + nx * 2, p2.y() + ny * 2, p2_ext.x() + nx * 5, p2_ext.y() + ny * 5, pen_ext),
            self.scene.addLine(p1_ext.x(), p1_ext.y(), p2_ext.x(), p2_ext.y(), pen_dim)
        ]
        items.extend(self._draw_arrow_head_shape(p2_ext, angle_rad, self.arrow_head_type))
        items.extend(self._draw_arrow_head_shape(p1_ext, angle_rad + math.pi, self.arrow_head_type))

        val_str = f"{dist * self.scale_factor:.1f}"
        t_item = self.scene.addText(val_str); t_item.setDefaultTextColor(disp_color); t_item.setFont(QFont("Meiryo", int(max(10, self.current_thickness * 3.5))))
        t_rect = t_item.boundingRect(); t_item.setTransformOriginPoint(t_rect.width() / 2.0, t_rect.height() / 2.0)
        text_angle = math.degrees(angle_rad)
        if text_angle > 90 or text_angle < -90: text_angle += 180.0
        t_item.setRotation(text_angle)
        t_item.setPos((p1_ext.x() + p2_ext.x()) / 2.0 - t_rect.width() / 2.0 + nx * 8, (p1_ext.y() + p2_ext.y()) / 2.0 - t_rect.height() / 2.0 + ny * 8)
        items.append(t_item)

        group = self.scene.createItemGroup(items)
        group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True); group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.shapes.append({"type": "dimension", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "val_str": val_str, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": group})

    def create_radius_dimension(self, p1, p2, is_diameter=False):
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y(); dist = math.hypot(dx, dy)
        if dist < 1e-4: return

        disp_color = self.get_display_color(self.current_color); pen = QPen(disp_color, self.current_thickness, Qt.PenStyle.SolidLine)
        items = []
        angle_rad = math.atan2(dy, dx); angle_deg = math.degrees(angle_rad)

        if is_diameter:
            p0 = QPointF(p1.x() - dx, p1.y() - dy)
            items.append(self.scene.addLine(p0.x(), p0.y(), p2.x(), p2.y(), pen))
            items.extend(self._draw_arrow_head_shape(p2, angle_rad, self.arrow_head_type))
            items.extend(self._draw_arrow_head_shape(p0, angle_rad + math.pi, self.arrow_head_type))
            val_str = f"φ{dist * 2.0 * self.scale_factor:.1f}"
        else:
            items.append(self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen))
            items.extend(self._draw_arrow_head_shape(p2, angle_rad, self.arrow_head_type))
            val_str = f"R{dist * self.scale_factor:.1f}"

        t_item = self.scene.addText(val_str); t_item.setDefaultTextColor(disp_color); t_item.setFont(QFont("Meiryo", int(max(10, self.current_thickness * 3.5))))
        text_angle = angle_deg
        if text_angle > 90 or text_angle < -90: text_angle += 180.0
        t_rect = t_item.boundingRect(); t_item.setTransformOriginPoint(t_rect.width() / 2.0, t_rect.height() / 2.0)
        t_item.setRotation(text_angle)
        t_item.setPos((p1.x() + p2.x()) / 2.0 - t_rect.width() / 2.0, (p1.y() + p2.y()) / 2.0 - t_rect.height() / 2.0 - 5.0)
        items.append(t_item)

        group = self.scene.createItemGroup(items)
        group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True); group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.shapes.append({"type": "dimension", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "val_str": val_str, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": group})

    def add_leader_with_auto_measure(self, p1, p2, text=None):
        if text is None:
            text = self.calculate_shape_measurements(p1) or QInputDialog.getText(self, "引き出し線注釈", "注釈文字を入力してください:")[0]
            if not text: return

        disp_color = self.get_display_color(self.current_color); pen = QPen(disp_color, self.current_thickness, self.current_style)
        leader_items = [self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)]
        leader_items.extend(self._draw_arrow_head_shape(p1, math.atan2(p1.y() - p2.y(), p1.x() - p2.x())))

        landing = 40 if p2.x() >= p1.x() else -40; p3_x = p2.x() + landing
        leader_items.append(self.scene.addLine(p2.x(), p2.y(), p3_x, p2.y(), pen))

        t_item = self.scene.addText(text); t_item.setDefaultTextColor(disp_color); t_item.setFont(QFont("Meiryo", int(max(11, self.current_thickness * 4))))
        rect = t_item.boundingRect()
        t_item.setPos(p2.x() if landing > 0 else (p3_x - rect.width()), p2.y() - rect.height() + (rect.height() * 0.15))
        leader_items.append(t_item)

        group = self.scene.createItemGroup(leader_items)
        group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True); group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)

        shape_data = {"type": "leader", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "p3": (p3_x, p2.y()), "text": text, "font_size": int(max(11, self.current_thickness * 4)), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": group}
        if not next((s for s in self.shapes if s.get("item") == group), None): self.shapes.append(shape_data)
        return group

    def reverse_selected_arrows(self):
        selected = self.scene.selectedItems()
        if not selected: return
        self.start_history_record()
        for shape in self.shapes:
            if shape.get("item") in selected and shape.get("type") == "arrow":
                shape["p1"], shape["p2"] = shape["p2"], shape["p1"]
                self._update_arrow_shape_graphics(shape)
        self.commit_history_record()

    def change_selected_arrows_style(self, head_type=None, direction=None):
        selected = self.scene.selectedItems()
        if not selected: return
        self.start_history_record()
        for shape in self.shapes:
            if shape.get("item") in selected and shape.get("type") == "arrow":
                if head_type: shape["arrow_head_type"] = head_type
                if direction: shape["arrow_direction"] = direction
                self._update_arrow_shape_graphics(shape)
        self.commit_history_record()

    def prompt_arrow_settings(self):
        head_types = ["▲ 塗りつぶし (FILLED)", "＞ 開いた線 (OPEN)", "● 黒丸 (DOT)", "/ 建築用斜線 (SLASH)"]
        directions = ["終点のみ (END)", "始点のみ (START)", "両端 (BOTH)"]
        type_keys, dir_keys = ["FILLED", "OPEN", "DOT", "SLASH"], ["END", "START", "BOTH"]

        h_idx = type_keys.index(self.arrow_head_type) if self.arrow_head_type in type_keys else 0
        d_idx = dir_keys.index(self.arrow_direction) if self.arrow_direction in dir_keys else 0

        h_item, ok1 = QInputDialog.getItem(self, "矢印形状の設定", "矢印の頭部形状を選択:", head_types, h_idx, False)
        if not ok1: return
        d_item, ok2 = QInputDialog.getItem(self, "矢印向きの設定", "矢印の配置方向を選択:", directions, d_idx, False)
        if not ok2: return

        self.arrow_head_type = type_keys[head_types.index(h_item)]
        self.arrow_direction = dir_keys[directions.index(d_item)]
        if self.scene.selectedItems(): self.change_selected_arrows_style(head_type=self.arrow_head_type, direction=self.arrow_direction)

    def _draw_arrow_head_shape(self, pos, angle, head_type=None):
        if not head_type: head_type = self.arrow_head_type
        disp_color = self.get_display_color(self.current_color)
        size = max(10, self.current_thickness * 3.5)
        created_items = []

        if head_type == "FILLED":
            p_a1 = QPointF(pos.x() - size * math.cos(angle - math.pi / 6), pos.y() - size * math.sin(angle - math.pi / 6))
            p_a2 = QPointF(pos.x() - size * math.cos(angle + math.pi / 6), pos.y() - size * math.sin(angle + math.pi / 6))
            item = self.scene.addPolygon(QPolygonF([pos, p_a1, p_a2]), QPen(disp_color, 1), QBrush(disp_color))
            created_items.append(item)
        elif head_type == "OPEN":
            p_a1 = QPointF(pos.x() - size * math.cos(angle - math.pi / 6), pos.y() - size * math.sin(angle - math.pi / 6))
            p_a2 = QPointF(pos.x() - size * math.cos(angle + math.pi / 6), pos.y() - size * math.sin(angle + math.pi / 6))
            pen = QPen(disp_color, self.current_thickness, Qt.PenStyle.SolidLine)
            created_items.extend([self.scene.addLine(pos.x(), pos.y(), p_a1.x(), p_a1.y(), pen), self.scene.addLine(pos.x(), pos.y(), p_a2.x(), p_a2.y(), pen)])
        elif head_type == "DOT":
            r = size * 0.4
            item = self.scene.addEllipse(pos.x() - r, pos.y() - r, 2 * r, 2 * r, QPen(disp_color, 1), QBrush(disp_color))
            created_items.append(item)
        elif head_type == "SLASH":
            pen = QPen(disp_color, self.current_thickness, Qt.PenStyle.SolidLine)
            s_angle = angle + math.pi / 4
            p1 = QPointF(pos.x() - size * 0.6 * math.cos(s_angle), pos.y() - size * 0.6 * math.sin(s_angle))
            p2 = QPointF(pos.x() + size * 0.6 * math.cos(s_angle), pos.y() + size * 0.6 * math.sin(s_angle))
            created_items.append(self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen))

        return created_items

    def _update_arrow_shape_graphics(self, shape):
        if "head_items" in shape:
            for item in shape["head_items"]: self.safe_remove_item(item)
            shape["head_items"] = []

        if shape.get("item") and isinstance(shape["item"], QGraphicsLineItem):
            p1, p2 = shape["p1"], shape["p2"]
            shape["item"].setLine(p1[0], p1[1], p2[0], p2[1])

            angle_end = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
            angle_start = math.atan2(p1[1] - p2[1], p1[0] - p2[0])
            h_type, dir_val = shape.get("arrow_head_type", self.arrow_head_type), shape.get("arrow_direction", self.arrow_direction)

            head_items = []
            if dir_val in ["END", "BOTH"]: head_items.extend(self._draw_arrow_head_shape(QPointF(p2[0], p2[1]), angle_end, h_type))
            if dir_val in ["START", "BOTH"]: head_items.extend(self._draw_arrow_head_shape(QPointF(p1[0], p1[1]), angle_start, h_type))
            shape["head_items"] = head_items

    # --- アングルスナップ・スナップ判定 ---
    def set_grid_snap(self, enabled, size=None):
        self.grid_snap_enabled = enabled
        if size: self.grid_size = float(size)

    def set_angle_snap(self, enabled): self.angle_snap_enabled = enabled
    def set_otrack_enabled(self, enabled): self.otrack_enabled = enabled; self.clear_tracking_lines()

    def get_snap_points(self, current_pos=None):
        snaps = []
        if getattr(self, 'poly_points', None):
            for i, p_pt in enumerate(self.poly_points):
                snaps.append((p_pt[0], p_pt[1], "END"))
                if i > 0:
                    prev_pt = self.poly_points[i - 1]
                    snaps.append(((prev_pt[0] + p_pt[0]) / 2.0, (prev_pt[1] + p_pt[1]) / 2.0, "MID"))

        for shape in self.shapes:
            layer_props = self.layers.get(shape.get("layer", "0"), {})
            if not layer_props.get("visible", True): continue
            stype = shape.get("type")

            if stype in ["line", "dimension", "arrow", "leader"]:
                p1, p2 = shape["p1"], shape["p2"]
                snaps.extend([(p1[0], p1[1], "END"), (p2[0], p2[1], "END"), ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, "MID")])
            elif stype == "rect":
                x1, y1, x2, y2 = shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1]
                corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                for c in corners: snaps.append((c[0], c[1], "END"))
                for i in range(4):
                    p_a, p_b = corners[i], corners[(i + 1) % 4]
                    snaps.append(((p_a[0] + p_b[0]) / 2, (p_a[1] + p_b[1]) / 2, "MID"))
                snaps.append(((x1 + x2) / 2, (y1 + y2) / 2, "CENTER"))
            elif stype in ["circle", "arc"]: snaps.append((shape["center"][0], shape["center"][1], "CENTER"))
            elif stype in ["polyline", "spline"]:
                pts = shape["points"]
                for p in pts: snaps.append((p[0], p[1], "END"))
                for i in range(len(pts) - 1):
                    snaps.append(((pts[i][0] + pts[i+1][0]) / 2, (pts[i][1] + pts[i+1][1]) / 2, "MID"))

        return snaps

    def get_snapped_pos(self, raw_pos):
        self.clear_tracking_lines()
        if self.mode == "SELECT": return raw_pos

        view_scale = self.transform().m11()
        dynamic_threshold = self.snap_threshold / max(0.001, view_scale)

        if self.grid_snap_enabled and self.grid_size > 0:
            gx, gy = round(raw_pos.x() / self.grid_size) * self.grid_size, round(raw_pos.y() / self.grid_size) * self.grid_size
            grid_pt = QPointF(gx, gy)
            snaps = self.get_snap_points(raw_pos)
            if not any(math.hypot(raw_pos.x() - x, raw_pos.y() - y) < dynamic_threshold for x, y, _ in snaps):
                self.update_snap_marker(grid_pt, "GRID")
                return grid_pt

        snaps = self.get_snap_points(raw_pos)
        best_pt, best_type, min_dist = raw_pos, None, float("inf")

        for x, y, stype in snaps:
            dist = math.hypot(raw_pos.x() - x, raw_pos.y() - y)
            effective_threshold = dynamic_threshold * 1.5 if stype == "END" else dynamic_threshold
            if dist < effective_threshold:
                weighted_dist = dist * 0.6 if stype == "END" else dist
                if weighted_dist < min_dist:
                    min_dist, best_pt, best_type = weighted_dist, QPointF(x, y), stype

        if min_dist < float("inf"):
            self.update_snap_marker(best_pt, best_type)
            return best_pt

        self.update_snap_marker(None, None)
        return raw_pos

    def update_snap_marker(self, pos, stype):
        if self.snap_marker: self.safe_remove_item(self.snap_marker); self.snap_marker = None
        if pos:
            size, colors = 10, {"END": Qt.GlobalColor.red, "MID": Qt.GlobalColor.yellow, "CENTER": Qt.GlobalColor.blue, "INTER": Qt.GlobalColor.cyan, "GRID": Qt.GlobalColor.magenta}
            pen = QPen(colors.get(stype, Qt.GlobalColor.green), 2)
            self.snap_marker = self.scene.addRect(pos.x() - size/2, pos.y() - size/2, size, size, pen)
            self.snap_marker.setZValue(100)

    def clear_tracking_lines(self):
        for item in self.tracking_items: self.safe_remove_item(item)
        self.tracking_items.clear()

    # --- ヘルパー制御関数 ---
    def apply_angle_snap(self, p1, p2):
        if not self.angle_snap_enabled or not p1: return p2
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y(); dist = math.hypot(dx, dy)
        if dist < 1e-4: return p2
        snapped_deg = round(math.degrees(math.atan2(dy, dx)) / 15.0) * 15.0
        return QPointF(p1.x() + dist * math.cos(math.radians(snapped_deg)), p1.y() + dist * math.sin(math.radians(snapped_deg)))

    def finish_multi_point_mode(self):
        self.click_points.clear(); self.poly_points.clear()
        [self.safe_remove_item(item) for item in self.poly_temp_items]
        self.poly_temp_items.clear(); self.update_snap_marker(None, None)
        if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None
        self.start_point = None

    def finish_polyline(self):
        self.start_history_record()
        if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None
        if len(self.poly_points) > 1:
            pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
            is_closed = (self.mode == "POLYGON")
            path = QPainterPath(); path.moveTo(QPointF(self.poly_points[0][0], self.poly_points[0][1]))
            [path.lineTo(QPointF(p[0], p[1])) for p in self.poly_points[1:]]
            if is_closed: path.closeSubpath()
            item = self.scene.addPath(path, pen)
            [self.safe_remove_item(tmp) for tmp in self.poly_temp_items]
            self.shapes.append({"type": "polyline", "points": list(self.poly_points), "is_closed": is_closed, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
        elif len(self.poly_points) == 1: [self.safe_remove_item(item) for item in self.poly_temp_items]
        self.poly_points.clear(); self.poly_temp_items.clear(); self.commit_history_record()

    def set_mode(self, mode):
        self.finish_multi_point_mode()
        if mode in ["ROTATE", "ROTATE_COPY", "SCALE", "SCALE_COPY", "MIRROR", "MIRROR_COPY", "OFFSET", "ARRAY"]:
            self.mode = mode; self.execute_edit_command(); self.set_mode("SELECT"); return
        elif mode == "HATCH": self.apply_hatching(); self.set_mode("SELECT"); return
        elif mode == "CLOUD_OBJECT": self.convert_selected_to_cloud(); self.set_mode("SELECT"); return

        self.mode = mode; self.mode_changed.emit(mode)
        is_select = (mode == "SELECT")
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if is_select else QGraphicsView.DragMode.NoDrag)
        for item in self.scene.items():
            if item not in (self.paper_guide_item, getattr(self, 'custom_print_rect_item', None)):
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_select)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, is_select)

    def set_color(self, color): self.current_color = color; self.apply_property_to_selected(color=color)
    def set_thickness(self, thickness): self.current_thickness = thickness; self.apply_property_to_selected(thickness=thickness)
    def set_style(self, style): self.current_style = style; self.apply_property_to_selected(style=style)

    def apply_property_to_selected(self, color=None, thickness=None, style=None):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        self.start_history_record()
        for shape in self.shapes:
            if shape.get("item") in selected_items:
                if color is not None: shape["color"] = color
                if thickness is not None: shape["thickness"] = thickness
                if style is not None: shape["style"] = style
        self.apply_layer_states(); self.commit_history_record()

    def get_display_color(self, raw_color, is_export=False):
        if not is_export and self.is_dark_mode and raw_color.red() < 50 and raw_color.green() < 50 and raw_color.blue() < 50:
            return QColor(255, 255, 255)
        return raw_color

    def apply_layer_states(self, is_export=False):
        for shape in list(self.shapes):
            item = shape.get("item")
            if not item or item.scene() != self.scene: continue
            props = self.layers.get(shape.get("layer", "0"), self.layers["0"])
            item.setVisible(props["visible"] and (props["printable"] if is_export else True))
            is_movable = (self.mode == "SELECT" and not props["locked"])
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_movable)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, is_movable)

            disp_color = self.get_display_color(shape.get("color", props["color"]), is_export=is_export)
            if hasattr(item, "pen") and hasattr(item, "setPen"):
                pen = item.pen(); pen.setColor(disp_color); pen.setWidth(shape.get("thickness", props["thickness"])); pen.setStyle(shape.get("style", props["style"])); item.setPen(pen)
            elif hasattr(item, "setDefaultTextColor"): item.setDefaultTextColor(disp_color)

    def start_history_record(self): self._before_items, self._before_shapes_len = set(self.scene.items()), len(self.shapes)

    def commit_history_record(self):
        added_items = [i for i in (set(self.scene.items()) - self._before_items) if i not in (self.temp_item, self.snap_marker, self.paper_guide_item, getattr(self, 'custom_print_rect_item', None)) and i not in self.poly_temp_items]
        added_shapes = self.shapes[self._before_shapes_len:]
        if added_items or added_shapes: self.undo_stack.append(("add", added_items, added_shapes)); self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack: return
        action = self.undo_stack.pop()
        if action[0] == "add":
            [self.safe_remove_item(item) for item in action[1]]
            [self.shapes.remove(s) for s in action[2] if s in self.shapes]
        self.redo_stack.append(action)

    def redo(self):
        if not self.redo_stack: return
        action = self.redo_stack.pop()
        if action[0] == "add":
            [self.scene.addItem(item) for item in action[1]]
            [self.shapes.append(s) for s in action[2]]
        self.undo_stack.append(action)

    def bring_selected_to_front(self): [i.setZValue(max([item.zValue() for item in self.scene.items()] or [0]) + 1) for i in self.scene.selectedItems()]
    def send_selected_to_back(self): [i.setZValue(min([item.zValue() for item in self.scene.items()] or [0]) - 1) for i in self.scene.selectedItems()]

    def zoom_in(self): self.scale(self.zoom_factor, self.zoom_factor)
    def zoom_out(self): self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
    def zoom_fit(self):
        rect = self.scene.itemsBoundingRect()
        if not rect.isEmpty(): self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        else: self.resetTransform()

    # --- 右クリックコンテキストメニュー ---
    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        menu = QMenu(self)

        if item and item not in (self.paper_guide_item, getattr(self, 'custom_print_rect_item', None)):
            if not item.isSelected():
                self.scene.clearSelection()
                item.setSelected(True)

        selected = self.scene.selectedItems()
        if selected:
            table_shape = next((s for s in self.shapes if s.get("type") == "table" and (s.get("item") in selected or any(isinstance(s.get("item"), QGraphicsItemGroup) and child in selected for child in s.get("item").childItems()))), None)
            text_shape = next((s for s in self.shapes if s.get("type") == "text" and s.get("item") in selected), None)

            if table_shape: menu.addAction("📊 表を再編集...", lambda: self.edit_table_shape(table_shape)); menu.addSeparator()
            if text_shape: menu.addAction("📝 文字を再編集...", lambda: self.edit_text_shape(text_shape)); menu.addSeparator()

            prop_menu = menu.addMenu("⚙️ プロパティ / レイヤー変更")
            prop_menu.addAction("🏷️ レイヤーを変更...", self.prompt_change_selected_layer)
            prop_menu.addAction("🎨 選択図形の色を個別に変更...", self.prompt_change_selected_color)
            prop_menu.addAction("➖ 選択図形の線種・太さを個別に変更...", self.prompt_change_selected_style)
            menu.addSeparator()

            menu.addAction("📋 複製 (Duplicate)", self.duplicate_selected)
            menu.addAction("✂ コピー (Ctrl+C)", self.copy_selected_to_clipboard)
            menu.addAction("🗑 削除 (Delete)", self.delete_selected)
            menu.addSeparator()

            has_arrow = any(s.get("item") in selected and s.get("type") == "arrow" for s in self.shapes)
            if has_arrow:
                arrow_menu = menu.addMenu("🏹 矢印のクイック操作")
                arrow_menu.addAction("🔃 向きを反転", self.reverse_selected_arrows)
                arrow_menu.addAction("終点に矢印 (標準)", lambda: self.change_selected_arrows_style(direction="END"))
                arrow_menu.addAction("始点に矢印", lambda: self.change_selected_arrows_style(direction="START"))
                arrow_menu.addAction("両端に矢印", lambda: self.change_selected_arrows_style(direction="BOTH"))
                arrow_menu.addSeparator()
                arrow_menu.addAction("▲ 塗りつぶし矢印", lambda: self.change_selected_arrows_style(head_type="FILLED"))
                arrow_menu.addAction("＞ 開いた線矢印", lambda: self.change_selected_arrows_style(head_type="OPEN"))
                arrow_menu.addAction("● 黒丸 (ドット)", lambda: self.change_selected_arrows_style(head_type="DOT"))
                arrow_menu.addAction("/ 建築用斜線 (スラッシュ)", lambda: self.change_selected_arrows_style(head_type="SLASH"))

            menu.addSeparator()
            menu.addAction("↔️ 平行線 (オフセット距離指定)...", self.prompt_offset_settings)
            menu.addAction("↕️ X軸/Y軸ピッチ平行線...", self.prompt_xy_offset_settings)
            menu.addSeparator()

            menu.addAction("🔄 90度回転", lambda: self.set_mode("ROTATE"))
            menu.addAction("🔄 90度回転コピー", lambda: self.set_mode("ROTATE_COPY"))
            menu.addAction("🪞 左右反転", lambda: self.set_mode("MIRROR"))
            menu.addAction("🪞 左右反転コピー", lambda: self.set_mode("MIRROR_COPY"))
            
            menu.addSeparator()
            menu.addAction("🎨 ハッチング適用 (HATCH)", self.apply_hatching)
            menu.addAction("☁️ 雲マークに変換", self.convert_selected_to_cloud)
            
            menu.addSeparator()
            menu.addAction("⬆ 最前面へ移動", self.bring_selected_to_front)
            menu.addAction("⬇ 最背面へ移動", self.send_selected_to_back)
            
            menu.addSeparator()
            menu.addAction("🔗 線の結合 (JOIN)", self.join_selected_lines)
            menu.addAction("🎯 中心線生成", self.generate_centerlines)
            
            menu.addSeparator()
            menu.addAction("📦 グループ化", self.group_selected_items)
            menu.addAction("💥 グループ解除", self.ungroup_selected_items)
            
            has_image = any(isinstance(i, (QGraphicsPixmapItem, CustomPixmapItem)) for i in selected)
            if has_image:
                menu.addSeparator()
                menu.addAction("🌫️ 透明度を変更...", self.set_selected_image_opacity)

        else:
            menu.addAction("📏 水平・垂直線の長さ指定...", self.prompt_line_length_settings)
            menu.addAction("🏹 矢印の設定 (形状・向き)...", self.prompt_arrow_settings)
            menu.addSeparator()
            menu.addAction("⚙️ レイヤー設定 (線種・色) を一括変更...", self.prompt_edit_layer_settings)
            menu.addSeparator()

            if self.copied_shapes_buffer: 
                menu.addAction("📋 ペースト (Ctrl+V)", self.paste_from_clipboard)
                menu.addSeparator()
            
            menu.addAction("☑ すべて選択 (Ctrl+A)", lambda: [i.setSelected(True) for i in self.scene.items() if i not in (self.paper_guide_item, getattr(self, 'custom_print_rect_item', None))])
            menu.addAction("📄 DXFファイルを読み込む...", lambda: self.import_dxf_file())
            menu.addAction("📄 キャンバスクリア (新規)", self.new_project)

        menu.exec(event.globalPos())

    # --- 境界線抽出・ハッチング機能 ---
    def _extract_boundaries_from_items(self, items):
        lines = []
        for item in items:
            shape = next((s for s in self.shapes if s.get("item") == item), None)
            stype = shape.get("type") if shape else None

            if isinstance(item, QGraphicsLineItem) or stype in ["line", "arrow", "leader"]:
                if shape and "p1" in shape and "p2" in shape: lines.append(LineString([shape["p1"], shape["p2"]]))
                else:
                    line = item.line(); p1, p2 = item.mapToScene(line.p1()), item.mapToScene(line.p2())
                    lines.append(LineString([(p1.x(), p1.y()), (p2.x(), p2.y())]))
            elif isinstance(item, QGraphicsRectItem) or stype == "rect":
                rect = item.sceneTransform().mapRect(item.boundingRect()); x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
                lines.append(LineString([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]))
            elif isinstance(item, QGraphicsEllipseItem) or stype in ["circle", "arc"]:
                rect = item.sceneTransform().mapRect(item.boundingRect()); cx, cy, rx, ry = rect.center().x(), rect.center().y(), rect.width() / 2.0, rect.height() / 2.0
                pts = [(cx + rx * math.cos(2 * math.pi * i / 64), cy + ry * math.sin(2 * math.pi * i / 64)) for i in range(65)]
                lines.append(LineString(pts))
            elif isinstance(item, QGraphicsPolygonItem) or (shape and shape.get("type") in ["polyline", "spline"]):
                if shape and "points" in shape:
                    pts = shape["points"]; pts = pts + [pts[0]] if shape.get("is_closed") and len(pts) >= 3 else pts
                    if len(pts) >= 2: lines.append(LineString(pts))
            elif isinstance(item, QGraphicsItemGroup): lines.extend(self._extract_boundaries_from_items(item.childItems()))
        return lines

    def _item_to_shapely_polygon(self, item):
        rect = item.sceneTransform().mapRect(item.boundingRect()); x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        if w <= 0 or h <= 0: return None
        if isinstance(item, QGraphicsEllipseItem):
            cx, cy, rx, ry = rect.center().x(), rect.center().y(), w / 2.0, h / 2.0
            return Polygon([(cx + rx * math.cos(2 * math.pi * i / 64), cy + ry * math.sin(2 * math.pi * i / 64)) for i in range(64)])
        elif isinstance(item, QGraphicsPolygonItem): return Polygon([(pt.x(), pt.y()) for pt in item.polygon()])
        elif isinstance(item, QGraphicsRectItem): return Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
        return Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])

    def apply_hatching(self):
        selected = self.scene.selectedItems()
        if not selected: QMessageBox.warning(self, "通知", "ハッチングを行うオブジェクトを選択してください。"); return

        boundary_lines = self._extract_boundaries_from_items(selected)
        polygonized_polygons = []
        if boundary_lines:
            try:
                for p in list(polygonize(unary_union(boundary_lines))):
                    if p.is_valid and not p.is_empty and p.area > 1e-3: polygonized_polygons.append(p)
            except Exception: pass

        single_polygons = [self._item_to_shapely_polygon(item) for item in selected if self._item_to_shapely_polygon(item)]
        polygons = polygonized_polygons if polygonized_polygons else single_polygons

        if not polygons: QMessageBox.warning(self, "エラー", "交点や端点で囲まれた閉領域が見つかりませんでした。"); return

        target_geom = None
        if len(polygons) == 1: target_geom = polygons[0]
        else:
            options = ["1. 交点・端点で囲まれたすべての閉領域にハッチング", "2. 選択オブジェクト個別にハッチング", "3. 外枠から内側を除外（中抜きハッチング）"]
            item_str, ok = QInputDialog.getItem(self, "ハッチング範囲の指定", "ハッチングの作図範囲を選択してください:", options, 0, False)
            if not ok: return
            if item_str.startswith("1"): target_geom = MultiPolygon(polygons)
            elif item_str.startswith("2"): target_geom = MultiPolygon(single_polygons if single_polygons else polygons)
            elif item_str.startswith("3"):
                p_list = single_polygons if single_polygons else polygons; p_list.sort(key=lambda p: p.area, reverse=True)
                outer = p_list[0]
                for inner in p_list[1:]: outer = outer.difference(inner)
                target_geom = outer

        if target_geom is None or target_geom.is_empty: return
        self.start_history_record(); pen = QPen(self.get_display_color(self.hatch_color), self.hatch_thickness, self.hatch_style)

        minx, miny, maxx, maxy = target_geom.bounds
        cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
        diag = math.hypot(maxx - minx, maxy - miny) * 2.0
        rad, spacing = math.radians(self.hatch_angle), max(2.0, self.hatch_spacing)
        num_lines = int(diag / spacing) + 1

        for i in range(-num_lines, num_lines + 1):
            offset = i * spacing
            rx1 = cx + (-diag) * math.cos(rad) - offset * math.sin(rad)
            ry1 = cy + (-diag) * math.sin(rad) + offset * math.cos(rad)
            rx2 = cx + diag * math.cos(rad) - offset * math.sin(rad)
            ry2 = cy + diag * math.sin(rad) + offset * math.cos(rad)

            try:
                inter = target_geom.intersection(LineString([(rx1, ry1), (rx2, ry2)]))
                if not inter.is_empty:
                    geoms = [inter] if isinstance(inter, LineString) else (list(inter.geoms) if hasattr(inter, 'geoms') else [])
                    for g in geoms:
                        if isinstance(g, LineString):
                            coords = list(g.coords)
                            h_item = self.scene.addLine(coords[0][0], coords[0][1], coords[-1][0], coords[-1][1], pen)
                            self.shapes.append({"type": "line", "p1": coords[0], "p2": coords[-1], "layer": self.active_layer, "color": self.hatch_color, "item": h_item})
            except Exception: pass

        self.commit_history_record()

    # --- マウスイベント（作図と操作） ---
    def wheelEvent(self, event):
        if event.angleDelta().y() > 0: self.zoom_in()
        else: self.zoom_out()

    def mousePressEvent(self, event):
        self.start_history_record()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True; self._pan_start = event.pos(); self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.commit_history_record(); return

        if event.button() == Qt.MouseButton.RightButton:
            if self.mode in ["POLYLINE", "POLYGON", "SPLINE"] and len(self.poly_points) > 0:
                self.finish_polyline(); self.commit_history_record(); return
            elif self.mode != "SELECT":
                self.set_mode("SELECT"); self.commit_history_record(); return
            else:
                super().mousePressEvent(event); return

        raw_pos = self.mapToScene(event.pos())
        pos = self.get_snapped_pos(raw_pos)

        if self.mode == "REG_POLYGON" and event.button() == Qt.MouseButton.LeftButton:
            if self.start_point is None:
                self.start_point = pos
            else:
                cx, cy = self.start_point.x(), self.start_point.y()
                radius = math.hypot(pos.x() - cx, pos.y() - cy)
                angle_deg = math.degrees(math.atan2(pos.y() - cy, pos.x() - cx))
                pts = self._calc_regular_polygon_points_by_angle(cx, cy, radius, self.preset_poly_sides, angle_deg)
                disp_color = self.get_display_color(self.current_color)
                pen = QPen(disp_color, self.current_thickness, self.current_style)
                item = self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in pts]), pen, QBrush(Qt.BrushStyle.NoBrush))
                self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
                self.start_point = None
                self.set_mode("SELECT")
                self.commit_history_record()
            return

        elif self.mode == "POINT" and event.button() == Qt.MouseButton.LeftButton:
            disp_color = self.get_display_color(self.current_color)
            pen = QPen(disp_color, self.current_thickness, self.current_style)
            r = max(2.0, self.current_thickness)
            item = self.scene.addEllipse(pos.x() - r, pos.y() - r, 2 * r, 2 * r, pen, QBrush(disp_color))
            self.shapes.append({"type": "point", "pos": (pos.x(), pos.y()), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
            self.commit_history_record(); return

        elif self.mode == "TEXT" and event.button() == Qt.MouseButton.LeftButton:
            dlg = TextEditDialog(self, font_size=max(11, int(self.current_thickness * 4)), color=self.current_color)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                input_txt, font_size, text_color = dlg.get_result()
                if input_txt.strip():
                    disp_color = self.get_display_color(text_color)
                    t_item = self.scene.addText(input_txt.strip())
                    t_item.setDefaultTextColor(disp_color)
                    t_item.setFont(QFont("Meiryo", int(font_size)))
                    t_item.setPos(pos)
                    self.shapes.append({"type": "text", "text": input_txt.strip(), "pos": (pos.x(), pos.y()), "font_size": font_size, "layer": self.active_layer, "color": text_color, "thickness": self.current_thickness, "style": self.current_style, "item": t_item})
            self.set_mode("SELECT")
            self.commit_history_record(); return

        elif self.mode in ["PRESET_RECT", "PRESET_CIRCLE", "PRESET_ARC", "PRESET_POLYGON"] and event.button() == Qt.MouseButton.LeftButton:
            disp_color = self.get_display_color(self.current_color)
            pen = QPen(disp_color, self.current_thickness, self.current_style)
            cx, cy = pos.x(), pos.y()
            if self.mode == "PRESET_RECT":
                w, h = self.preset_rect_w, self.preset_rect_h
                item = self.scene.addRect(cx - w / 2, cy - h / 2, w, h, pen)
                self.shapes.append({"type": "rect", "p1": (cx - w/2, cy - h/2), "p2": (cx + w/2, cy + h/2), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
            elif self.mode == "PRESET_CIRCLE":
                r = self.preset_circle_r
                item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
                self.shapes.append({"type": "circle", "center": (cx, cy), "radius": r, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
            elif self.mode == "PRESET_ARC":
                r, st, sp = self.preset_arc_r, self.preset_arc_start, self.preset_arc_span
                path = QPainterPath(); path.arcMoveTo(cx - r, cy - r, 2 * r, 2 * r, st); path.arcTo(cx - r, cy - r, 2 * r, 2 * r, st, sp)
                item = self.scene.addPath(path, pen)
                self.shapes.append({"type": "arc", "center": (cx, cy), "radius": r, "start_angle": st, "span_angle": sp, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
            elif self.mode == "PRESET_POLYGON":
                pts = self._calc_regular_polygon_points_by_angle(cx, cy, self.preset_poly_r, self.preset_poly_sides, self.preset_poly_angle)
                item = self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in pts]), pen, QBrush(Qt.BrushStyle.NoBrush))
                self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
            self.set_mode("SELECT")
            self.commit_history_record(); return

        elif self.mode in ["CONCENTRIC_CIRCLE", "CONCENTRIC_RECT", "CONCENTRIC_POLYGON"] and event.button() == Qt.MouseButton.LeftButton:
            stype = "CIRCLE" if self.mode == "CONCENTRIC_CIRCLE" else ("RECT" if self.mode == "CONCENTRIC_RECT" else "POLYGON")
            self.generate_concentric_shapes(stype, 20.0, 20.0, False, 4, center=pos)
            self.set_mode("SELECT")
            self.commit_history_record(); return

        elif self.mode == "FILLET" and event.button() == Qt.MouseButton.LeftButton:
            self.execute_fillet_chamfer(pos)
            self.commit_history_record(); return

        elif self.mode in ["MOVE", "COPY"] and event.button() == Qt.MouseButton.LeftButton:
            if not self.scene.selectedItems():
                QMessageBox.warning(self, "通知", "対象のオブジェクトを選択してください。")
                self.set_mode("SELECT")
            elif getattr(self, 'move_base_pt', None) is None: self.move_base_pt = pos
            else:
                if self.mode == "MOVE": self.execute_move_selected(self.move_base_pt, pos)
                elif self.mode == "COPY": self.execute_copy_selected(self.move_base_pt, pos)
                self.move_base_pt = None
            self.commit_history_record(); return

        elif self.mode == "DIM_ANGLE" and event.button() == Qt.MouseButton.LeftButton:
            click_pt = Point(pos.x(), pos.y())
            target_shape = next((s for s in self.shapes if s.get("type") == "line" and LineString([s["p1"], s["p2"]]).distance(click_pt) < 15.0), None)
            if target_shape:
                if not self.angle_dim_first_line:
                    self.angle_dim_first_line = target_shape
                    QMessageBox.information(self, "ガイド", "2本目の直線を選択してください。")
                else:
                    self.create_angle_dimension(self.angle_dim_first_line, target_shape)
                    self.set_mode("SELECT")
            self.commit_history_record(); return

        elif self.mode == "TRACE_CALIBRATE" and event.button() == Qt.MouseButton.LeftButton:
            self.click_points.append(pos)
            if len(self.click_points) == 2:
                p1, p2 = self.click_points
                current_dist = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
                if current_dist > 0:
                    real_dist, ok = QInputDialog.getDouble(self, "下絵の縮尺合わせ", "2点間の実際の寸法（mm）:", current_dist, 0.1, 999999.0, 1)
                    if ok and real_dist > 0 and self.calibrate_target_item:
                        scale_factor = real_dist / current_dist
                        self.calibrate_target_item.setTransformOriginPoint(self.calibrate_target_item.boundingRect().topLeft())
                        self.calibrate_target_item.setScale(self.calibrate_target_item.scale() * scale_factor)
                        QMessageBox.information(self, "完了", f"実寸に自動補正しました（{scale_factor:.4f}倍）。")
                self.set_mode("SELECT")
            self.commit_history_record(); return

        elif self.mode in ["AUTO_TRACE", "PRINT_AREA", "TRIM"] and event.button() == Qt.MouseButton.LeftButton:
            if self.mode == "TRIM": self.trim_start_pos = raw_pos
            else: self.start_point = pos
            self.commit_history_record(); return

        elif self.mode == "SELECT" and event.button() == Qt.MouseButton.LeftButton:
            super().mousePressEvent(event); self.commit_history_record(); return

        if event.button() == Qt.MouseButton.LeftButton:
            disp_color = self.get_display_color(self.current_color)
            pen = QPen(disp_color, self.current_thickness, self.current_style)

            if self.mode in ["LINE", "H_LINE", "V_LINE", "RECT", "CIRCLE", "CIRCLE_2P", "CIRCLE_3P", "ELLIPSE", "DIMENSION", "DIM_RADIUS", "DIM_DIAMETER", "LEADER", "CLOUD"]:
                self.start_point = pos

            elif self.mode == "ARROW":
                self.start_point = pos

            elif self.mode in ["ARC_3P", "ARC"]:
                self.click_points.append(pos)
                if len(self.click_points) == 3:
                    if self.mode == "ARC_3P": self.create_3pt_arc()
                    elif self.mode == "ARC": self.create_center_arc()

            elif self.mode in ["POLYLINE", "POLYGON", "SPLINE"]:
                last_pt = QPointF(self.poly_points[-1][0], self.poly_points[-1][1]) if self.poly_points else None
                pos_angled = self.apply_angle_snap(last_pt, raw_pos) if last_pt else raw_pos
                p = self.get_snapped_pos(pos_angled)

                if len(self.poly_points) >= 2:
                    start_p = QPointF(self.poly_points[0][0], self.poly_points[0][1])
                    view_scale = self.transform().m11()
                    snap_pixel_thresh = (self.snap_threshold * 1.5) / max(0.001, view_scale)
                    if math.hypot(p.x() - start_p.x(), p.y() - start_p.y()) <= snap_pixel_thresh:
                        if self.mode == "POLYGON": self.finish_polyline()
                        else: self.poly_points.append((start_p.x(), start_p.y())); self.finish_polyline()
                        self.commit_history_record()
                        return

                self.poly_points.append((p.x(), p.y()))
                if len(self.poly_points) > 1:
                    p1, p2 = self.poly_points[-2], self.poly_points[-1]
                    self.poly_temp_items.append(self.scene.addLine(p1[0], p1[1], p2[0], p2[1], pen))

        self.commit_history_record()

    def mouseMoveEvent(self, event):
        if self._is_panning:
            delta = event.pos() - self._pan_start; self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return

        raw_pos = self.mapToScene(event.pos())
        pos_angled = self.apply_angle_snap(self.start_point, raw_pos) if getattr(self, 'start_point', None) else raw_pos
        current_pos = self.get_snapped_pos(pos_angled)

        if self.mode == "REG_POLYGON" and getattr(self, 'start_point', None):
            if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None
            cx, cy = self.start_point.x(), self.start_point.y()
            radius = math.hypot(current_pos.x() - cx, current_pos.y() - cy)
            angle_deg = math.degrees(math.atan2(current_pos.y() - cy, current_pos.x() - cx))
            pts = self._calc_regular_polygon_points_by_angle(cx, cy, radius, self.preset_poly_sides, angle_deg)
            disp_color = self.get_display_color(self.current_color)
            pen_preview = QPen(disp_color, self.current_thickness, Qt.PenStyle.DashLine)
            self.temp_item = self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in pts]), pen_preview, QBrush(Qt.BrushStyle.NoBrush))

        elif self.mode == "TRIM":
            self.clear_trim_preview()
            is_shift = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
            if getattr(self, 'trim_start_pos', None) and event.buttons() & Qt.MouseButton.LeftButton:
                pen_fence = QPen(QColor(255, 0, 0), 2, Qt.PenStyle.DashDotLine)
                self.trim_preview_item = self.scene.addLine(self.trim_start_pos.x(), self.trim_start_pos.y(), raw_pos.x(), raw_pos.y(), pen_fence)
            elif is_shift:
                old_s, new_s = self._find_extend_target(raw_pos)
                if new_s:
                    pen_ext = QPen(QColor(0, 120, 255), max(4, self.current_thickness + 2), Qt.PenStyle.SolidLine)
                    c = new_s["p1"], new_s["p2"] if new_s["type"] == "line" else new_s["points"]
                    self.trim_preview_item = self.scene.addLine(c[0][0], c[0][1], c[-1][0], c[-1][1], pen_ext)
            else:
                trim_seg, _, _ = self._find_trim_target_and_split(raw_pos)
                if trim_seg:
                    pen_hl = QPen(QColor(255, 50, 50), max(4, self.current_thickness + 2), Qt.PenStyle.SolidLine)
                    c = list(trim_seg.coords)
                    self.trim_preview_item = self.scene.addLine(c[0][0], c[0][1], c[-1][0], c[-1][1], pen_hl)

        elif self.mode in ["MOVE", "COPY"] and getattr(self, 'move_base_pt', None):
            self.clear_trim_preview()
            pen_preview = QPen(QColor(0, 150, 255), 2, Qt.PenStyle.DashLine)
            self.trim_preview_item = self.scene.addLine(self.move_base_pt.x(), self.move_base_pt.y(), current_pos.x(), current_pos.y(), pen_preview)

        elif self.mode in ["PRINT_AREA", "AUTO_TRACE"] and getattr(self, 'start_point', None):
            if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None
            x1, y1, x2, y2 = self.start_point.x(), self.start_point.y(), current_pos.x(), current_pos.y()
            pen_color = QColor(0, 255, 100) if self.mode == "AUTO_TRACE" else QColor(255, 102, 0)
            self.temp_item = self.scene.addRect(QRectF(QPointF(x1, y1), QPointF(x2, y2)), QPen(pen_color, 2, Qt.PenStyle.DashLine))

        if self.mode == "SELECT": super().mouseMoveEvent(event); return

        disp_color = self.get_display_color(self.current_color)
        pen_preview = QPen(disp_color, self.current_thickness, self.current_style)
        if self.temp_item and self.mode not in ["PRINT_AREA", "AUTO_TRACE", "REG_POLYGON"]: self.safe_remove_item(self.temp_item); self.temp_item = None

        if getattr(self, 'start_point', None) and self.mode not in ["PRINT_AREA", "AUTO_TRACE", "REG_POLYGON"]:
            x1, y1, x2, y2 = self.start_point.x(), self.start_point.y(), current_pos.x(), current_pos.y()
            if self.mode in ["LINE", "LEADER", "DIMENSION", "DIM_RADIUS", "DIM_DIAMETER", "CLOUD", "ARROW"]:
                self.temp_item = self.scene.addLine(x1, y1, x2, y2, pen_preview)
            elif self.mode == "H_LINE":
                if self.preset_line_length > 0:
                    l_val = self.preset_line_length if x2 >= x1 else -self.preset_line_length
                    self.temp_item = self.scene.addLine(x1, y1, x1 + l_val, y1, pen_preview)
                else:
                    self.temp_item = self.scene.addLine(x1 - 5000, y1, x1 + 5000, y1, pen_preview)
            elif self.mode == "V_LINE":
                if self.preset_line_length > 0:
                    l_val = self.preset_line_length if y2 >= y1 else -self.preset_line_length
                    self.temp_item = self.scene.addLine(x1, y1, x1, y1 + l_val, pen_preview)
                else:
                    self.temp_item = self.scene.addLine(x1, y1 - 5000, x1, y1 + 5000, pen_preview)
            elif self.mode in ["RECT", "ELLIPSE"]:
                self.temp_item = self.scene.addRect(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2), pen_preview)
            elif self.mode in ["CIRCLE", "CIRCLE_2P"]:
                r = math.hypot(x2 - x1, y2 - y1)
                self.temp_item = self.scene.addEllipse(x1 - r, y1 - r, 2 * r, 2 * r, pen_preview)

        elif self.mode in ["POLYLINE", "POLYGON", "SPLINE"] and len(self.poly_points) > 0:
            last_pt = QPointF(self.poly_points[-1][0], self.poly_points[-1][1])
            c_pos = self.get_snapped_pos(self.apply_angle_snap(last_pt, raw_pos))
            self.temp_item = self.scene.addLine(last_pt.x(), last_pt.y(), c_pos.x(), c_pos.y(), pen_preview)

    def mouseReleaseEvent(self, event):
        self.start_history_record()
        if event.button() == Qt.MouseButton.MiddleButton and self._is_panning:
            self._is_panning = False; self.setCursor(Qt.CursorShape.ArrowCursor)
            self.commit_history_record(); return

        raw_pos = self.mapToScene(event.pos())

        if self.mode == "TRIM" and event.button() == Qt.MouseButton.LeftButton:
            self.clear_trim_preview()
            sp = getattr(self, 'trim_start_pos', None)
            self.trim_start_pos = None
            is_shift = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)

            if is_shift:
                old_s, new_s = self._find_extend_target(raw_pos)
                if old_s and new_s and old_s in self.shapes:
                    self.shapes[self.shapes.index(old_s)] = new_s
                    self.apply_layer_states()
            else:
                trim_seg, remaining_segs, target_shape = self._find_trim_target_and_split(raw_pos)
                if target_shape and target_shape in self.shapes:
                    self.shapes.remove(target_shape)
                    pen = QPen(self.get_display_color(target_shape.get("color", self.current_color)), self.current_thickness, self.current_style)
                    for seg in remaining_segs:
                        c = list(seg.coords)
                        item = self.scene.addLine(c[0][0], c[0][1], c[-1][0], c[-1][1], pen)
                        self.shapes.append({"type": "line", "p1": c[0], "p2": c[-1], "layer": target_shape.get("layer", self.active_layer), "color": target_shape.get("color", self.current_color), "thickness": self.current_thickness, "style": self.current_style, "item": item})
                    self.apply_layer_states()
            self.commit_history_record(); return

        elif self.mode == "AUTO_TRACE" and getattr(self, 'start_point', None):
            rect = QRectF(self.start_point, self.get_snapped_pos(raw_pos)).normalized()
            if rect.width() > 10 and rect.height() > 10: self.auto_trace_region(rect)
            self.start_point = None; self.set_mode("SELECT")
            self.commit_history_record(); return

        elif self.mode == "PRINT_AREA" and getattr(self, 'start_point', None):
            rect = QRectF(self.start_point, self.get_snapped_pos(raw_pos)).normalized()
            if rect.width() > 10 and rect.height() > 10: self.set_custom_print_rect(rect)
            self.start_point = None; self.set_mode("SELECT")
            self.commit_history_record(); return

        if self.mode in ["SELECT", "REG_POLYGON"] or event.button() != Qt.MouseButton.LeftButton or not getattr(self, 'start_point', None):
            super().mouseReleaseEvent(event)
            self.commit_history_record(); return

        end_point = self.get_snapped_pos(self.apply_angle_snap(self.start_point, raw_pos))
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)
        x1, y1, x2, y2 = self.start_point.x(), self.start_point.y(), end_point.x(), end_point.y()

        if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None

        if self.mode == "LINE":
            item = self.scene.addLine(x1, y1, x2, y2, pen)
            self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (x2, y2), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

        elif self.mode == "ARROW":
            item = self.scene.addLine(x1, y1, x2, y2, pen)
            shape_data = {
                "type": "arrow", "p1": (x1, y1), "p2": (x2, y2),
                "arrow_head_type": self.arrow_head_type,
                "arrow_direction": self.arrow_direction,
                "layer": self.active_layer, "color": self.current_color,
                "thickness": self.current_thickness, "style": self.current_style,
                "item": item
            }
            self._update_arrow_shape_graphics(shape_data)
            self.shapes.append(shape_data)

        elif self.mode == "H_LINE":
            if self.preset_line_length > 0:
                l_val = self.preset_line_length if x2 >= x1 else -self.preset_line_length
                p_end_x = x1 + l_val
            else:
                x1, p_end_x = x1 - 5000, x1 + 5000
            item = self.scene.addLine(x1, y1, p_end_x, y1, pen)
            self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (p_end_x, y1), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

        elif self.mode == "V_LINE":
            if self.preset_line_length > 0:
                l_val = self.preset_line_length if y2 >= y1 else -self.preset_line_length
                p_end_y = y1 + l_val
            else:
                y1, p_end_y = y1 - 5000, y1 + 5000
            item = self.scene.addLine(x1, y1, x1, p_end_y, pen)
            self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (x1, p_end_y), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

        elif self.mode == "RECT":
            rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2)
            item = self.scene.addRect(rx, ry, rw, rh, pen)
            self.shapes.append({"type": "rect", "p1": (rx, ry), "p2": (rx + rw, ry + rh), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

        elif self.mode in ["CIRCLE", "CIRCLE_2P"]:
            r = math.hypot(x2 - x1, y2 - y1)
            item = self.scene.addEllipse(x1 - r, y1 - r, 2 * r, 2 * r, pen)
            self.shapes.append({"type": "circle", "center": (x1, y1), "radius": r, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

        elif self.mode == "ELLIPSE":
            rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2)
            item = self.scene.addEllipse(rx, ry, rw, rh, pen)
            self.shapes.append({"type": "ellipse", "center": (rx + rw/2, ry + rh/2), "rx": rw/2, "ry": rh/2, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

        elif self.mode == "DIMENSION":
            self.create_autocad_dimension(self.start_point, end_point)

        elif self.mode == "DIM_RADIUS":
            self.create_radius_dimension(self.start_point, end_point, is_diameter=False)

        elif self.mode == "DIM_DIAMETER":
            self.create_radius_dimension(self.start_point, end_point, is_diameter=True)

        elif self.mode == "LEADER":
            self.add_leader_with_auto_measure(self.start_point, end_point)

        elif self.mode == "CLOUD":
            rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2)
            corners = [(rx, ry), (rx + rw, ry), (rx + rw, ry + rh), (rx, ry + rh)]
            pts = []
            step = max(5.0, self.cloud_pitch)
            for i in range(4):
                p1, p2 = corners[i], corners[(i + 1) % 4]
                length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                n = max(1, int(length / step))
                for k in range(n): pts.append((p1[0] + (k / n) * (p2[0] - p1[0]), p1[1] + (k / n) * (p2[1] - p1[1])))
            pts.append(corners[0])

            cloud_path = QPainterPath(); cloud_path.moveTo(QPointF(pts[0][0], pts[0][1]))
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                mx, my, dx, dy = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, p2[0] - p1[0], p2[1] - p1[1]
                d_val = math.hypot(dx, dy)
                if d_val > 0: cloud_path.quadTo(QPointF(mx + (dy / d_val) * self.cloud_arc_height, my + (-dx / d_val) * self.cloud_arc_height), QPointF(p2[0], p2[1]))
            item = self.scene.addPath(cloud_path, pen)
            self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

        self.start_point = None
        self.commit_history_record()

    # --- 円弧・寸法作成 ---
    def create_3pt_arc(self):
        if len(self.click_points) < 3: return
        p1, p2, p3 = self.click_points[:3]
        x1, y1, x2, y2, x3, y3 = p1.x(), p1.y(), p2.x(), p2.y(), p3.x(), p3.y()
        d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(d) < 1e-6:
            QMessageBox.warning(self, "エラー", "3点が一直線上にあるため円弧を作成できません。")
            self.click_points.clear(); return

        ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)) / d
        uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)) / d
        r = math.hypot(x1 - ux, y1 - uy)

        a1, a2, a3 = math.degrees(math.atan2(y1 - uy, x1 - ux)) % 360, math.degrees(math.atan2(y2 - uy, x2 - ux)) % 360, math.degrees(math.atan2(y3 - uy, x3 - ux)) % 360
        span = (a3 - a1) % 360
        if (a2 - a1) % 360 > span: span -= 360

        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)
        path = QPainterPath()
        path.arcMoveTo(ux - r, uy - r, 2 * r, 2 * r, -a1)
        path.arcTo(ux - r, uy - r, 2 * r, 2 * r, -a1, -span)
        item = self.scene.addPath(path, pen)
        self.shapes.append({"type": "arc", "center": (ux, uy), "radius": r, "start_angle": -a1, "span_angle": -span, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
        self.click_points.clear()

    def create_center_arc(self):
        if len(self.click_points) < 3: return
        p1, p2, p3 = self.click_points[:3]
        cx, cy, r = p1.x(), p1.y(), math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
        if r > 0:
            a1, a2 = math.degrees(math.atan2(p2.y() - cy, p2.x() - cx)) % 360, math.degrees(math.atan2(p3.y() - cy, p3.x() - cx)) % 360
            span = (a2 - a1) % 360
            disp_color = self.get_display_color(self.current_color)
            pen = QPen(disp_color, self.current_thickness, self.current_style)
            path = QPainterPath()
            path.arcMoveTo(cx - r, cy - r, 2 * r, 2 * r, -a1)
            path.arcTo(cx - r, cy - r, 2 * r, 2 * r, -a1, -span)
            item = self.scene.addPath(path, pen)
            self.shapes.append({"type": "arc", "center": (cx, cy), "radius": r, "start_angle": -a1, "span_angle": -span, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
        self.click_points.clear()

    def create_angle_dimension(self, s1, s2):
        p1, p2 = QPointF(*s1["p1"]), QPointF(*s1["p2"])
        p3, p4 = QPointF(*s2["p1"]), QPointF(*s2["p2"])
        den = (p1.x()-p2.x())*(p3.y()-p4.y()) - (p1.y()-p2.y())*(p3.x()-p4.x())
        if abs(den) < 1e-6:
            QMessageBox.warning(self, "エラー", "平行な直線同士の角度は測定できません。")
            return
        px = ((p1.x()*p2.y() - p1.y()*p2.x())*(p3.x()-p4.x()) - (p1.x()-p2.x())*(p3.x()*p4.y() - p3.y()*p4.x())) / den
        py = ((p1.x()*p2.y() - p1.y()*p2.x())*(p3.y()-p4.y()) - (p1.y()-p2.y())*(p3.x()*p4.y() - p3.y()*p4.x())) / den
        vx, vy = px, py

        v1 = p2 if math.hypot(p2.x()-vx, p2.y()-vy) > math.hypot(p1.x()-vx, p1.y()-vy) else p1
        v2 = p4 if math.hypot(p4.x()-vx, p4.y()-vy) > math.hypot(p3.x()-vx, p3.y()-vy) else p3
        a1 = math.degrees(math.atan2(v1.y() - vy, v1.x() - vx)) % 360
        a2 = math.degrees(math.atan2(v2.y() - vy, v2.x() - vx)) % 360
        diff = (a2 - a1) % 360
        if diff > 180: diff, a1, a2 = 360 - diff, a2, a1

        r = 60.0
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)

        path = QPainterPath()
        path.arcMoveTo(vx - r, vy - r, 2 * r, 2 * r, -a1)
        path.arcTo(vx - r, vy - r, 2 * r, 2 * r, -a1, -diff)
        item = self.scene.addPath(path, pen)
        mid_a = math.radians(-a1 - diff / 2.0)
        tx, ty = vx + (r + 15) * math.cos(mid_a), vy + (r + 15) * math.sin(mid_a)
        val_str = f"{diff:.1f}°"
        
        t_item = self.scene.addText(val_str); t_item.setDefaultTextColor(disp_color)
        t_item.setFont(QFont("Meiryo", int(max(10, self.current_thickness * 4))))
        t_item.setPos(tx - 15, ty - 10)

        self.shapes.append({"type": "arc", "center": (vx, vy), "radius": r, "start_angle": -a1, "span_angle": -diff, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
        self.shapes.append({"type": "text", "text": val_str, "pos": (tx - 15, ty - 10), "font_size": 12, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": t_item})

    def clear_trim_preview(self):
        if getattr(self, 'trim_preview_item', None) and self.trim_preview_item.scene() == self.scene:
            self.scene.removeItem(self.trim_preview_item)
            self.trim_preview_item = None

    def _find_trim_target_and_split(self, pos):
        click_pt = Point(pos.x(), pos.y())
        target_shape = None
        min_dist = 12.0
        for shape in self.shapes:
            geom = self._shape_to_shapely(shape)
            if geom and geom.geom_type in ['LineString', 'MultiLineString']:
                d = geom.distance(click_pt)
                if d < min_dist: min_dist, target_shape = d, shape
        if not target_shape: return None, [], None

        target_geom = self._shape_to_shapely(target_shape)
        intersections = []
        for shape in self.shapes:
            if shape == target_shape: continue
            other_geom = self._shape_to_shapely(shape)
            if other_geom and target_geom.intersects(other_geom):
                inter = target_geom.intersection(other_geom)
                if isinstance(inter, Point): intersections.append(inter)
                elif isinstance(inter, MultiPoint): intersections.extend(inter.geoms)
                elif isinstance(inter, GeometryCollection):
                    for g in inter.geoms:
                        if isinstance(g, Point): intersections.append(g)

        if not intersections: return target_geom, [], target_shape

        cut_pts = MultiPoint(intersections)
        snapped_target = snap(target_geom, cut_pts, 1.0)
        split_res = split(snapped_target, cut_pts)
        segments = list(split_res.geoms) if hasattr(split_res, 'geoms') else [split_res]

        best_seg, best_dist = None, float('inf')
        for seg in segments:
            d = seg.distance(click_pt)
            if d < best_dist: best_dist, best_seg = d, seg
        return best_seg, [s for s in segments if s != best_seg], target_shape