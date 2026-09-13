import os
import math
import json
import pymupdf
import ezdxf
import copy
from shapely.geometry import LineString, Point, Polygon, MultiPoint, GeometryCollection, MultiPolygon
from shapely.ops import split, snap

from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QInputDialog, QMessageBox, 
                             QFileDialog, QGraphicsItem, QGraphicsEllipseItem, 
                             QGraphicsLineItem, QGraphicsRectItem, QGraphicsPolygonItem, 
                             QGraphicsPathItem, QGraphicsTextItem, QGraphicsPixmapItem, 
                             QGraphicsItemGroup, QApplication, QMenu, QDialog,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QComboBox, QDoubleSpinBox, QSpinBox, QTextEdit, QColorDialog)
from PyQt6.QtGui import (QPen, QColor, QPixmap, QPolygonF, QBrush, QFont, QImage, 
                         QPainterPath, QPainter, QPageSize, QPageLayout)
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal

def _clean_for_json(obj):
    """QColorやPenStyleなどの特殊オブジェクトを安全なJSON互換データに変換"""
    if isinstance(obj, QColor):
        return obj.name()
    elif hasattr(obj, "value"):
        return obj.value
    elif isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items() if k != "item"}
    elif isinstance(obj, list):
        return [_clean_for_json(i) for i in obj]
    elif isinstance(obj, tuple):
        return [_clean_for_json(i) for i in obj]
    return obj

class TextEditDialog(QDialog):
    """文章入力・再編集用ダイアログ（改行・フォントサイズ・文字色対応）"""
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

        layout.addWidget(QLabel("テキスト (Enter / Shift+Enter で改行):"))
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
    """表データのグラフィカル再編集ダイアログ（文字サイズ・文字色＆自動フィッティング機能付き）"""
    def __init__(self, parent=None, grid_data=None, cell_w=100, cell_h=30, align="CENTER", font_size=12, color=None):
        super().__init__(parent)
        self.setWindowTitle("表データの再編集")
        self.resize(650, 480)

        self.grid_data = copy.deepcopy(grid_data) if grid_data else [[""]]
        self.cell_w = cell_w
        self.cell_h = cell_h
        self.align = align
        self.font_size = font_size
        self.selected_color = QColor(color) if color else QColor(0, 0, 0)

        main_layout = QVBoxLayout(self)

        cfg_layout = QHBoxLayout()
        cfg_layout.addWidget(QLabel("セル幅(mm):"))
        self.w_spin = QDoubleSpinBox()
        self.w_spin.setRange(10.0, 2000.0)
        self.w_spin.setValue(float(self.cell_w))
        cfg_layout.addWidget(self.w_spin)

        cfg_layout.addWidget(QLabel("セル高(mm):"))
        self.h_spin = QDoubleSpinBox()
        self.h_spin.setRange(5.0, 1000.0)
        self.h_spin.setValue(float(self.cell_h))
        cfg_layout.addWidget(self.h_spin)

        cfg_layout.addWidget(QLabel("文字サイズ(pt):"))
        self.font_spin = QSpinBox()
        self.font_spin.setRange(6, 200)
        self.font_spin.setValue(int(self.font_size))
        cfg_layout.addWidget(self.font_spin)

        cfg_layout.addWidget(QLabel("文字色:"))
        self.color_btn = QPushButton(" 色を選択 ")
        self.update_color_button_style()
        self.color_btn.clicked.connect(self.choose_color)
        cfg_layout.addWidget(self.color_btn)

        cfg_layout.addWidget(QLabel("文字揃え:"))
        self.align_combo = QComboBox()
        self.align_combo.addItems(["中央寄せ (CENTER)", "左寄せ (LEFT)", "右寄せ (RIGHT)"])
        align_map = {"CENTER": 0, "LEFT": 1, "RIGHT": 2}
        self.align_combo.setCurrentIndex(align_map.get(str(align).upper(), 0))
        cfg_layout.addWidget(self.align_combo)

        main_layout.addLayout(cfg_layout)

        btn_layout = QHBoxLayout()
        add_row_btn = QPushButton("＋ 行追加")
        add_row_btn.clicked.connect(self.add_row)
        del_row_btn = QPushButton("－ 行削除")
        del_row_btn.clicked.connect(self.del_row)
        add_col_btn = QPushButton("＋ 列追加")
        add_col_btn.clicked.connect(self.add_col)
        del_col_btn = QPushButton("－ 列削除")
        del_col_btn.clicked.connect(self.del_col)
        auto_fit_btn = QPushButton("📐 文字に合わせ自動調整")
        auto_fit_btn.clicked.connect(self.auto_fit_cell_size)

        btn_layout.addWidget(add_row_btn)
        btn_layout.addWidget(del_row_btn)
        btn_layout.addWidget(add_col_btn)
        btn_layout.addWidget(del_col_btn)
        btn_layout.addWidget(auto_fit_btn)
        main_layout.addLayout(btn_layout)

        self.table_widget = QTableWidget()
        self.populate_table()
        main_layout.addWidget(self.table_widget)

        dlg_btns = QHBoxLayout()
        ok_btn = QPushButton("OK (表を更新)")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        dlg_btns.addStretch()
        dlg_btns.addWidget(ok_btn)
        dlg_btns.addWidget(cancel_btn)
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
        rows = len(self.grid_data)
        cols = max(len(r) for r in self.grid_data) if self.grid_data else 1
        self.table_widget.setRowCount(rows)
        self.table_widget.setColumnCount(cols)
        for r in range(rows):
            for c in range(cols):
                val = self.grid_data[r][c] if c < len(self.grid_data[r]) else ""
                self.table_widget.setItem(r, c, QTableWidgetItem(str(val)))

    def add_row(self): self.table_widget.insertRow(self.table_widget.rowCount())
    def del_row(self):
        curr = self.table_widget.currentRow()
        if curr >= 0: self.table_widget.removeRow(curr)
        elif self.table_widget.rowCount() > 0: self.table_widget.removeRow(self.table_widget.rowCount() - 1)

    def add_col(self): self.table_widget.insertColumn(self.table_widget.columnCount())
    def del_col(self):
        curr = self.table_widget.currentColumn()
        if curr >= 0: self.table_widget.removeColumn(curr)
        elif self.table_widget.columnCount() > 0: self.table_widget.removeColumn(self.table_widget.columnCount() - 1)

    def auto_fit_cell_size(self):
        f_size = self.font_spin.value()
        max_lines = 1
        max_chars = 1
        for r in range(self.table_widget.rowCount()):
            for c in range(self.table_widget.columnCount()):
                item = self.table_widget.item(r, c)
                if item and item.text():
                    lines = item.text().split("\n")
                    max_lines = max(max_lines, len(lines))
                    for line in lines:
                        max_chars = max(max_chars, len(line))
        calc_w = max(40.0, max_chars * f_size * 0.9 + 20.0)
        calc_h = max(20.0, max_lines * f_size * 1.5 + 10.0)
        self.w_spin.setValue(calc_w)
        self.h_spin.setValue(calc_h)

    def get_result(self):
        rows = self.table_widget.rowCount()
        cols = self.table_widget.columnCount()
        res_grid = []
        for r in range(rows):
            row_vals = []
            for c in range(cols):
                item = self.table_widget.item(r, c)
                row_vals.append(item.text() if item else "")
            res_grid.append(row_vals)

        align_list = ["CENTER", "LEFT", "RIGHT"]
        res_align = align_list[self.align_combo.currentIndex()]
        return res_grid, self.w_spin.value(), self.h_spin.value(), res_align, self.font_spin.value(), self.selected_color


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

        self.undo_stack = []
        self.redo_stack = []
        self.copied_shapes_buffer = []
        self._before_items = set()
        self._before_shapes_len = 0

        self.arrow_head_type = "FILLED"
        self.arrow_direction = "END"

        self.cloud_pitch = 20.0
        self.cloud_arc_height = 8.0
        self.preset_rect_w, self.preset_rect_h = 100.0, 50.0
        self.preset_circle_r = 40.0
        self.preset_arc_r, self.preset_arc_start, self.preset_arc_span = 40.0, 0.0, 90.0
        self.preset_poly_sides, self.preset_poly_r, self.preset_poly_angle = 6, 40.0, 0.0
        self.offset_dist = 20.0
        self.rotate_angle = 45.0
        self.scale_factor_val = 1.5
        self.array_rows, self.array_cols = 3, 3
        self.array_row_gap, self.array_col_gap = 50.0, 50.0
        self.hatch_angle = 45.0
        self.hatch_spacing = 15.0
        self.hatch_color = QColor(255, 0, 0)
        self.hatch_thickness = 1
        self.hatch_style = Qt.PenStyle.SolidLine
        self.fillet_radius = 50.0
        self.chamfer_dist = 50.0

        self.trace_canny_low = 20
        self.trace_canny_high = 80
        self.trace_min_line_length = 6
        self.trace_max_line_gap = 4

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

    # --- 新規作成・保存・読み込み ---
    def new_project(self):
        if self.shapes or any(not isinstance(i, QGraphicsItemGroup) for i in self.scene.items()):
            res = QMessageBox.question(
                self, "新規作成", 
                "現在の図面を全消去して新規作成しますか？\n（保存していない変更は失われます）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if res != QMessageBox.StandardButton.Yes: return False

        self.scene.clear()
        self.shapes.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.paper_guide_item = None
        self.custom_print_rect_item = None
        self.set_mode("SELECT")
        return True

    def save_project_json(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "プロジェクトを保存", "", "CAD Project Files (*.json)")
        if not file_path: return
        try:
            data = {
                "version": "1.0",
                "layers": _clean_for_json(self.layers),
                "blocks": _clean_for_json(self.blocks),
                "shapes": _clean_for_json(self.shapes),
                "paper_scale": self.paper_scale,
                "active_layer": self.active_layer
            }

            def cad_json_default(obj):
                if isinstance(obj, QColor):
                    return obj.name()
                if hasattr(obj, "value"):
                    return obj.value
                raise TypeError(f"Type {type(obj)} not serializable")

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False, default=cad_json_default)
            QMessageBox.information(self, "成功", f"プロジェクトを保存しました:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"保存失敗:\n{e}")

    def load_project_json(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "プロジェクトを開く", "", "CAD Project Files (*.json)")
        if not file_path: return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            QMessageBox.critical(self, "エラー", f"ファイルが壊れているため読み込めません:\n{e}")
            return
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"読み込み失敗:\n{e}")
            return

        try:
            self.scene.clear(); self.shapes.clear()
            self.paper_guide_item, self.custom_print_rect_item = None, None
            self.layers.clear()

            for name, props in data.get("layers", {}).items():
                style_val = props.get("style", 1)
                try: style = Qt.PenStyle(int(style_val))
                except: style = Qt.PenStyle.SolidLine
                self.layers[name] = {
                    "color": QColor(props.get("color", "#000000")),
                    "thickness": props.get("thickness", 2),
                    "style": style,
                    "visible": props.get("visible", True),
                    "locked": props.get("locked", False),
                    "printable": props.get("printable", True)
                }

            self.active_layer = data.get("active_layer", "0")
            self.paper_scale = data.get("paper_scale", 100)
            self.blocks = data.get("blocks", {})

            for s in data.get("shapes", []):
                color_val = s.get("color", "#FF0000")
                s["color"] = QColor(color_val) if isinstance(color_val, str) else color_val
                
                style_val = s.get("style", 1)
                try: style = Qt.PenStyle(int(style_val))
                except: style = Qt.PenStyle.SolidLine
                s["style"] = style

                stype = s.get("type")
                color = self.get_display_color(s["color"])
                thickness = s.get("thickness", self.current_thickness)
                pen = QPen(color, thickness, style)
                item = None

                if stype == "line":
                    item = self.scene.addLine(s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1], pen)
                elif stype == "arrow":
                    item = self.scene.addLine(s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1], pen)
                    self._update_arrow_shape_graphics(s)
                elif stype == "rect":
                    x1, y1, x2, y2 = s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1]
                    item = self.scene.addRect(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2), pen)
                elif stype == "circle":
                    cx, cy, r = s["center"][0], s["center"][1], s["radius"]
                    item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
                elif stype == "arc":
                    cx, cy, r = s["center"][0], s["center"][1], s["radius"]
                    st, sp = s["start_angle"], s["span_angle"]
                    path = QPainterPath()
                    path.arcMoveTo(cx - r, cy - r, 2 * r, 2 * r, st)
                    path.arcTo(cx - r, cy - r, 2 * r, 2 * r, st, sp)
                    item = self.scene.addPath(path, pen)
                elif stype == "table":
                    grid_data = s.get("grid_data", [[""]])
                    cell_w = s.get("cell_w", 100)
                    cell_h = s.get("cell_h", 30)
                    align = s.get("align", "CENTER")
                    f_size = s.get("font_size", 12)
                    t_col = s.get("color", self.current_color)
                    pos_val = s.get("pos", (0, 0))
                    self.add_table_data(grid_data, cell_w, cell_h, QPointF(pos_val[0], pos_val[1]), align=align, font_size=f_size, color=t_col, target_shape=s)
                    continue
                elif stype == "polyline":
                    pts = [QPointF(pt[0], pt[1]) for pt in s["points"]]
                    if s.get("is_closed"): item = self.scene.addPolygon(pts, pen)
                    else:
                        path = QPainterPath(); path.moveTo(pts[0])
                        for pt in pts[1:]: path.lineTo(pt)
                        item = self.scene.addPath(path, pen)
                elif stype == "text":
                    item = self.scene.addText(s["text"])
                    item.setDefaultTextColor(color); item.setFont(QFont("Meiryo", int(s.get("font_size", 12))))
                    item.setPos(s["pos"][0], s["pos"][1])

                s["item"] = item
                self.shapes.append(s)

            self.apply_layer_states()
            QMessageBox.information(self, "成功", f"プロジェクトを読み込みました:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"プロジェクトデータの構造解析に失敗しました:\n{e}")

    # --- DXFファイルのインポート ---
    def import_dxf_file(self, file_path=None):
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(self, "DXFファイルを開く", "", "DXF Files (*.dxf)")
        if not file_path or not os.path.exists(file_path): return

        try:
            doc = ezdxf.readfile(file_path)
            msp = doc.modelspace()
            self.start_history_record()

            for entity in msp:
                dxftype = entity.dxftype()
                layer_name = entity.dxf.layer if hasattr(entity.dxf, 'layer') else "0"
                if layer_name not in self.layers:
                    self.layers[layer_name] = {"color": QColor(0, 0, 0), "thickness": 2, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True}

                color = self.layers[layer_name]["color"]
                pen = QPen(self.get_display_color(color), self.current_thickness, self.current_style)

                if dxftype == 'LINE':
                    p1 = (entity.dxf.start.x, -entity.dxf.start.y)
                    p2 = (entity.dxf.end.x, -entity.dxf.end.y)
                    item = self.scene.addLine(p1[0], p1[1], p2[0], p2[1], pen)
                    self.shapes.append({"type": "line", "p1": p1, "p2": p2, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

                elif dxftype == 'CIRCLE':
                    cx, cy = entity.dxf.center.x, -entity.dxf.center.y
                    r = entity.dxf.radius
                    item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
                    self.shapes.append({"type": "circle", "center": (cx, cy), "radius": r, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

                elif dxftype == 'ARC':
                    cx, cy = entity.dxf.center.x, -entity.dxf.center.y
                    r = entity.dxf.radius
                    st = -entity.dxf.start_angle
                    end_a = -entity.dxf.end_angle
                    span = (end_a - st) % 360
                    if span == 0: span = -360
                    path = QPainterPath()
                    path.arcMoveTo(cx - r, cy - r, 2 * r, 2 * r, st)
                    path.arcTo(cx - r, cy - r, 2 * r, 2 * r, st, span)
                    item = self.scene.addPath(path, pen)
                    self.shapes.append({"type": "arc", "center": (cx, cy), "radius": r, "start_angle": st, "span_angle": span, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

                elif dxftype in ['LWPOLYLINE', 'POLYLINE']:
                    pts = [(p[0], -p[1]) for p in entity.get_points()]
                    is_closed = entity.is_closed
                    qpts = [QPointF(px, py) for px, py in pts]
                    if is_closed:
                        item = self.scene.addPolygon(QPolygonF(qpts), pen)
                    else:
                        path = QPainterPath()
                        if qpts:
                            path.moveTo(qpts[0])
                            for pt in qpts[1:]: path.lineTo(pt)
                        item = self.scene.addPath(path, pen)
                    self.shapes.append({"type": "polyline", "points": pts, "is_closed": is_closed, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

                elif dxftype in ['TEXT', 'MTEXT']:
                    txt = entity.dxf.text if dxftype == 'TEXT' else entity.text
                    pos = (entity.dxf.insert.x, -entity.dxf.insert.y) if hasattr(entity.dxf, 'insert') else (0, 0)
                    t_item = self.scene.addText(txt)
                    t_item.setDefaultTextColor(self.get_display_color(color))
                    t_item.setFont(QFont("Meiryo", 12))
                    t_item.setPos(pos[0], pos[1])
                    self.shapes.append({"type": "text", "text": txt, "pos": pos, "font_size": 12, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": t_item})

            self.apply_layer_states()
            self.commit_history_record()
            QMessageBox.information(self, "成功", f"DXFファイルを読み込みました:\n{os.path.basename(file_path)}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"DXFインポート失敗:\n{e}")

    # --- 単体テキスト編集 ---
    def edit_text_shape(self, shape):
        if not shape or shape.get("type") != "text": return
        current_txt = shape.get("text", "")
        current_font_size = shape.get("font_size", 12)
        current_color = shape.get("color", self.current_color)

        dlg = TextEditDialog(self, text=current_txt, font_size=current_font_size, color=current_color)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_txt, new_size, new_color = dlg.get_result()
            if new_txt.strip():
                self.start_history_record()
                item = shape.get("item")
                disp_color = self.get_display_color(new_color)

                shape["text"] = new_txt.strip()
                shape["font_size"] = new_size
                shape["color"] = new_color

                if item and isinstance(item, QGraphicsTextItem):
                    item.setPlainText(new_txt.strip())
                    item.setFont(QFont("Meiryo", int(new_size)))
                    item.setDefaultTextColor(disp_color)
                self.commit_history_record()

    # --- 自動寸法・要素測定ヘルパー ---
    def calculate_shape_measurements(self, pos):
        click_pt = Point(pos.x(), pos.y())
        target_shape = None
        min_dist = 20.0
        for shape in self.shapes:
            geom = self._shape_to_shapely(shape)
            if geom and geom.distance(click_pt) < min_dist:
                min_dist = geom.distance(click_pt)
                target_shape = shape

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
                poly = Polygon(pts)
                return f"A = {(poly.area * (self.scale_factor ** 2)) / 1000000.0:.2f} m²\n(L = {length_mm:.1f} mm)"
            return f"L = {length_mm:.1f} mm"
        return ""

    # --- AutoCADスタイル寸法線描画 ---
    def create_autocad_dimension(self, p1, p2, offset=25.0):
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-4: return

        angle_rad = math.atan2(dy, dx)
        angle_deg = math.degrees(angle_rad)

        nx, ny = -dy / dist, dx / dist
        p1_ext = QPointF(p1.x() + nx * offset, p1.y() + ny * offset)
        p2_ext = QPointF(p2.x() + nx * offset, p2.y() + ny * offset)

        disp_color = self.get_display_color(self.current_color)
        pen_dim = QPen(disp_color, self.current_thickness, Qt.PenStyle.SolidLine)
        pen_ext = QPen(disp_color, max(1, self.current_thickness - 1), Qt.PenStyle.SolidLine)

        items = []
        items.append(self.scene.addLine(p1.x() + nx * 2, p1.y() + ny * 2, p1_ext.x() + nx * 5, p1_ext.y() + ny * 5, pen_ext))
        items.append(self.scene.addLine(p2.x() + nx * 2, p2.y() + ny * 2, p2_ext.x() + nx * 5, p2_ext.y() + ny * 5, pen_ext))
        items.append(self.scene.addLine(p1_ext.x(), p1_ext.y(), p2_ext.x(), p2_ext.y(), pen_dim))

        items.extend(self._draw_arrow_head_shape(p2_ext, angle_rad, self.arrow_head_type))
        items.extend(self._draw_arrow_head_shape(p1_ext, angle_rad + math.pi, self.arrow_head_type))

        val_str = f"{dist * self.scale_factor:.1f}"
        t_item = self.scene.addText(val_str)
        t_item.setDefaultTextColor(disp_color)
        t_item.setFont(QFont("Meiryo", int(max(10, self.current_thickness * 3.5))))

        text_angle = angle_deg
        if text_angle > 90 or text_angle < -90:
            text_angle += 180.0

        t_rect = t_item.boundingRect()
        t_item.setTransformOriginPoint(t_rect.width() / 2.0, t_rect.height() / 2.0)
        t_item.setRotation(text_angle)

        mid_x, mid_y = (p1_ext.x() + p2_ext.x()) / 2.0, (p1_ext.y() + p2_ext.y()) / 2.0
        t_item.setPos(mid_x - t_rect.width() / 2.0 + nx * 8, mid_y - t_rect.height() / 2.0 + ny * 8)
        items.append(t_item)

        group = self.scene.createItemGroup(items)
        group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)

        self.shapes.append({
            "type": "dimension", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()),
            "val_str": val_str, "layer": self.active_layer, "color": self.current_color,
            "thickness": self.current_thickness, "style": self.current_style, "item": group
        })

    def create_radius_dimension(self, p1, p2, is_diameter=False):
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-4: return

        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, Qt.PenStyle.SolidLine)
        items = []
        angle_rad = math.atan2(dy, dx)
        angle_deg = math.degrees(angle_rad)

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

        t_item = self.scene.addText(val_str)
        t_item.setDefaultTextColor(disp_color)
        t_item.setFont(QFont("Meiryo", int(max(10, self.current_thickness * 3.5))))

        text_angle = angle_deg
        if text_angle > 90 or text_angle < -90:
            text_angle += 180.0

        t_rect = t_item.boundingRect()
        t_item.setTransformOriginPoint(t_rect.width() / 2.0, t_rect.height() / 2.0)
        t_item.setRotation(text_angle)

        mid_x, mid_y = (p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0
        t_item.setPos(mid_x - t_rect.width() / 2.0, mid_y - t_rect.height() / 2.0 - 5.0)
        items.append(t_item)

        group = self.scene.createItemGroup(items)
        group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)

        self.shapes.append({
            "type": "dimension", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()),
            "val_str": val_str, "layer": self.active_layer, "color": self.current_color,
            "thickness": self.current_thickness, "style": self.current_style, "item": group
        })

    # --- 表（テーブル）自動生成・移動・再編集 ---
    def add_table_data(self, grid_data, cell_w, cell_h, pos, align="CENTER", font_size=None, color=None, target_shape=None):
        rows = len(grid_data)
        cols = max(len(r) for r in grid_data) if grid_data else 0
        if rows == 0 or cols == 0: return

        if isinstance(align, bool):
            align = "CENTER" if align else "LEFT"
        align = str(align).upper()

        if font_size is None:
            font_size = max(10, int(self.current_thickness * 3.5))
        else:
            font_size = int(font_size)

        table_color = QColor(color) if color else self.current_color
        font = QFont("Meiryo", font_size)

        dummy_item = QGraphicsTextItem()
        dummy_item.setFont(font)
        for r in range(rows):
            for c in range(len(grid_data[r])):
                val = str(grid_data[r][c]).strip()
                if val:
                    dummy_item.setPlainText(val)
                    rect = dummy_item.boundingRect()
                    cell_w = max(cell_w, rect.width() + 10.0)
                    cell_h = max(cell_h, rect.height() + 6.0)

        self.start_history_record()
        disp_color = self.get_display_color(table_color)
        pen = QPen(disp_color, self.current_thickness, Qt.PenStyle.SolidLine)
        sx, sy = pos.x(), pos.y()

        table_items = []

        for r in range(rows + 1):
            line = self.scene.addLine(sx, sy + r * cell_h, sx + cols * cell_w, sy + r * cell_h, pen)
            table_items.append(line)
        for c in range(cols + 1):
            line = self.scene.addLine(sx + c * cell_w, sy, sx + c * cell_w, sy + rows * cell_h, pen)
            table_items.append(line)

        for r in range(rows):
            for c in range(len(grid_data[r])):
                val = str(grid_data[r][c]).strip()
                if val:
                    t_item = self.scene.addText(val, font)
                    t_item.setDefaultTextColor(disp_color)
                    t_rect = t_item.boundingRect()

                    cell_x = sx + c * cell_w
                    cell_y = sy + r * cell_h
                    ty = cell_y + (cell_h - t_rect.height()) / 2.0

                    if align == "CENTER":
                        tx = cell_x + (cell_w - t_rect.width()) / 2.0
                    elif align == "RIGHT":
                        tx = cell_x + cell_w - t_rect.width() - 5.0
                    else:  # LEFT
                        tx = cell_x + 5.0

                    t_item.setPos(tx, ty)
                    table_items.append(t_item)

        if table_items:
            group = self.scene.createItemGroup(table_items)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)

            if target_shape and target_shape in self.shapes:
                if target_shape.get("item"):
                    self.safe_remove_item(target_shape["item"])
                target_shape["grid_data"] = grid_data
                target_shape["cell_w"] = cell_w
                target_shape["cell_h"] = cell_h
                target_shape["align"] = align
                target_shape["font_size"] = font_size
                target_shape["color"] = table_color
                target_shape["pos"] = (sx, sy)
                target_shape["item"] = group
            else:
                self.shapes.append({
                    "type": "table",
                    "grid_data": grid_data,
                    "cell_w": cell_w,
                    "cell_h": cell_h,
                    "align": align,
                    "font_size": font_size,
                    "pos": (sx, sy),
                    "layer": self.active_layer,
                    "color": table_color,
                    "thickness": self.current_thickness,
                    "style": self.current_style,
                    "item": group
                })

        self.commit_history_record()

    def edit_table_shape(self, shape):
        if not shape or shape.get("type") != "table": return
        
        grp_item = shape.get("item")
        if grp_item and isinstance(grp_item, QGraphicsItemGroup):
            rect = grp_item.sceneTransform().mapRect(grp_item.boundingRect())
            pos = QPointF(rect.x(), rect.y())
        else:
            pos_val = shape.get("pos", (0, 0))
            pos = QPointF(pos_val[0], pos_val[1])

        dlg = TableEditDialog(
            self,
            grid_data=shape.get("grid_data", [[""]]),
            cell_w=shape.get("cell_w", 100),
            cell_h=shape.get("cell_h", 30),
            align=shape.get("align", "CENTER"),
            font_size=shape.get("font_size", 12),
            color=shape.get("color", self.current_color)
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_grid, new_w, new_h, new_align, new_fsize, new_color = dlg.get_result()
            self.add_table_data(new_grid, new_w, new_h, pos, align=new_align, font_size=new_fsize, color=new_color, target_shape=shape)

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.pos())
        if item:
            for shape in self.shapes:
                stype = shape.get("type")
                if stype == "table":
                    grp = shape.get("item")
                    if grp == item or (isinstance(grp, QGraphicsItemGroup) and item in grp.childItems()):
                        self.edit_table_shape(shape)
                        return
                elif stype == "text":
                    if shape.get("item") == item:
                        self.edit_text_shape(shape)
                        return
        super().mouseDoubleClickEvent(event)

    # --- 矢印形状・方向の設定・変更処理 ---
    def prompt_arrow_settings(self):
        head_types = ["▲ 塗りつぶし (FILLED)", "＞ 開いた線 (OPEN)", "● 黒丸 (DOT)", "/ 建築用斜線 (SLASH)"]
        directions = ["終点のみ (END)", "始点のみ (START)", "両端 (BOTH)"]

        type_keys = ["FILLED", "OPEN", "DOT", "SLASH"]
        dir_keys = ["END", "START", "BOTH"]

        h_idx = type_keys.index(self.arrow_head_type) if self.arrow_head_type in type_keys else 0
        d_idx = dir_keys.index(self.arrow_direction) if self.arrow_direction in dir_keys else 0

        h_item, ok1 = QInputDialog.getItem(self, "矢印形状の設定", "矢印の頭部形状を選択してください:", head_types, h_idx, False)
        if not ok1: return

        d_item, ok2 = QInputDialog.getItem(self, "矢印向きの設定", "矢印の配置方向を選択してください:", directions, d_idx, False)
        if not ok2: return

        new_h = type_keys[head_types.index(h_item)]
        new_d = dir_keys[directions.index(d_item)]

        self.arrow_head_type = new_h
        self.arrow_direction = new_d

        selected = self.scene.selectedItems()
        if selected:
            self.change_selected_arrows_style(head_type=new_h, direction=new_d)
            QMessageBox.information(self, "完了", "選択中の矢印/寸法線のスタイルを変更しました。")
        else:
            QMessageBox.information(self, "完了", f"今後の作図設定を更新しました:\n・形状: {h_item}\n・向き: {d_item}")

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
            item1 = self.scene.addLine(pos.x(), pos.y(), p_a1.x(), p_a1.y(), pen)
            item2 = self.scene.addLine(pos.x(), pos.y(), p_a2.x(), p_a2.y(), pen)
            created_items.extend([item1, item2])

        elif head_type == "DOT":
            r = size * 0.4
            item = self.scene.addEllipse(pos.x() - r, pos.y() - r, 2 * r, 2 * r, QPen(disp_color, 1), QBrush(disp_color))
            created_items.append(item)

        elif head_type == "SLASH":
            pen = QPen(disp_color, self.current_thickness, Qt.PenStyle.SolidLine)
            s_angle = angle + math.pi / 4
            p1 = QPointF(pos.x() - size * 0.6 * math.cos(s_angle), pos.y() - size * 0.6 * math.sin(s_angle))
            p2 = QPointF(pos.x() + size * 0.6 * math.cos(s_angle), pos.y() + size * 0.6 * math.sin(s_angle))
            item = self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
            created_items.append(item)

        return created_items

    def _update_arrow_shape_graphics(self, shape):
        if "head_items" in shape:
            for item in shape["head_items"]:
                self.safe_remove_item(item)
            shape["head_items"] = []

        if shape.get("item") and isinstance(shape["item"], QGraphicsLineItem):
            p1, p2 = shape["p1"], shape["p2"]
            shape["item"].setLine(p1[0], p1[1], p2[0], p2[1])

            angle_end = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
            angle_start = math.atan2(p1[1] - p2[1], p1[0] - p2[0])

            h_type = shape.get("arrow_head_type", self.arrow_head_type)
            dir_val = shape.get("arrow_direction", self.arrow_direction)

            head_items = []
            if dir_val in ["END", "BOTH"]:
                head_items.extend(self._draw_arrow_head_shape(QPointF(p2[0], p2[1]), angle_end, h_type))
            if dir_val in ["START", "BOTH"]:
                head_items.extend(self._draw_arrow_head_shape(QPointF(p1[0], p1[1]), angle_start, h_type))
            shape["head_items"] = head_items

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
            table_shape = None
            text_shape = None
            for shape in self.shapes:
                grp = shape.get("item")
                if shape.get("type") == "table" and (grp in selected or any(isinstance(grp, QGraphicsItemGroup) and child in selected for child in grp.childItems())):
                    table_shape = shape
                    break
                elif shape.get("type") == "text" and grp in selected:
                    text_shape = shape
                    break

            if table_shape:
                tbl_act = menu.addAction("📊 表を再編集...")
                tbl_act.triggered.connect(lambda: self.edit_table_shape(table_shape))
                menu.addSeparator()

            if text_shape:
                txt_act = menu.addAction("📝 文字を再編集...")
                txt_act.triggered.connect(lambda: self.edit_text_shape(text_shape))
                menu.addSeparator()

            menu.addAction("📋 複製 (Duplicate)", self.duplicate_selected)
            menu.addAction("✂ コピー (Ctrl+C)", self.copy_selected_to_clipboard)
            menu.addAction("🗑 削除 (Delete)", self.delete_selected)
            menu.addSeparator()

            arrow_cfg_act = menu.addAction("🏹 矢印の設定 (形状・向き)...")
            arrow_cfg_act.triggered.connect(self.prompt_arrow_settings)

            has_arrow = any(s.get("item") in selected and s.get("type") == "arrow" for s in self.shapes)
            if has_arrow:
                arrow_menu = menu.addMenu("🏹 矢印のクイック操作")
                arrow_menu.addAction("🔃 向きを反転", self.reverse_selected_arrows)
                arrow_menu.addSeparator()

                dir_end = arrow_menu.addAction("終点に矢印 (標準)")
                dir_end.triggered.connect(lambda: self.change_selected_arrows_style(direction="END"))
                dir_start = arrow_menu.addAction("始点に矢印")
                dir_start.triggered.connect(lambda: self.change_selected_arrows_style(direction="START"))
                dir_both = arrow_menu.addAction("両端に矢印")
                dir_both.triggered.connect(lambda: self.change_selected_arrows_style(direction="BOTH"))

                arrow_menu.addSeparator()
                type_filled = arrow_menu.addAction("▲ 塗りつぶし矢印")
                type_filled.triggered.connect(lambda: self.change_selected_arrows_style(head_type="FILLED"))
                type_open = arrow_menu.addAction("＞ 開いた線矢印")
                type_open.triggered.connect(lambda: self.change_selected_arrows_style(head_type="OPEN"))
                type_dot = arrow_menu.addAction("● 黒丸 (ドット)")
                type_dot.triggered.connect(lambda: self.change_selected_arrows_style(head_type="DOT"))
                type_slash = arrow_menu.addAction("/ 建築用斜線 (スラッシュ)")
                type_slash.triggered.connect(lambda: self.change_selected_arrows_style(head_type="SLASH"))

            menu.addSeparator()

            rot_act = menu.addAction("🔄 90度回転")
            rot_act.triggered.connect(lambda: self.set_mode("ROTATE"))
            rot_copy_act = menu.addAction("🔄 90度回転コピー")
            rot_copy_act.triggered.connect(lambda: self.set_mode("ROTATE_COPY"))
            mir_act = menu.addAction("🪞 左右反転")
            mir_act.triggered.connect(lambda: self.set_mode("MIRROR"))
            mir_copy_act = menu.addAction("🪞 左右反転コピー")
            mir_copy_act.triggered.connect(lambda: self.set_mode("MIRROR_COPY"))
            
            menu.addSeparator()
            hatch_act = menu.addAction("🎨 ハッチング適用 (HATCH)")
            hatch_act.triggered.connect(self.apply_hatching)
            cloud_act = menu.addAction("☁️ 雲マークに変換")
            cloud_act.triggered.connect(self.convert_selected_to_cloud)
            
            menu.addSeparator()
            front_act = menu.addAction("⬆ 最前面へ移動")
            front_act.triggered.connect(self.bring_selected_to_front)
            back_act = menu.addAction("⬇ 最背面へ移動")
            back_act.triggered.connect(self.send_selected_to_back)
            
            menu.addSeparator()
            join_act = menu.addAction("🔗 線の結合 (JOIN)")
            join_act.triggered.connect(self.join_selected_lines)
            center_act = menu.addAction("🎯 中心線生成")
            center_act.triggered.connect(self.generate_centerlines)
            
            menu.addSeparator()
            grp_act = menu.addAction("📦 グループ化")
            grp_act.triggered.connect(self.group_selected_items)
            ungrp_act = menu.addAction("💥 グループ解除")
            ungrp_act.triggered.connect(self.ungroup_selected_items)
            
            layer_act = menu.addAction("🏷️ レイヤー変更...")
            layer_act.triggered.connect(self.prompt_change_selected_layer)

            has_image = any(isinstance(i, QGraphicsPixmapItem) for i in selected)
            if has_image:
                opacity_act = menu.addAction("🌫️ 透明度を変更...")
                opacity_act.triggered.connect(self.set_selected_image_opacity)

        else:
            arrow_cfg_act = menu.addAction("🏹 矢印の設定 (形状・向き)...")
            arrow_cfg_act.triggered.connect(self.prompt_arrow_settings)
            menu.addSeparator()

            if self.copied_shapes_buffer:
                paste_act = menu.addAction("📋 ペースト (Ctrl+V)")
                paste_act.triggered.connect(self.paste_from_clipboard)
                menu.addSeparator()
            
            select_all_act = menu.addAction("☑ すべて選択 (Ctrl+A)")
            select_all_act.triggered.connect(lambda: [i.setSelected(True) for i in self.scene.items() if i not in (self.paper_guide_item, getattr(self, 'custom_print_rect_item', None))])
            
            new_act = menu.addAction("📄 DXFファイルを読み込む...")
            new_act.triggered.connect(lambda: self.import_dxf_file())

            new_proj_act = menu.addAction("📄 キャンバスクリア (新規)")
            new_proj_act.triggered.connect(self.new_project)

        menu.exec(event.globalPos())

    # --- ハッチング機能 ---
    def _item_to_shapely_polygon(self, item):
        rect = item.sceneTransform().mapRect(item.boundingRect())
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        if w <= 0 or h <= 0: return None

        if isinstance(item, QGraphicsEllipseItem):
            cx, cy, rx, ry = rect.center().x(), rect.center().y(), w / 2.0, h / 2.0
            pts = [(cx + rx * math.cos(2 * math.pi * i / 64), cy + ry * math.sin(2 * math.pi * i / 64)) for i in range(64)]
            return Polygon(pts)
        elif isinstance(item, QGraphicsPolygonItem):
            poly_f = item.polygon()
            pts = [(pt.x(), pt.y()) for pt in poly_f]
            if len(pts) >= 3: return Polygon(pts)
        elif isinstance(item, QGraphicsRectItem):
            return Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
        elif isinstance(item, QGraphicsPathItem):
            path = item.path()
            pts = []
            for i in range(path.elementCount()):
                elem = path.elementAt(i)
                pts.append((elem.x, elem.y))
            if len(pts) >= 3: return Polygon(pts)
        else:
            return Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
        return None

    def apply_hatching(self):
        selected = self.scene.selectedItems()
        if not selected:
            QMessageBox.warning(self, "通知", "ハッチングを行うオブジェクトを選択してください。")
            return

        polygons = []
        for item in selected:
            poly = self._item_to_shapely_polygon(item)
            if poly and poly.is_valid and not poly.is_empty:
                polygons.append(poly)

        if not polygons:
            QMessageBox.warning(self, "エラー", "有効な閉図形が選択されていません。")
            return

        target_geom = None
        if len(polygons) == 1:
            target_geom = polygons[0]
        else:
            options = [
                "1. 選択オブジェクト個別にハッチング",
                "2. 外枠から内側を除外（中抜きハッチング）",
                "3. オブジェクトの重なり部分のみ (AND)",
                "4. オブジェクトの重なっていない部分のみ (XOR/差分)"
            ]
            item_str, ok = QInputDialog.getItem(self, "ハッチング範囲の指定", "ハッチングの作図範囲を選択してください:", options, 0, False)
            if not ok: return

            if item_str.startswith("1"):
                target_geom = MultiPolygon(polygons)
            elif item_str.startswith("2"):
                polygons.sort(key=lambda p: p.area, reverse=True)
                outer = polygons[0]
                for inner in polygons[1:]:
                    outer = outer.difference(inner)
                target_geom = outer
            elif item_str.startswith("3"):
                target_geom = polygons[0]
                for p in polygons[1:]:
                    target_geom = target_geom.intersection(p)
            elif item_str.startswith("4"):
                target_geom = polygons[0]
                for p in polygons[1:]:
                    target_geom = target_geom.symmetric_difference(p)

        if target_geom is None or target_geom.is_empty:
            QMessageBox.information(self, "通知", "該当するハッチング領域がありません。")
            return

        self.start_history_record()
        pen = QPen(self.get_display_color(self.hatch_color), self.hatch_thickness, self.hatch_style)

        bounds = target_geom.bounds
        minx, miny, maxx, maxy = bounds
        cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
        w, h = maxx - minx, maxy - miny
        diag = math.hypot(w, h) * 2.0

        rad = math.radians(self.hatch_angle)
        spacing = max(2.0, self.hatch_spacing)
        num_lines = int(diag / spacing) + 1

        for i in range(-num_lines, num_lines + 1):
            offset = i * spacing
            rx1 = cx + (-diag) * math.cos(rad) - offset * math.sin(rad)
            ry1 = cy + (-diag) * math.sin(rad) + offset * math.cos(rad)
            rx2 = cx + diag * math.cos(rad) - offset * math.sin(rad)
            ry2 = cy + diag * math.sin(rad) + offset * math.cos(rad)

            line = LineString([(rx1, ry1), (rx2, ry2)])
            try:
                inter = target_geom.intersection(line)
                if not inter.is_empty:
                    geoms = [inter] if isinstance(inter, LineString) else (list(inter.geoms) if hasattr(inter, 'geoms') else [])
                    for g in geoms:
                        if isinstance(g, LineString):
                            coords = list(g.coords)
                            h_item = self.scene.addLine(coords[0][0], coords[0][1], coords[-1][0], coords[-1][1], pen)
                            self.shapes.append({"type": "line", "p1": coords[0], "p2": coords[-1], "layer": self.active_layer, "color": self.hatch_color, "item": h_item})
            except Exception:
                pass

        self.commit_history_record()

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

    # --- グループ化機能 ---
    def group_selected_items(self):
        selected = self.scene.selectedItems()
        if len(selected) < 2:
            QMessageBox.warning(self, "通知", "グループ化するには2つ以上の要素を選択してください。")
            return False
        self.start_history_record()
        group_item = self.scene.createItemGroup(selected)
        group_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        group_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.shapes.append({"type": "group", "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item_count": len(selected), "item": group_item})
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

    # --- ブロック機能 ---
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
            elif stype == "circle": s_copy["center"] = (s_copy["center"][0] - bx, s_copy["center"][1] - by)
            elif stype == "polyline": s_copy["points"] = [(px - bx, py - by) for px, py in s_copy["points"]]
            elif stype in ["point", "text"]: s_copy["pos"] = (s_copy["pos"][0] - bx, s_copy["pos"][1] - by)
            rel_shapes.append(s_copy)

        self.blocks[block_name] = {"category": category, "base_pt": (bx, by), "shapes": rel_shapes}
        self.start_history_record()
        for item in selected_items: self.safe_remove_item(item)
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
            if stype == "line": item = self.scene.addLine(px + s["p1"][0], py + s["p1"][1], px + s["p2"][0], py + s["p2"][1], pen)
            elif stype == "rect": item = self.scene.addRect(px + min(s["p1"][0], s["p2"][0]), py + min(s["p1"][1], s["p2"][1]), abs(s["p2"][0] - s["p1"][0]), abs(s["p2"][1] - s["p1"][1]), pen)
            elif stype == "circle": item = self.scene.addEllipse(px + s["center"][0] - s["radius"], py + s["center"][1] - s["radius"], 2 * s["radius"], 2 * s["radius"], pen)
            else: continue
            group_items.append(item)

        if group_items:
            group = self.scene.createItemGroup(group_items)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            self.shapes.append({"type": "block_ref", "block_name": block_name, "pos": (px, py), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": group})
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

    def _add_cloned_shape_record(self, orig_item, cloned_item):
        for shape in list(self.shapes):
            if shape.get("item") == orig_item:
                s_copy = {k: v for k, v in shape.items() if k != "item"}
                s_copy = copy.deepcopy(s_copy)
                s_copy["item"] = cloned_item
                self.shapes.append(s_copy)
                break

    # --- 移動・複写実行処理 ---
    def execute_move_selected(self, base_pt, target_pt):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        dx, dy = target_pt.x() - base_pt.x(), target_pt.y() - base_pt.y()
        self.start_history_record()
        for item in selected_items: item.moveBy(dx, dy)
        for shape in self.shapes:
            if shape.get("item") and shape["item"].isSelected():
                self._translate_shape(shape, dx, dy)
        self.commit_history_record()

    def execute_copy_selected(self, base_pt, target_pt):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        dx, dy = target_pt.x() - base_pt.x(), target_pt.y() - base_pt.y()
        self.start_history_record()
        self.scene.clearSelection()
        for shape in list(self.shapes):
            item = shape.get("item")
            if item and item in selected_items:
                s_copy = {k: v for k, v in shape.items() if k != "item"}
                s_copy = copy.deepcopy(s_copy)
                self._translate_shape(s_copy, dx, dy)
                new_item = self._recreate_shape_item(s_copy)
                if new_item:
                    new_item.setSelected(True)
                    s_copy["item"] = new_item
                    self.shapes.append(s_copy)
        self.commit_history_record()

    # --- 変形・特殊編集コマンド ---
    def execute_edit_command(self):
        selected = self.scene.selectedItems()
        if not selected: return
        self.start_history_record()
        pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)

        for item in selected:
            rect = item.sceneTransform().mapRect(item.boundingRect())
            if self.mode == "ROTATE":
                item.setTransformOriginPoint(item.boundingRect().center())
                item.setRotation(item.rotation() + self.rotate_angle)

            elif self.mode == "ROTATE_COPY":
                cloned = self._clone_item(item)
                if cloned:
                    cloned.setTransformOriginPoint(cloned.boundingRect().center())
                    cloned.setRotation(cloned.rotation() + self.rotate_angle)
                    self._add_cloned_shape_record(item, cloned)

            elif self.mode == "SCALE":
                item.setScale(item.scale() * self.scale_factor_val)

            elif self.mode == "SCALE_COPY":
                cloned = self._clone_item(item)
                if cloned:
                    cloned.setScale(cloned.scale() * self.scale_factor_val)
                    self._add_cloned_shape_record(item, cloned)

            elif self.mode == "MIRROR":
                item.setTransformOriginPoint(item.boundingRect().center())
                item.setTransform(item.transform().scale(-1, 1))

            elif self.mode == "MIRROR_COPY":
                cloned = self._clone_item(item)
                if cloned:
                    cloned.setTransformOriginPoint(cloned.boundingRect().center())
                    cloned.setTransform(cloned.transform().scale(-1, 1))
                    self._add_cloned_shape_record(item, cloned)

            elif self.mode == "OFFSET":
                line = LineString([(rect.left(), rect.top()), (rect.right(), rect.bottom())])
                offset_line = line.parallel_offset(self.offset_dist, 'left')
                if not offset_line.is_empty:
                    c = list(offset_line.coords)
                    o_item = self.scene.addLine(c[0][0], c[0][1], c[1][0], c[1][1], pen)
                    self.shapes.append({"type": "line", "p1": c[0], "p2": c[-1], "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": o_item})

            elif self.mode == "ARRAY":
                for r in range(self.array_rows):
                    for c in range(self.array_cols):
                        if r == 0 and c == 0: continue
                        dx, dy = c * self.array_col_gap, r * self.array_row_gap
                        if isinstance(item, QGraphicsEllipseItem):
                            new_item = self.scene.addEllipse(rect.x() + dx, rect.y() + dy, rect.width(), rect.height(), pen)
                            self.shapes.append({"type": "circle", "center": (rect.center().x()+dx, rect.center().y()+dy), "radius": rect.width()/2, "layer": self.active_layer, "color": self.current_color, "item": new_item})
                        else:
                            new_item = self.scene.addRect(rect.x() + dx, rect.y() + dy, rect.width(), rect.height(), pen)
                            self.shapes.append({"type": "rect", "p1": (rect.x()+dx, rect.y()+dy), "p2": (rect.x()+dx+rect.width(), rect.y()+dx+rect.height()), "layer": self.active_layer, "color": self.current_color, "item": new_item})
        self.commit_history_record()

    # --- 数値指定・一括作図機能（ピッチ連続複写対応） ---
    def process_coordinate_input(self, x, y, is_relative=False):
        if is_relative:
            base_x, base_y = (self.start_point.x(), self.start_point.y()) if self.start_point else (self.shapes[-1]["pos"] if self.shapes and "pos" in self.shapes[-1] else (0.0, 0.0))
            target_pt = QPointF(base_x + x, base_y + y)
        else: target_pt = QPointF(x, y)
        cx, cy, pen = target_pt.x(), target_pt.y(), QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)

        self.start_history_record()
        if self.mode == "POINT":
            r = max(2.0, self.current_thickness)
            item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen, QBrush(self.get_display_color(self.current_color)))
            self.shapes.append({"type": "point", "pos": (cx, cy), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
        elif self.mode in ["LINE", "RECT", "CIRCLE"]:
            if self.start_point is None: self.start_point = target_pt
            else:
                x1, y1 = self.start_point.x(), self.start_point.y()
                if self.mode == "LINE":
                    item = self.scene.addLine(x1, y1, cx, cy, pen)
                    self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (cx, cy), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
                self.start_point = None
        self.commit_history_record()

    def generate_pitch_points(self, count, dx, dy, start_pos=None):
        selected_items = self.scene.selectedItems()
        self.start_history_record()

        if selected_items:
            for i in range(1, count + 1):
                offset_x, offset_y = dx * i, dy * i
                for shape in list(self.shapes):
                    item = shape.get("item")
                    if item and item in selected_items:
                        s_copy = {k: v for k, v in shape.items() if k != "item"}
                        s_copy = copy.deepcopy(s_copy)
                        self._translate_shape(s_copy, offset_x, offset_y)
                        new_item = self._recreate_shape_item(s_copy)
                        if new_item:
                            s_copy["item"] = new_item
                            self.shapes.append(s_copy)
        else:
            if not start_pos:
                start_pos = self.mapToScene(self.viewport().rect().center())
            disp_color = self.get_display_color(self.current_color)
            pen = QPen(disp_color, self.current_thickness, self.current_style)
            r = max(2.0, self.current_thickness)
            for i in range(1, count + 1):
                cx, cy = start_pos.x() + dx * i, start_pos.y() + dy * i
                item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen, QBrush(disp_color))
                self.shapes.append({
                    "type": "point", "pos": (cx, cy), "layer": self.active_layer,
                    "color": self.current_color, "thickness": self.current_thickness,
                    "style": self.current_style, "item": item
                })
        self.commit_history_record()

    def generate_concentric_shapes(self, shape_type, base_size, step_val, is_multiplier, count, sides=4, center=None):
        cx, cy = (center.x(), center.y()) if center else (self.mapToScene(self.viewport().rect().center()).x(), self.mapToScene(self.viewport().rect().center()).y())
        self.start_history_record()
        pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
        current_r = base_size
        for i in range(count):
            if shape_type == "CIRCLE":
                item = self.scene.addEllipse(cx - current_r, cy - current_r, 2 * current_r, 2 * current_r, pen)
                self.shapes.append({"type": "circle", "center": (cx, cy), "radius": current_r, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
            elif shape_type == "RECT":
                item = self.scene.addRect(cx - current_r, cy - current_r, 2 * current_r, 2 * current_r, pen)
                self.shapes.append({"type": "rect", "p1": (cx - current_r, cy - current_r), "p2": (cx + current_r, cy + current_r), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
            elif shape_type == "POLYGON":
                pts = self._calc_regular_polygon_points_by_angle(cx, cy, current_r, sides, 0.0)
                item = self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in pts]), pen, QBrush(Qt.BrushStyle.NoBrush))
                self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
            current_r = current_r * step_val if is_multiplier else current_r + step_val
        self.commit_history_record()

    def _calc_regular_polygon_points_by_angle(self, cx, cy, radius, sides, start_angle_deg):
        base_angle = math.radians(start_angle_deg)
        return [(cx + radius * math.cos(base_angle + 2 * math.pi * i / sides), cy + radius * math.sin(base_angle + 2 * math.pi * i / sides)) for i in range(sides)]

    # --- キーボード・ショートカット操作 ---
    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        key = event.key()

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif key == Qt.Key.Key_Escape:
            self.set_mode("SELECT")
        elif modifiers & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_A:
            for item in self.scene.items():
                if item not in (self.paper_guide_item, getattr(self, 'custom_print_rect_item', None)):
                    item.setSelected(True)
        elif modifiers & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_C:
            self.copy_selected_to_clipboard()
        elif modifiers & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_V:
            self.paste_from_clipboard()
        elif modifiers & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_D:
            self.duplicate_selected()
        else:
            super().keyPressEvent(event)

    def copy_selected_to_clipboard(self):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        self.copied_shapes_buffer.clear()
        for shape in self.shapes:
            item = shape.get("item")
            if item and item.isSelected():
                s_copy = {k: v for k, v in shape.items() if k != "item"}
                s_copy = copy.deepcopy(s_copy)
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
            elif stype in ["point", "text", "block_ref", "table"]:
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
                s_copy = {k: v for k, v in shape.items() if k != "item"}
                s_copy = copy.deepcopy(s_copy)
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
        elif isinstance(item, QGraphicsPixmapItem): new_item = self.scene.addPixmap(item.pixmap())
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

        if stype == "line":
            item = self.scene.addLine(shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1], pen)
        elif stype == "arrow":
            item = self.scene.addLine(shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1], pen)
            self._update_arrow_shape_graphics(shape)
        elif stype == "rect":
            x1, y1, x2, y2 = shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1]
            item = self.scene.addRect(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2), pen)
        elif stype == "circle":
            cx, cy, r = shape["center"][0], shape["center"][1], shape["radius"]
            item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
        elif stype == "arc":
            cx, cy, r = shape["center"][0], shape["center"][1], shape["radius"]
            st, sp = shape["start_angle"], shape["span_angle"]
            path = QPainterPath()
            path.arcMoveTo(cx - r, cy - r, 2 * r, 2 * r, st)
            path.arcTo(cx - r, cy - r, 2 * r, 2 * r, st, sp)
            item = self.scene.addPath(path, pen)
        elif stype in ["polyline", "spline"]:
            pts = [QPointF(pt[0], pt[1]) for pt in shape["points"]]
            path = QPainterPath(); path.moveTo(pts[0])
            for pt in pts[1:]: path.lineTo(pt)
            if shape.get("is_closed"): path.closeSubpath()
            item = self.scene.addPath(path, pen)
        elif stype == "text":
            item = self.scene.addText(shape["text"])
            item.setDefaultTextColor(color); item.setFont(QFont("Meiryo", int(shape.get("font_size", 12))))
            item.setPos(shape["pos"][0], shape["pos"][1])

        if item:
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        return item

    # --- モード切替・描画リセット制御 ---
    def finish_multi_point_mode(self):
        self.click_points.clear(); self.poly_points.clear()
        for item in self.poly_temp_items: self.safe_remove_item(item)
        self.poly_temp_items.clear(); self.update_snap_marker(None, None)
        if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None
        self.start_point = None

    def finish_polyline(self):
        self.start_history_record()
        if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None
            
        if len(self.poly_points) > 1:
            pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
            if self.mode == "SPLINE":
                for item in self.poly_temp_items: self.safe_remove_item(item)
                path = QPainterPath()
                path.moveTo(QPointF(self.poly_points[0][0], self.poly_points[0][1]))
                for i in range(1, len(self.poly_points)-1):
                    p1, p2 = self.poly_points[i], self.poly_points[i+1]
                    path.quadTo(QPointF(p1[0], p1[1]), QPointF((p1[0]+p2[0])/2, (p1[1]+p2[1])/2))
                path.lineTo(QPointF(self.poly_points[-1][0], self.poly_points[-1][1]))
                item = self.scene.addPath(path, pen)
                self.shapes.append({"type": "spline", "points": list(self.poly_points), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
            else:
                is_closed = (self.mode == "POLYGON")
                path = QPainterPath()
                path.moveTo(QPointF(self.poly_points[0][0], self.poly_points[0][1]))
                for p in self.poly_points[1:]: path.lineTo(QPointF(p[0], p[1]))
                if is_closed: path.closeSubpath()
                item = self.scene.addPath(path, pen)
                for tmp in self.poly_temp_items: self.safe_remove_item(tmp)
                self.shapes.append({"type": "polyline", "points": list(self.poly_points), "is_closed": is_closed, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
        elif len(self.poly_points) == 1:
            for item in self.poly_temp_items: self.safe_remove_item(item)
            
        self.poly_points.clear(); self.poly_temp_items.clear()
        self.commit_history_record()

    def clear_trim_preview(self):
        if getattr(self, 'trim_preview_item', None) and self.trim_preview_item.scene() == self.scene:
            self.scene.removeItem(self.trim_preview_item)
            self.trim_preview_item = None

    def set_mode(self, mode):
        self.finish_multi_point_mode()
        self.clear_trim_preview()
        self.move_base_pt = None; self.angle_dim_first_line = None
        self.setMouseTracking(mode in ["TRIM", "BREAK", "TRACE_CALIBRATE", "FILLET"])

        if mode in ["ROTATE", "ROTATE_COPY", "SCALE", "SCALE_COPY", "MIRROR", "MIRROR_COPY", "OFFSET", "ARRAY"]:
            self.mode = mode; self.execute_edit_command(); self.set_mode("SELECT"); return
        elif mode == "HATCH":
            self.apply_hatching(); self.set_mode("SELECT"); return
        elif mode == "CLOUD_OBJECT":
            self.convert_selected_to_cloud(); self.set_mode("SELECT"); return

        if mode == "REG_POLYGON":
            sides, ok = QInputDialog.getInt(self, "正多角形作成", "角の数 (角数) を入力してください:", self.preset_poly_sides, 3, 32, 1)
            if ok: self.preset_poly_sides = sides
            else: mode = "SELECT"

        self.mode = mode
        self.mode_changed.emit(mode)
        is_select = (mode in ["SELECT", "CLOUD_OBJECT", "HATCH", "ROTATE", "ROTATE_COPY", "SCALE", "SCALE_COPY", "MIRROR", "MIRROR_COPY", "OFFSET", "ARRAY", "MOVE", "COPY"])
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if mode == "SELECT" else QGraphicsView.DragMode.NoDrag)

        for item in self.scene.items():
            if item not in (self.paper_guide_item, getattr(self, 'custom_print_rect_item', None)):
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_select)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, mode == "SELECT")

    # --- マウスイベント制御 ---
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
                    self.shapes.append({
                        "type": "text", "text": input_txt.strip(), "pos": (pos.x(), pos.y()),
                        "font_size": font_size, "layer": self.active_layer, "color": text_color,
                        "thickness": self.current_thickness, "style": self.current_style, "item": t_item
                    })
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
                path = QPainterPath()
                path.arcMoveTo(cx - r, cy - r, 2 * r, 2 * r, st)
                path.arcTo(cx - r, cy - r, 2 * r, 2 * r, st, sp)
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
                self.temp_item = self.scene.addLine(x1 - 5000, y1, x1 + 5000, y1, pen_preview)
            elif self.mode == "V_LINE":
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
            item = self.scene.addLine(x1 - 5000, y1, x1 + 5000, y1, pen)
            self.shapes.append({"type": "line", "p1": (x1 - 5000, y1), "p2": (x1 + 5000, y1), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

        elif self.mode == "V_LINE":
            item = self.scene.addLine(x1, y1 - 5000, x1, y1 + 5000, pen)
            self.shapes.append({"type": "line", "p1": (x1, y1 - 5000), "p2": (x1, y1 + 5000), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

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

    def add_leader_with_auto_measure(self, p1, p2):
        auto_text = self.calculate_shape_measurements(p1)
        if auto_text: text = auto_text
        else:
            input_txt, ok = QInputDialog.getText(self, "引き出し線注釈", "注釈文字を入力してください:")
            if not ok or not input_txt: return
            text = input_txt

        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)
        l_item = self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
        self._draw_arrow_head_shape(p1, math.atan2(p2.y() - p1.y(), p2.x() - p1.x()))
        landing = 40 if p2.x() >= p1.x() else -40
        p3_x = p2.x() + landing
        self.scene.addLine(p2.x(), p2.y(), p3_x, p2.y(), pen)
        t_item = self.scene.addText(text)
        t_item.setDefaultTextColor(disp_color)
        t_item.setFont(QFont("Meiryo", int(max(11, self.current_thickness * 4))))
        rect = t_item.boundingRect()
        t_item.setPos(p2.x() if landing > 0 else (p3_x - rect.width()), p2.y() - rect.height() + (rect.height() * 0.15))
        self.shapes.append({"type": "leader", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "text": text, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": l_item})

    def _shape_to_shapely(self, shape):
        stype = shape.get("type")
        if stype in ["line", "dimension", "arrow", "leader"]: return LineString([shape["p1"], shape["p2"]])
        elif stype == "rect": x1, y1, x2, y2 = shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1]; return LineString([(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)])
        elif stype in ["circle", "arc"]: cx, cy, r = shape["center"][0], shape["center"][1], shape["radius"]; return Point(cx, cy).buffer(r).boundary
        elif stype in ["polyline", "spline"]:
            pts = shape["points"]
            if len(pts) < 2: return None
            return LineString(pts + [pts[0]]) if shape.get("is_closed") else LineString(pts)
        return None

    def _translate_shape(self, shape, dx, dy):
        stype = shape.get("type")
        if stype in ["line", "dimension", "arrow", "leader", "rect"]:
            shape["p1"] = (shape["p1"][0] + dx, shape["p1"][1] + dy)
            shape["p2"] = (shape["p2"][0] + dx, shape["p2"][1] + dy)
        elif stype in ["circle", "ellipse", "arc"]: shape["center"] = (shape["center"][0] + dx, shape["center"][1] + dy)
        elif stype in ["polyline", "spline"]: shape["points"] = [(px + dx, py + dy) for px, py in shape["points"]]
        elif stype in ["point", "text", "block_ref", "table"]: shape["pos"] = (shape["pos"][0] + dx, shape["pos"][1] + dy)

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

    def _find_extend_target(self, pos):
        click_pt = Point(pos.x(), pos.y())
        target_shape = None
        min_dist = 15.0
        for shape in self.shapes:
            geom = self._shape_to_shapely(shape)
            if geom and geom.geom_type in ['LineString', 'MultiLineString']:
                d = geom.distance(click_pt)
                if d < min_dist: min_dist, target_shape = d, shape
        if not target_shape or target_shape.get("type") not in ["line", "polyline"]: return None, None

        stype = target_shape["type"]
        coords = [target_shape["p1"], target_shape["p2"]] if stype == "line" else target_shape["points"]
        if len(coords) < 2: return None, None

        p_start, p_end = QPointF(*coords[0]), QPointF(*coords[-1])
        extend_from_start = math.hypot(pos.x() - p_start.x(), pos.y() - p_start.y()) < math.hypot(pos.x() - p_end.x(), pos.y() - p_end.y())
        near_pt, far_pt = (p_start, p_end) if extend_from_start else (p_end, p_start)

        dx, dy = near_pt.x() - far_pt.x(), near_pt.y() - far_pt.y()
        length = math.hypot(dx, dy)
        if length < 1e-4: return None, None

        ray_ls = LineString([(near_pt.x(), near_pt.y()), (near_pt.x() + (dx / length) * 50000.0, near_pt.y() + (dy / length) * 50000.0)])
        closest_inter_pt, min_inter_dist = None, float("inf")

        for shape in self.shapes:
            if shape == target_shape: continue
            other_geom = self._shape_to_shapely(shape)
            if other_geom and ray_ls.intersects(other_geom):
                inter = ray_ls.intersection(other_geom)
                pts = [inter] if isinstance(inter, Point) else list(inter.geoms) if isinstance(inter, MultiPoint) else []
                for pt in pts:
                    dist = math.hypot(pt.x - near_pt.x(), pt.y - near_pt.y())
                    if 1.0 < dist < min_inter_dist: min_inter_dist, closest_inter_pt = dist, QPointF(pt.x, pt.y)

        if closest_inter_pt:
            new_shape = dict(target_shape)
            if stype == "line":
                if extend_from_start: new_shape["p1"] = (closest_inter_pt.x(), closest_inter_pt.y())
                else: new_shape["p2"] = (closest_inter_pt.x(), closest_inter_pt.y())
            elif stype == "polyline":
                pts = list(target_shape["points"])
                if extend_from_start: pts[0] = (closest_inter_pt.x(), closest_inter_pt.y())
                else: pts[-1] = (closest_inter_pt.x(), closest_inter_pt.y())
                new_shape["points"] = pts
            return target_shape, new_shape
        return None, None

    def execute_fillet_chamfer(self, pos):
        click_pt = Point(pos.x(), pos.y())
        target_shapes = []
        for shape in self.shapes:
            if shape.get("type") == "line":
                geom = LineString([shape["p1"], shape["p2"]])
                if geom.distance(click_pt) < 20.0: target_shapes.append(shape)
        if len(target_shapes) < 2: return

        s1, s2 = target_shapes[0], target_shapes[1]
        p1, p2 = QPointF(*s1["p1"]), QPointF(*s1["p2"])
        p3, p4 = QPointF(*s2["p1"]), QPointF(*s2["p2"])

        den = (p1.x()-p2.x())*(p3.y()-p4.y()) - (p1.y()-p2.y())*(p3.x()-p4.x())
        if den == 0: return 
        px = ((p1.x()*p2.y() - p1.y()*p2.x())*(p3.x()-p4.x()) - (p1.x()-p2.x())*(p3.x()*p4.y() - p3.y()*p4.x())) / den
        py = ((p1.x()*p2.y() - p1.y()*p2.x())*(p3.y()-p4.y()) - (p1.y()-p2.y())*(p3.x()*p4.y() - p3.y()*p4.x())) / den

        self.start_history_record()
        pen1 = QPen(self.get_display_color(s1.get("color", self.current_color)), self.current_thickness, self.current_style)
        pen2 = QPen(self.get_display_color(s2.get("color", self.current_color)), self.current_thickness, self.current_style)

        item1 = self.scene.addLine(s1["p1"][0], s1["p1"][1], px, py, pen1)
        item2 = self.scene.addLine(px, py, s2["p2"][0], s2["p2"][1], pen2)

        if s1 in self.shapes: self.shapes.remove(s1)
        if s2 in self.shapes: self.shapes.remove(s2)
        
        self.shapes.append({"type": "line", "p1": s1["p1"], "p2": (px, py), "layer": s1.get("layer", self.active_layer), "color": s1.get("color", self.current_color), "thickness": self.current_thickness, "style": self.current_style, "item": item1})
        self.shapes.append({"type": "line", "p1": (px, py), "p2": s2["p2"], "layer": s2.get("layer", self.active_layer), "color": s2.get("color", self.current_color), "thickness": self.current_thickness, "style": self.current_style, "item": item2})
        self.commit_history_record()

    def join_selected_lines(self):
        selected = self.scene.selectedItems()
        target_shapes = []
        for item in selected:
            for shape in self.shapes:
                if shape.get("item") == item and shape.get("type") in ["line", "polyline"]:
                    target_shapes.append(shape)

        if len(target_shapes) < 2:
            QMessageBox.warning(self, "通知", "結合するには2本以上の線分を選択してください。")
            return

        self.start_history_record()
        all_pts = []
        for s in target_shapes:
            if s["type"] == "line": all_pts.extend([s["p1"], s["p2"]])
            elif s["type"] == "polyline": all_pts.extend(s["points"])
            self.safe_remove_item(s.get("item"))
        
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

    # --- スナップ・トラッキングヘルパー ---
    def set_grid_snap(self, enabled, size=None):
        self.grid_snap_enabled = enabled
        if size: self.grid_size = float(size)

    def set_angle_snap(self, enabled): self.angle_snap_enabled = enabled

    def apply_angle_snap(self, p1, p2):
        if not self.angle_snap_enabled or not p1: return p2
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-4: return p2
        angle_rad = math.atan2(dy, dx)
        snapped_deg = round(math.degrees(angle_rad) / 15.0) * 15.0
        return QPointF(p1.x() + dist * math.cos(math.radians(snapped_deg)), p1.y() + dist * math.sin(math.radians(snapped_deg)))

    def set_otrack_enabled(self, enabled):
        self.otrack_enabled = enabled; self.clear_tracking_lines()

    def clear_tracking_lines(self):
        for item in self.tracking_items: self.safe_remove_item(item)
        self.tracking_items.clear()

    def get_snap_points(self, current_pos=None):
        snaps = []
        for shape in self.shapes:
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
            elif stype in ["circle", "arc"]:
                cx, cy, r = shape["center"][0], shape["center"][1], shape["radius"]
                snaps.append((cx, cy, "CENTER"))
            elif stype in ["polyline", "spline"]:
                pts = shape["points"]
                for p in pts: snaps.append((p[0], p[1], "END"))
                for i in range(len(pts) - 1):
                    snaps.append(((pts[i][0] + pts[i+1][0]) / 2, (pts[i][1] + pts[i+1][1]) / 2, "MID"))

        for item in self.scene.items():
            if isinstance(item, QGraphicsPixmapItem):
                rect = item.sceneBoundingRect()
                x1, y1, x2, y2 = rect.left(), rect.top(), rect.right(), rect.bottom()
                corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                for c in corners: snaps.append((c[0], c[1], "END"))
                snaps.append(((x1 + x2) / 2, y1, "MID"))
                snaps.append(((x1 + x2) / 2, y2, "MID"))
                snaps.append((x1, (y1 + y2) / 2, "MID"))
                snaps.append((x2, (y1 + y2) / 2, "MID"))
                snaps.append(((x1 + x2) / 2, (y1 + y2) / 2, "CENTER"))

        return snaps

    def get_snapped_pos(self, raw_pos):
        self.clear_tracking_lines()
        if self.mode == "SELECT": return raw_pos

        if self.grid_snap_enabled and self.grid_size > 0:
            gx, gy = round(raw_pos.x() / self.grid_size) * self.grid_size, round(raw_pos.y() / self.grid_size) * self.grid_size
            grid_pt = QPointF(gx, gy)
            snaps = self.get_snap_points(raw_pos)
            if not any(math.hypot(raw_pos.x() - x, raw_pos.y() - y) < self.snap_threshold for x, y, _ in snaps):
                self.update_snap_marker(grid_pt, "GRID")
                return grid_pt

        snaps = self.get_snap_points(raw_pos)
        best_pt, best_type, min_dist = raw_pos, None, float("inf")

        for x, y, stype in snaps:
            dist = math.hypot(raw_pos.x() - x, raw_pos.y() - y)
            effective_threshold = self.snap_threshold * 1.5 if stype == "END" else self.snap_threshold
            if dist < effective_threshold:
                weighted_dist = dist * 0.6 if stype == "END" else dist
                if weighted_dist < min_dist:
                    min_dist, best_pt, best_type = weighted_dist, QPointF(x, y), stype

        if min_dist < float("inf"):
            self.update_snap_marker(best_pt, best_type)
            return best_pt

        if self.otrack_enabled and snaps:
            rx, ry = raw_pos.x(), raw_pos.y()
            track_x, track_y, ref_x_pt, ref_y_pt = None, None, None, None
            for x, y, _ in snaps:
                if abs(ry - y) < self.snap_threshold: track_y, ref_y_pt = y, (x, y)
                if abs(rx - x) < self.snap_threshold: track_x, ref_x_pt = x, (x, y)

            final_x = track_x if track_x is not None else rx
            final_y = track_y if track_y is not None else ry
            pen_track = QPen(QColor(0, 180, 255), 1, Qt.PenStyle.DashLine)

            if track_y is not None and ref_y_pt:
                line = self.scene.addLine(-99999, track_y, 99999, track_y, pen_track)
                line.setZValue(98); self.tracking_items.append(line)
            if track_x is not None and ref_x_pt:
                line = self.scene.addLine(track_x, -99999, track_x, 99999, pen_track)
                line.setZValue(98); self.tracking_items.append(line)

            if track_x is not None or track_y is not None:
                snapped_track_pt = QPointF(final_x, final_y)
                self.update_snap_marker(snapped_track_pt, "INTER")
                return snapped_track_pt

        self.update_snap_marker(None, None)
        return raw_pos

    def update_snap_marker(self, pos, stype):
        if self.snap_marker: self.safe_remove_item(self.snap_marker); self.snap_marker = None
        if pos:
            size, colors = 10, {"END": Qt.GlobalColor.red, "MID": Qt.GlobalColor.yellow, "CENTER": Qt.GlobalColor.blue, "INTER": Qt.GlobalColor.cyan, "GRID": Qt.GlobalColor.magenta}
            pen = QPen(colors.get(stype, Qt.GlobalColor.green), 2)
            self.snap_marker = self.scene.addRect(pos.x() - size/2, pos.y() - size/2, size, size, pen)
            self.snap_marker.setZValue(100)

    # --- 色・レイヤーヘルパー ---
    def get_display_color(self, raw_color, is_export=False):
        if not is_export and self.is_dark_mode:
            if raw_color.red() < 50 and raw_color.green() < 50 and raw_color.blue() < 50:
                return QColor(255, 255, 255)
        return raw_color

    def refresh_display_colors(self, is_export=False):
        for shape in self.shapes:
            item = shape.get("item")
            if not item or item.scene() != self.scene: continue
            layer_name = shape.get("layer", "0")
            props = self.layers.get(layer_name, self.layers["0"])
            raw_color = shape.get("color", props["color"])
            disp_color = self.get_display_color(raw_color, is_export=is_export)
            if hasattr(item, "pen") and hasattr(item, "setPen"):
                pen = item.pen(); pen.setColor(disp_color); item.setPen(pen)
            elif hasattr(item, "setDefaultTextColor"):
                item.setDefaultTextColor(disp_color)

    def apply_layer_states(self, is_export=False):
        for shape in list(self.shapes):
            item = shape.get("item")
            if not item or item.scene() != self.scene: continue
            layer_name = shape.get("layer", "0")
            props = self.layers.get(layer_name, self.layers["0"])
            
            item.setVisible(props["visible"] and (props["printable"] if is_export else True))
            is_movable = (self.mode == "SELECT" and not props["locked"])
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_movable)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, is_movable)
            
            raw_color = shape.get("color", props["color"])
            disp_color = self.get_display_color(raw_color, is_export=is_export)
            thickness = shape.get("thickness", props["thickness"])
            style = shape.get("style", props["style"])
            
            if hasattr(item, "pen") and hasattr(item, "setPen"):
                pen = item.pen(); pen.setColor(disp_color); pen.setWidth(thickness); pen.setStyle(style); item.setPen(pen)
            elif hasattr(item, "setDefaultTextColor"):
                item.setDefaultTextColor(disp_color)
        self.refresh_display_colors(is_export=is_export)

    def set_active_layer(self, layer_name):
        if layer_name in self.layers:
            self.active_layer = layer_name
            props = self.layers[layer_name]
            self.current_color = props["color"]
            self.current_thickness = props["thickness"]
            self.current_style = props["style"]

    # --- 履歴管理 ---
    def start_history_record(self):
        self._before_items = set(self.scene.items())
        self._before_shapes_len = len(self.shapes)

    def commit_history_record(self):
        added_items = list(set(self.scene.items()) - self._before_items)
        added_items = [i for i in added_items if i not in (self.temp_item, self.snap_marker, self.paper_guide_item, getattr(self, 'custom_print_rect_item', None)) and i not in self.poly_temp_items and i != getattr(self, 'trim_preview_item', None)]
        added_shapes = self.shapes[self._before_shapes_len:]
        if added_items or added_shapes:
            self.undo_stack.append(("add", added_items, added_shapes))
            self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack: return
        action = self.undo_stack.pop()
        if action[0] == "add":
            for item in action[1]: self.safe_remove_item(item)
            for s in action[2]:
                if s in self.shapes: self.shapes.remove(s)
        self.redo_stack.append(action)

    def redo(self):
        if not self.redo_stack: return
        action = self.redo_stack.pop()
        if action[0] == "add":
            for item in action[1]: self.scene.addItem(item)
            for s in action[2]: self.shapes.append(s)
        self.undo_stack.append(action)

    # --- 基本・設定変更機能 ---
    def set_color(self, color): self.current_color = color; self.apply_property_to_selected(color=color)
    def set_thickness(self, thickness): self.current_thickness = thickness; self.apply_property_to_selected(thickness=thickness)
    def set_style(self, style): self.current_style = style; self.apply_property_to_selected(style=style)
    def set_cloud_pitch(self, pitch): self.cloud_pitch = pitch
    def set_cloud_arc_height(self, height): self.cloud_arc_height = height

    def apply_property_to_selected(self, color=None, thickness=None, style=None):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        self.start_history_record()
        for shape in self.shapes:
            item = shape.get("item")
            if item and item in selected_items:
                if color is not None: shape["color"] = color
                if thickness is not None: shape["thickness"] = thickness
                if style is not None: shape["style"] = style
        self.apply_layer_states()
        self.commit_history_record()

    def bring_selected_to_front(self):
        for item in self.scene.selectedItems(): item.setZValue(max([i.zValue() for i in self.scene.items()] or [0]) + 1)
    def send_selected_to_back(self):
        for item in self.scene.selectedItems(): item.setZValue(min([i.zValue() for i in self.scene.items()] or [0]) - 1)

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

    def change_selected_layer(self, new_layer_name):
        selected_items = self.scene.selectedItems()
        if not selected_items or new_layer_name not in self.layers: return
        self.start_history_record()
        for shape in self.shapes:
            item = shape.get("item")
            if item and item.isSelected(): shape["layer"] = new_layer_name
        self.apply_layer_states()
        self.commit_history_record()

    def prompt_change_selected_layer(self):
        layer_names = list(self.layers.keys())
        current_idx = layer_names.index(self.active_layer) if self.active_layer in layer_names else 0
        layer_name, ok = QInputDialog.getItem(self, "レイヤー変更", "変更先のレイヤーを選択してください:", layer_names, current_idx, False)
        if ok and layer_name: self.change_selected_layer(layer_name)

    def set_selected_image_opacity(self):
        selected = [i for i in self.scene.selectedItems() if isinstance(i, QGraphicsPixmapItem)]
        if not selected: return
        current_opacity = int(selected[0].opacity() * 100)
        val, ok = QInputDialog.getInt(self, "透明度の変更", "不透明度を入力してください (10〜100%):", current_opacity, 10, 100, 5)
        if ok:
            self.start_history_record()
            for item in selected: item.setOpacity(val / 100.0)
            self.commit_history_record()

    # --- ヘルパー処理 ---
    def safe_remove_item(self, item):
        if isinstance(item, list):
            for i in item:
                if i and i.scene() == self.scene: self.scene.removeItem(i)
        elif item and item.scene() == self.scene:
            self.scene.removeItem(item)

    def set_canvas_bg_color(self, bg_type):
        if bg_type == "DARK":
            self.setBackgroundBrush(QBrush(QColor(30, 30, 30))); self.is_dark_mode = True
        elif bg_type == "WHITE":
            self.setBackgroundBrush(QBrush(QColor(255, 255, 255))); self.is_dark_mode = False
        elif bg_type == "GRAY":
            self.setBackgroundBrush(QBrush(QColor(220, 220, 220))); self.is_dark_mode = False
        self.apply_layer_states(is_export=False)

    # --- 入出力 & 印刷機能 ---
    def save_to_dxf(self):
        self.finish_polyline()
        file_path, _ = QFileDialog.getSaveFileName(self, "DXF形式で保存", "", "DXF Files (*.dxf)")
        if not file_path: return
        try:
            doc = ezdxf.new('R2010')
            msp = doc.modelspace()
            for l_name, l_props in self.layers.items():
                if not doc.layers.has_entry(l_name):
                    c = l_props["color"]
                    layer = doc.layers.add(name=l_name)
                    layer.rgb = (c.red(), c.green(), c.blue())

            for shape in self.shapes:
                color = shape.get("color", self.current_color)
                layer = shape.get("layer", "0")
                attribs = {'true_color': ezdxf.rgb2int((color.red(), color.green(), color.blue())), 'layer': layer}
                stype = shape.get("type")

                if stype in ["line", "arrow", "dimension", "leader"]:
                    msp.add_line((shape["p1"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p2"][1]), dxfattribs=attribs)
                elif stype == "rect":
                    x1, y1, x2, y2 = shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1]
                    msp.add_lwpolyline([(x1, -y1), (x2, -y1), (x2, -y2), (x1, -y2)], close=True, dxfattribs=attribs)
                elif stype == "circle":
                    msp.add_circle((shape["center"][0], -shape["center"][1]), shape["radius"], dxfattribs=attribs)
                elif stype in ["polyline", "spline"]:
                    msp.add_lwpolyline([(x, -y) for x, y in shape["points"]], close=shape.get("is_closed", False), dxfattribs=attribs)

            doc.saveas(file_path)
            QMessageBox.information(self, "成功", f"DXFファイルを保存しました:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"DXF保存失敗:\n{e}")

    def import_jww_file(self, file_path):
        if not os.path.exists(file_path): return
        try:
            self.start_history_record()
            with open(file_path, "rb") as f:
                header = f.read(16)
                if not header.startswith(b"JWW_Drawing_Data"):
                    QMessageBox.warning(self, "エラー", "有効な Jw_cad (.jww) ファイルではありません。")
                    return
            jww_layer_name = f"JWW_{os.path.basename(file_path)}"
            if jww_layer_name not in self.layers:
                self.layers[jww_layer_name] = {"color": QColor(0, 100, 200), "thickness": 2, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True}
            QMessageBox.information(self, "完了", f"JWW図面要素を取り込みました:\n{os.path.basename(file_path)}")
            self.commit_history_record()
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"JWW読み込み失敗:\n{e}")

    def set_background_file(self, file_path):
        if file_path.lower().endswith('.pdf'):
            try:
                doc = pymupdf.open(file_path)
                pix = doc[0].get_pixmap(dpi=150)
                fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
                pixmap = QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt))
            except Exception as e: QMessageBox.critical(self, "エラー", f"PDF読込失敗:\n{e}"); return
        else: pixmap = QPixmap(file_path)
        self.scene.addPixmap(pixmap)
        self.scene.setSceneRect(-100000, -100000, 200000, 200000)

    def insert_image_or_pdf(self, file_path, pos=None):
        self.start_history_record()
        if file_path.lower().endswith('.pdf'):
            try:
                doc = pymupdf.open(file_path)
                pix = doc[0].get_pixmap(dpi=150)
                fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
                pixmap = QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt))
            except Exception as e: QMessageBox.critical(self, "エラー", f"PDF挿入失敗:\n{e}"); return
        else:
            pixmap = QPixmap(file_path)
            if pixmap.isNull(): return

        pixmap_item = self.scene.addPixmap(pixmap)
        is_select = (self.mode == "SELECT")
        pixmap_item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable, is_select)
        pixmap_item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable, is_select)
        if pos: pixmap_item.setPos(pos)
        else:
            scene_center = self.mapToScene(self.viewport().rect().center())
            pixmap_item.setPos(scene_center.x() - pixmap.width() / 2, scene_center.y() - pixmap.height() / 2)
        self.commit_history_record()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: super().dragEnterEvent(event)

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path:
                ext = os.path.splitext(file_path)[1].lower()
                if ext in ['.jww', '.jws']: self.import_jww_file(file_path)
                elif ext == '.dxf': self.import_dxf_file(file_path)
                else: self.insert_image_or_pdf(file_path, self.mapToScene(event.position().toPoint()))
        event.acceptProposedAction()

    def update_paper_guide(self, size_id=None, orientation=None, scale=None, show=None, custom_rect=None):
        if size_id is not None: self.paper_size_id = size_id
        if orientation is not None: self.paper_orientation = orientation
        if scale is not None: self.paper_scale = scale
        if show is not None: self.show_paper_guide = show

        if self.paper_guide_item: self.safe_remove_item(self.paper_guide_item); self.paper_guide_item = None
        if not self.show_paper_guide: return

        if custom_rect is not None:
            w_px, h_px, pos_x, pos_y = custom_rect.width(), custom_rect.height(), custom_rect.x(), custom_rect.y()
        else:
            mm_sizes = {QPageSize.PageSizeId.A4: (297, 210), QPageSize.PageSizeId.A3: (420, 297), QPageSize.PageSizeId.A2: (594, 420), QPageSize.PageSizeId.B4: (364, 257), QPageSize.PageSizeId.B5: (257, 182)}
            w_mm, h_mm = mm_sizes.get(self.paper_size_id, (297, 210))
            if self.paper_orientation == QPageLayout.Orientation.Landscape: w_mm, h_mm = max(w_mm, h_mm), min(w_mm, h_mm)
            else: w_mm, h_mm = min(w_mm, h_mm), max(w_mm, h_mm)
            w_px, h_px, pos_x, pos_y = w_mm * self.paper_scale, h_mm * self.paper_scale, 0, 0

        pen = QPen(QColor(0, 120, 215), max(2, int(self.paper_scale * 0.05)) if custom_rect is None else 2, Qt.PenStyle.DashDotLine)
        self.paper_guide_item = self.scene.addRect(0, 0, w_px, h_px, pen)
        self.paper_guide_item.setPos(pos_x, pos_y); self.paper_guide_item.setZValue(-10)

    def fit_paper_guide_to_selected(self):
        selected = self.scene.selectedItems()
        target = selected[0] if selected else next((i for i in self.scene.items() if isinstance(i, QGraphicsPixmapItem)), None)
        if target:
            self.update_paper_guide(show=True, custom_rect=target.sceneBoundingRect())
            return True
        return False

    def set_custom_print_rect(self, rect):
        if self.custom_print_rect_item: self.safe_remove_item(self.custom_print_rect_item); self.custom_print_rect_item = None
        if rect and not rect.isEmpty():
            self.custom_print_rect_item = self.scene.addRect(rect, QPen(QColor(255, 102, 0), 2, Qt.PenStyle.DashDotDotLine))
            self.custom_print_rect_item.setZValue(99)

    def clear_custom_print_rect(self):
        if self.custom_print_rect_item: self.safe_remove_item(self.custom_print_rect_item); self.custom_print_rect_item = None

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
        printer.setPageSize(QPageSize(page_size_id)); printer.setPageOrientation(orientation)

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

    def zoom_in(self): self.scale(self.zoom_factor, self.zoom_factor)
    def zoom_out(self): self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
    def zoom_fit(self):
        rect = self.scene.itemsBoundingRect()
        if not rect.isEmpty(): self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        else: self.resetTransform()