import os
import math
import json
import struct
import subprocess
import pymupdf
import ezdxf
from shapely.geometry import LineString, Point, Polygon, MultiPoint, GeometryCollection
from shapely.ops import split, snap

from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QInputDialog, QMessageBox, 
                             QFileDialog, QGraphicsItem, QGraphicsEllipseItem, 
                             QGraphicsLineItem, QGraphicsRectItem, QGraphicsPolygonItem, 
                             QGraphicsPathItem, QGraphicsTextItem, QGraphicsPixmapItem, 
                             QGraphicsItemGroup, QApplication, QMenu)
from PyQt6.QtGui import (QPen, QColor, QPixmap, QPolygonF, QBrush, QFont, QImage, 
                         QPainterPath, QPainter, QPageSize, QPageLayout)
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal

class CADCanvas(QGraphicsView):
    mode_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.scene.setSceneRect(0, 0, 1200, 800)
        self.setAcceptDrops(True)
        
        # 描画基本属性
        self.current_color = QColor(0, 0, 0)
        self.current_thickness = 2
        self.current_style = Qt.PenStyle.SolidLine
        self.mode = "SELECT"
        self.scale_factor = 1.0
        self.is_dark_mode = False

        # Undo / Redo 用の履歴スタック
        self.undo_stack = []
        self.redo_stack = []
        self.copied_items = []
        self._before_items = set()
        self._before_shapes_len = 0

        # 各種パラメータ
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

        # 用紙枠 & 出力範囲設定
        self.show_paper_guide = False
        self.paper_size_id = QPageSize.PageSizeId.A4
        self.paper_orientation = QPageLayout.Orientation.Landscape
        self.paper_scale = 100
        self.paper_guide_item = None
        self.custom_print_rect_item = None

        # グリッド & 同心 & 分割パラメータ
        self.grid_snap_enabled = False
        self.grid_size = 50.0
        self.concentric_center = None
        self.break_first_pt = None
        self.break_target_shape = None

        # レイヤー管理
        self.layers = {
            "0": {"color": QColor(0, 0, 0), "thickness": 2, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True},
            "背景図面": {"color": QColor(120, 120, 120), "thickness": 1, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": True, "printable": True},
            "朱書き": {"color": QColor(255, 0, 0), "thickness": 3, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True},
            "寸法・文字": {"color": QColor(0, 120, 215), "thickness": 1, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True},
            "下書き": {"color": QColor(128, 128, 128), "thickness": 1, "style": Qt.PenStyle.DashLine, "visible": True, "locked": False, "printable": False},
        }
        self.active_layer = "朱書き"

        # ブロック定義データベース & プリセット呼び出し
        self.blocks = {}
        self.init_preset_blocks()

        # ズーム・パン・スナップ設定
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.zoom_factor = 1.15
        self._is_panning = False
        self.angle_snap_enabled = True
        self.start_point = None
        self.temp_item = None
        self.click_points, self.poly_points, self.poly_temp_items = [], [], []
        self.snap_marker = None
        self.snap_threshold = 15.0
        self.shapes = []

    def safe_remove_item(self, item):
        if item and item.scene() == self.scene:
            self.scene.removeItem(item)

    # --- プリセットブロック初期化 ---
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
        self.blocks["片開きドア (900mm)"] = {
            "category": "建具", "base_pt": (0, 0),
            "shapes": [
                {"type": "rect", "p1": (0, -40), "p2": (40, 0), "color": QColor(0, 0, 0)},
                {"type": "rect", "p1": (900, -40), "p2": (940, 0), "color": QColor(0, 0, 0)},
                {"type": "line", "p1": (40, 0), "p2": (40, -900), "color": QColor(0, 0, 0)},
                {"type": "polyline", "points": [(40, 0), (900, 0)], "is_closed": False, "color": QColor(0, 0, 0)},
                {"type": "polyline", "points": [(40 + 860 * math.cos(math.radians(-90 + i * 5)), -860 * math.sin(math.radians(-90 + i * 5))) for i in range(19)], "is_closed": False, "color": QColor(120, 120, 120)}
            ]
        }
        self.blocks["洋式便器 (700x400)"] = {
            "category": "設備", "base_pt": (0, 0),
            "shapes": [
                {"type": "rect", "p1": (-200, -100), "p2": (200, 50), "color": QColor(0, 0, 0)},
                {"type": "circle", "center": (0, 300), "radius": 180, "color": QColor(0, 0, 0)},
                {"type": "line", "p1": (-180, 50), "p2": (-180, 300), "color": QColor(0, 0, 0)},
                {"type": "line", "p1": (180, 50), "p2": (180, 300), "color": QColor(0, 0, 0)}
            ]
        }
        self.blocks["事務デスクセット (1200x700)"] = {
            "category": "家具", "base_pt": (0, 0),
            "shapes": [
                {"type": "rect", "p1": (-600, -350), "p2": (600, 350), "color": QColor(0, 0, 0)},
                {"type": "rect", "p1": (-220, 400), "p2": (220, 850), "color": QColor(0, 0, 0)},
                {"type": "circle", "center": (0, 625), "radius": 200, "color": QColor(0, 0, 0)}
            ]
        }
        self.blocks["普通乗用車 (4700x1800)"] = {
            "category": "車両", "base_pt": (0, 0),
            "shapes": [
                {"type": "rect", "p1": (-2350, -900), "p2": (2350, 900), "color": QColor(0, 0, 0)},
                {"type": "line", "p1": (-1200, -800), "p2": (-1200, 800), "color": QColor(100, 100, 100)},
                {"type": "line", "p1": (1200, -800), "p2": (1200, 800), "color": QColor(100, 100, 100)},
                {"type": "rect", "p1": (-1000, -750), "p2": (1000, 750), "color": QColor(0, 0, 0)}
            ]
        }
        self.blocks["改訂番号記号 (No.)"] = {
            "category": "記号・注釈", "base_pt": (0, 0),
            "shapes": [
                {"type": "polyline", "points": [(0, -200), (173, 100), (-173, 100)], "is_closed": True, "color": QColor(255, 0, 0)},
                {"type": "text", "text": "1", "pos": (-15, -60), "font_size": 22, "color": QColor(255, 0, 0)}
            ]
        }

    # --- 表示色（7番色/ダークモード自動反転・コントラスト補正） ---
    def get_display_color(self, raw_color, is_export=False):
        """黒背景時に黒色・暗色を画面上だけ視認性の高い白に補正"""
        if not is_export and self.is_dark_mode:
            # 輝度(Luminance)計算：暗い色（輝度100未満）なら画面表示のみ白にする
            lum = 0.299 * raw_color.red() + 0.587 * raw_color.green() + 0.114 * raw_color.blue()
            if lum < 100:
                return QColor(255, 255, 255)
        return raw_color

    def refresh_display_colors(self, is_export=False):
        for shape, item in zip(self.shapes, [i for i in self.scene.items() if i != self.paper_guide_item and i != getattr(self, 'custom_print_rect_item', None)]):
            raw_color = shape.get("color", QColor(0, 0, 0))
            disp_color = self.get_display_color(raw_color, is_export=is_export)

            if hasattr(item, "pen") and hasattr(item, "setPen"):
                pen = item.pen()
                pen.setColor(disp_color)
                item.setPen(pen)
                if hasattr(item, "brush") and hasattr(item, "setBrush") and item.brush().style() != Qt.BrushStyle.NoBrush:
                    item.setBrush(QBrush(disp_color))
            elif isinstance(item, QGraphicsTextItem):
                item.setDefaultTextColor(disp_color)

    def set_canvas_bg_color(self, bg_type):
        if bg_type == "DARK":
            self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))
            self.is_dark_mode = True
        elif bg_type == "WHITE":
            self.setBackgroundBrush(QBrush(QColor(255, 255, 255)))
            self.is_dark_mode = False
        elif bg_type == "GRAY":
            self.setBackgroundBrush(QBrush(QColor(220, 220, 220)))
            self.is_dark_mode = False
        self.apply_layer_states(is_export=False)

    # --- レイヤー管理 ＆ 連動ロジック ---
    def set_active_layer(self, layer_name):
        """アクティブレイヤーを切り替え、デフォルト作図属性（色・太さ・線種）を自動同期"""
        if layer_name in self.layers:
            self.active_layer = layer_name
            props = self.layers[layer_name]
            self.current_color = props["color"]
            self.current_thickness = props["thickness"]
            self.current_style = props["style"]

    def apply_layer_states(self, is_export=False):
        """全レイヤーの「表示/非表示」「ロック」「印刷対象」および「色・太さ・線種」を全オブジェクトへ一括反映"""
        for shape, item in zip(self.shapes, [i for i in self.scene.items() if i != self.paper_guide_item and i != getattr(self, 'custom_print_rect_item', None)]):
            layer_name = shape.get("layer", "0")
            props = self.layers.get(layer_name, self.layers["0"])

            # 1. 表示 / 印刷非表示の制御
            if is_export:
                item.setVisible(props["visible"] and props["printable"])
            else:
                item.setVisible(props["visible"])

            # 2. ロック（選択・移動許可）の制御
            is_movable = (self.mode == "SELECT" and not props["locked"])
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_movable)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, is_movable)

            # 3. レイヤープロパティ（色・太さ・線種）を図形データおよび描画ペンへ連動
            shape["color"] = props["color"]
            disp_color = self.get_display_color(props["color"], is_export=is_export)

            if hasattr(item, "pen") and hasattr(item, "setPen"):
                pen = item.pen()
                pen.setColor(disp_color)
                pen.setWidth(props["thickness"])
                pen.setStyle(props["style"])
                item.setPen(pen)
                if hasattr(item, "brush") and hasattr(item, "setBrush") and item.brush().style() != Qt.BrushStyle.NoBrush:
                    item.setBrush(QBrush(disp_color))
            elif isinstance(item, QGraphicsTextItem):
                item.setDefaultTextColor(disp_color)

        self.refresh_display_colors(is_export=is_export)

    # --- ブロック操作 ---
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

        self.blocks[block_name] = {
            "category": category,
            "base_pt": (bx, by),
            "shapes": rel_shapes
        }

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
            pen = QPen(color, self.current_thickness, self.current_style)

            if stype == "line":
                item = self.scene.addLine(px + s["p1"][0], py + s["p1"][1], px + s["p2"][0], py + s["p2"][1], pen)
            elif stype == "rect":
                rx = px + min(s["p1"][0], s["p2"][0])
                ry = py + min(s["p1"][1], s["p2"][1])
                rw, rh = abs(s["p2"][0] - s["p1"][0]), abs(s["p2"][1] - s["p1"][1])
                item = self.scene.addRect(rx, ry, rw, rh, pen)
            elif stype == "circle":
                cx, cy, r = px + s["center"][0], py + s["center"][1], s["radius"]
                item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
            elif stype == "polyline":
                pts = [QPointF(px + pt[0], py + pt[1]) for pt in s["points"]]
                if s.get("is_closed"):
                    item = self.scene.addPolygon(QPolygonF(pts), pen, QBrush(Qt.BrushStyle.NoBrush))
                else:
                    path = QPainterPath()
                    path.moveTo(pts[0])
                    for pt in pts[1:]: path.lineTo(pt)
                    item = self.scene.addPath(path, pen)
            elif stype == "text":
                item = self.scene.addText(s["text"])
                item.setDefaultTextColor(color)
                item.setFont(QFont("Meiryo", s.get("font_size", 14)))
                item.setPos(px + s["pos"][0], py + s["pos"][1])
            else:
                continue
            group_items.append(item)

        if group_items:
            group = self.scene.createItemGroup(group_items)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            self.shapes.append({
                "type": "block_ref",
                "block_name": block_name,
                "pos": (px, py),
                "layer": self.active_layer,
                "color": self.current_color
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

    # --- 右クリックコンテキストメニュー & オブジェクト操作 ---
    def contextMenuEvent(self, event):
        if self.mode == "SELECT":
            item = self.itemAt(event.pos())
            if item and item not in (self.paper_guide_item, getattr(self, 'custom_print_rect_item', None)):
                if not item.isSelected():
                    self.scene.clearSelection()
                    item.setSelected(True)

                menu = QMenu(self)
                front_act = menu.addAction("⬆ 最前面へ移動")
                back_act = menu.addAction("⬇ 最背面へ移動")
                menu.addSeparator()
                dup_act = menu.addAction("📋 複製")
                del_act = menu.addAction("🗑 削除")

                action = menu.exec(event.globalPos())
                if action == front_act: self.bring_selected_to_front()
                elif action == back_act: self.send_selected_to_back()
                elif action == dup_act: self.duplicate_selected()
                elif action == del_act: self.delete_selected()
                return
        super().contextMenuEvent(event)

    def bring_selected_to_front(self):
        for item in self.scene.selectedItems():
            max_z = max([i.zValue() for i in self.scene.items()] or [0])
            item.setZValue(max_z + 1)

    def send_selected_to_back(self):
        for item in self.scene.selectedItems():
            min_z = min([i.zValue() for i in self.scene.items()] or [0])
            item.setZValue(min_z - 1)

    def duplicate_selected(self):
        self.start_history_record()
        for item in self.scene.selectedItems():
            cloned = self._clone_item(item)
            if cloned:
                cloned.moveBy(20, 20)
                cloned.setSelected(True)
        self.commit_history_record()

    def delete_selected(self):
        deleted = self.scene.selectedItems()
        if deleted:
            self.start_history_record()
            for item in deleted: self.safe_remove_item(item)
            self.commit_history_record()

    # --- JSONプロジェクト保存・復元 ---
    def save_project_json(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "プロジェクトを保存", "", "CAD Project Files (*.json)")
        if not file_path: return

        try:
            serializable_layers = {}
            for name, props in self.layers.items():
                serializable_layers[name] = {
                    "color": props["color"].name(),
                    "thickness": props["thickness"],
                    "style": int(props["style"]),
                    "visible": props["visible"],
                    "locked": props["locked"],
                    "printable": props["printable"]
                }

            serializable_shapes = []
            for s in self.shapes:
                s_copy = dict(s)
                if "color" in s_copy and isinstance(s_copy["color"], QColor):
                    s_copy["color"] = s_copy["color"].name()
                serializable_shapes.append(s_copy)

            data = {
                "version": "1.0",
                "layers": serializable_layers,
                "blocks": self.blocks,
                "shapes": serializable_shapes,
                "paper_scale": self.paper_scale,
                "active_layer": self.active_layer
            }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

            QMessageBox.information(self, "成功", f"プロジェクトを保存しました:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"保存失敗:\n{e}")

    def load_project_json(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "プロジェクトを開く", "", "CAD Project Files (*.json)")
        if not file_path: return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.scene.clear()
            self.shapes.clear()
            self.paper_guide_item = None
            self.custom_print_rect_item = None

            self.layers.clear()
            for name, props in data.get("layers", {}).items():
                self.layers[name] = {
                    "color": QColor(props["color"]),
                    "thickness": props["thickness"],
                    "style": Qt.PenStyle(props["style"]),
                    "visible": props["visible"],
                    "locked": props["locked"],
                    "printable": props["printable"]
                }

            self.active_layer = data.get("active_layer", "0")
            self.paper_scale = data.get("paper_scale", 100)
            self.blocks = data.get("blocks", {})

            raw_shapes = data.get("shapes", [])
            for s in raw_shapes:
                if "color" in s: s["color"] = QColor(s["color"])
                stype = s.get("type")
                color = self.get_display_color(s.get("color", self.current_color))
                pen = QPen(color, self.current_thickness, self.current_style)

                if stype == "line":
                    self.scene.addLine(s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1], pen)
                elif stype == "rect":
                    x1, y1, x2, y2 = s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1]
                    self.scene.addRect(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2), pen)
                elif stype == "circle":
                    cx, cy, r = s["center"][0], s["center"][1], s["radius"]
                    self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
                elif stype == "polyline":
                    pts = [QPointF(pt[0], pt[1]) for pt in s["points"]]
                    if s.get("is_closed"):
                        self.scene.addPolygon(QPolygonF(pts), pen, QBrush(Qt.BrushStyle.NoBrush))
                    else:
                        path = QPainterPath(); path.moveTo(pts[0])
                        for pt in pts[1:]: path.lineTo(pt)
                        self.scene.addPath(path, pen)
                elif stype == "text":
                    t = self.scene.addText(s["text"])
                    t.setDefaultTextColor(color)
                    t.setFont(QFont("Meiryo", s.get("font_size", 12)))
                    t.setPos(s["pos"][0], s["pos"][1])

                self.shapes.append(s)

            self.apply_layer_states()
            QMessageBox.information(self, "成功", f"プロジェクトを読み込みました:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"読み込み失敗:\n{e}")

    # --- JWW インポート ---
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
                self.layers[jww_layer_name] = {
                    "color": QColor(0, 100, 200),
                    "thickness": 2,
                    "style": Qt.PenStyle.SolidLine,
                    "visible": True,
                    "locked": False,
                    "printable": True
                }

            dxf_temp_path = file_path + ".temp.dxf"
            if os.path.exists("jww2dxf.exe"):
                subprocess.run(["jww2dxf.exe", file_path, dxf_temp_path], capture_output=True)
                if os.path.exists(dxf_temp_path):
                    doc = ezdxf.readfile(dxf_temp_path)
                    msp = doc.modelspace()
                    for e in msp:
                        if e.dxftype() == 'LINE':
                            p1, p2 = e.dxf.start, e.dxf.end
                            disp_c = self.get_display_color(self.current_color)
                            self.scene.addLine(p1.x, -p1.y, p2.x, -p2.y, QPen(disp_c, 2))
                            self.shapes.append({
                                "type": "line",
                                "p1": (p1.x, -p1.y), "p2": (p2.x, -p2.y),
                                "layer": jww_layer_name, "color": self.current_color
                            })
                    os.remove(dxf_temp_path)
                    self.commit_history_record()
                    QMessageBox.information(self, "完了", f"JWWファイルを読み込みました:\n{os.path.basename(file_path)}")
                    return

            QMessageBox.information(self, "完了", f"JWW図面要素を取り込みました:\n{os.path.basename(file_path)}")
            self.commit_history_record()

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"JWW読み込み失敗:\n{e}")

    # --- Undo / Redo エンジン ---
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
        elif action[0] == "delete":
            for item in action[1]: self.scene.addItem(item)
            for s in action[2]: self.shapes.append(s)
        self.redo_stack.append(action)

    def redo(self):
        if not self.redo_stack: return
        action = self.redo_stack.pop()
        if action[0] == "add":
            for item in action[1]: self.scene.addItem(item)
            for s in action[2]: self.shapes.append(s)
        elif action[0] == "delete":
            for item in action[1]: self.safe_remove_item(item)
            for s in action[2]: 
                if s in self.shapes: self.shapes.remove(s)
        self.undo_stack.append(action)

    # --- プロパティ設定 ---
    def set_color(self, color):
        self.current_color = color
        self.apply_property_to_selected(color=color)

    def set_thickness(self, thickness):
        self.current_thickness = thickness
        self.apply_property_to_selected(thickness=thickness)

    def set_style(self, style):
        self.current_style = style
        self.apply_property_to_selected(style=style)

    def apply_property_to_selected(self, color=None, thickness=None, style=None):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        self.start_history_record()
        for item in selected_items:
            if hasattr(item, "pen") and hasattr(item, "setPen"):
                pen = item.pen()
                if color is not None:
                    pen.setColor(self.get_display_color(color))
                    if hasattr(item, "brush") and hasattr(item, "setBrush") and item.brush().style() != Qt.BrushStyle.NoBrush:
                        item.setBrush(QBrush(self.get_display_color(color)))
                if thickness is not None: pen.setWidth(thickness)
                if style is not None: pen.setStyle(style)
                item.setPen(pen)
            elif isinstance(item, QGraphicsTextItem) and color is not None:
                item.setDefaultTextColor(self.get_display_color(color))
        self.commit_history_record()

    def set_angle_snap(self, enabled): self.angle_snap_enabled = enabled
    def set_cloud_pitch(self, val): self.cloud_pitch = float(val)
    def set_cloud_arc_height(self, val): self.cloud_arc_height = float(val)
    def set_grid_snap(self, enabled, size=None):
        self.grid_snap_enabled = enabled
        if size: self.grid_size = float(size)

    # --- モード変更 ＆ 選択制御 ---
    def set_mode(self, mode):
        self.finish_multi_point_mode()
        self.clear_trim_preview()
        self.setMouseTracking(mode in ["TRIM", "BREAK"])
        
        if mode == "REG_POLYGON":
            current_sides = getattr(self, 'polygon_sides', 6)
            sides, ok = QInputDialog.getInt(self, "正多角形", "頂点数（角の数）を入力してください:", current_sides, 3, 32)
            if ok: self.polygon_sides = sides
            else: self.set_mode("SELECT"); return

        self.mode = mode
        self.mode_changed.emit(mode)

        is_select = (mode in ["SELECT", "CLOUD_OBJECT", "HATCH", "ROTATE", "SCALE", "MIRROR", "OFFSET", "ARRAY"])
        if mode == "SELECT": self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        else: self.setDragMode(QGraphicsView.DragMode.NoDrag)

        for item in self.scene.items():
            if isinstance(item, QGraphicsItem) and item not in (self.paper_guide_item, self.custom_print_rect_item):
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_select)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, mode == "SELECT")

    def _clone_item(self, item):
        pen = item.pen() if hasattr(item, "pen") else QPen()
        brush = item.brush() if hasattr(item, "brush") else QBrush()
        new_item = None
        if isinstance(item, QGraphicsLineItem): new_item = self.scene.addLine(item.line(), pen)
        elif isinstance(item, QGraphicsRectItem): new_item = self.scene.addRect(item.rect(), pen, brush)
        elif isinstance(item, QGraphicsEllipseItem): new_item = self.scene.addEllipse(item.rect(), pen, brush)
        elif isinstance(item, QGraphicsPolygonItem): new_item = self.scene.addPolygon(item.polygon(), pen, brush)
        elif isinstance(item, QGraphicsPathItem): new_item = self.scene.addPath(item.path(), pen)
        elif isinstance(item, QGraphicsTextItem):
            new_item = self.scene.addText(item.toPlainText(), item.font())
            new_item.setDefaultTextColor(item.defaultTextColor())
        elif isinstance(item, QGraphicsPixmapItem):
            new_item = self.scene.addPixmap(item.pixmap())

        if new_item:
            new_item.setPos(item.pos())
            new_item.setRotation(item.rotation())
            new_item.setScale(item.scale())
            new_item.setTransformOriginPoint(item.transformOriginPoint())
            new_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            new_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        return new_item

    # --- 背景・画像・PDF挿入 ---
    def set_background_file(self, file_path):
        if file_path.lower().endswith('.pdf'):
            try:
                doc = pymupdf.open(file_path)
                pix = doc[0].get_pixmap(dpi=150)
                fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
                pixmap = QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt))
            except Exception as e:
                QMessageBox.critical(self, "エラー", f"PDF読込失敗:\n{e}"); return
        else:
            pixmap = QPixmap(file_path)
        self.scene.addPixmap(pixmap)
        self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())

    def insert_image_or_pdf(self, file_path, pos=None):
        self.start_history_record()
        if file_path.lower().endswith('.pdf'):
            try:
                doc = pymupdf.open(file_path)
                pix = doc[0].get_pixmap(dpi=150)
                fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
                pixmap = QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt))
            except Exception as e:
                QMessageBox.critical(self, "エラー", f"PDF挿入失敗:\n{e}"); return
        else:
            pixmap = QPixmap(file_path)
            if pixmap.isNull(): return

        pixmap_item = self.scene.addPixmap(pixmap)
        is_select = (self.mode == "SELECT")
        pixmap_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_select)
        pixmap_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, is_select)
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
                if ext in ['.jww', '.jws']:
                    self.import_jww_file(file_path)
                else:
                    pos = self.mapToScene(event.position().toPoint())
                    self.insert_image_or_pdf(file_path, pos)
        event.acceptProposedAction()

    # --- ズーム・パン ---
    def wheelEvent(self, event):
        selected_items = self.scene.selectedItems()
        if selected_items and (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
            self.start_history_record()
            for item in selected_items:
                item.setTransformOriginPoint(item.boundingRect().center())
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    scale_factor = 1.05 if event.angleDelta().y() > 0 else (1 / 1.05)
                    item.setScale(item.scale() * scale_factor)
                elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    angle_delta = 5.0 if event.angleDelta().y() > 0 else -5.0
                    item.setRotation(item.rotation() + angle_delta)
            self.commit_history_record()
            return

        if event.angleDelta().y() > 0: self.scale(self.zoom_factor, self.zoom_factor)
        else: self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)

    def zoom_in(self): self.scale(self.zoom_factor, self.zoom_factor)
    def zoom_out(self): self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
    def zoom_fit(self):
        rect = self.scene.itemsBoundingRect()
        if not rect.isEmpty(): self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        else: self.resetTransform()

    # --- 用紙ガイド枠 ＆ 自由指定出力枠 ---
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
            w_px, h_px = custom_rect.width(), custom_rect.height()
            pos_x, pos_y = custom_rect.x(), custom_rect.y()
        else:
            mm_sizes = {
                QPageSize.PageSizeId.A4: (297, 210), QPageSize.PageSizeId.A3: (420, 297),
                QPageSize.PageSizeId.A2: (594, 420), QPageSize.PageSizeId.B4: (364, 257),
                QPageSize.PageSizeId.B5: (257, 182),
            }
            w_mm, h_mm = mm_sizes.get(self.paper_size_id, (297, 210))
            if self.paper_orientation == QPageLayout.Orientation.Landscape:
                w_mm, h_mm = max(w_mm, h_mm), min(w_mm, h_mm)
            else:
                w_mm, h_mm = min(w_mm, h_mm), max(w_mm, h_mm)

            w_px = w_mm * self.paper_scale
            h_px = h_mm * self.paper_scale
            pos_x, pos_y = 0, 0

        pen_width = max(2, int(self.paper_scale * 0.05)) if custom_rect is None else 2
        pen = QPen(QColor(0, 120, 215), pen_width, Qt.PenStyle.DashDotLine)
        
        self.paper_guide_item = self.scene.addRect(0, 0, w_px, h_px, pen)
        self.paper_guide_item.setPos(pos_x, pos_y)
        self.paper_guide_item.setZValue(-10)
        self.paper_guide_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.paper_guide_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    def fit_paper_guide_to_selected(self):
        selected = self.scene.selectedItems()
        target = None
        if selected:
            items = [i for i in selected if i not in (self.paper_guide_item, self.custom_print_rect_item)]
            if items: target = items[0]

        if not target:
            pixmaps = [i for i in self.scene.items() if isinstance(i, QGraphicsPixmapItem)]
            if pixmaps: target = pixmaps[0]

        if target:
            rect = target.sceneBoundingRect()
            self.update_paper_guide(show=True, custom_rect=rect)
            return True
        return False

    def set_custom_print_rect(self, rect):
        if self.custom_print_rect_item:
            self.safe_remove_item(self.custom_print_rect_item)
            self.custom_print_rect_item = None

        if rect and not rect.isEmpty():
            pen = QPen(QColor(255, 102, 0), 2, Qt.PenStyle.DashDotDotLine)
            self.custom_print_rect_item = self.scene.addRect(rect, pen)
            self.custom_print_rect_item.setZValue(99)
            self.custom_print_rect_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            self.custom_print_rect_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    def clear_custom_print_rect(self):
        if self.custom_print_rect_item:
            self.safe_remove_item(self.custom_print_rect_item)
            self.custom_print_rect_item = None

    def _get_target_render_rect(self):
        if self.custom_print_rect_item:
            return self.custom_print_rect_item.sceneBoundingRect()
        if self.show_paper_guide and self.paper_guide_item:
            return self.paper_guide_item.sceneBoundingRect()
        rect = self.scene.itemsBoundingRect()
        return rect if not rect.isEmpty() else self.scene.sceneRect()

    def print_scene(self):
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QPageSize(self.paper_size_id if self.show_paper_guide else QPageSize.PageSizeId.A4))
        printer.setPageOrientation(self.paper_orientation if self.show_paper_guide else QPageLayout.Orientation.Landscape)

        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QPrintDialog.DialogCode.Accepted:
            self.apply_layer_states(is_export=True)
            if self.paper_guide_item: self.paper_guide_item.setVisible(False)
            if self.custom_print_rect_item: self.custom_print_rect_item.setVisible(False)

            painter = QPainter(printer)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            page_rect = printer.pageLayout().paintRectPixels(printer.resolution())
            source_rect = self._get_target_render_rect()
            
            self.scene.render(painter, QRectF(page_rect), source_rect)
            painter.end()

            self.apply_layer_states(is_export=False)
            if self.paper_guide_item and self.show_paper_guide: self.paper_guide_item.setVisible(True)
            if self.custom_print_rect_item: self.custom_print_rect_item.setVisible(True)
            
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
        page_rect = printer.pageLayout().paintRectPixels(printer.resolution())
        source_rect = self._get_target_render_rect()

        self.scene.render(painter, QRectF(page_rect), source_rect)
        painter.end()

        self.apply_layer_states(is_export=False)
        if self.paper_guide_item and self.show_paper_guide: self.paper_guide_item.setVisible(True)
        if self.custom_print_rect_item: self.custom_print_rect_item.setVisible(True)

        QMessageBox.information(self, "成功", f"PDFを出力しました:\n{file_path}")

    # --- スナップ ---
    def apply_angle_snap(self, p1, p2):
        if not self.angle_snap_enabled or not p1: return p2
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-4: return p2
        angle_rad = math.atan2(dy, dx)
        snapped_deg = round(math.degrees(angle_rad) / 15.0) * 15.0
        return QPointF(p1.x() + dist * math.cos(math.radians(snapped_deg)), p1.y() + dist * math.sin(math.radians(snapped_deg)))

    def get_snap_points(self, current_pos=None):
        snaps, geoms = [], []

        if self.show_paper_guide and self.paper_guide_item:
            rect = self.paper_guide_item.sceneBoundingRect()
            x1, y1, x2, y2 = rect.left(), rect.top(), rect.right(), rect.bottom()
            corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            for c in corners: snaps.append((c[0], c[1], "END"))
            for i in range(4):
                p1, p2 = corners[i], corners[(i + 1) % 4]
                snaps.append(((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, "MID"))
            snaps.append(((x1 + x2) / 2, (y1 + y2) / 2, "CENTER"))

        if self.custom_print_rect_item:
            rect = self.custom_print_rect_item.sceneBoundingRect()
            x1, y1, x2, y2 = rect.left(), rect.top(), rect.right(), rect.bottom()
            corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            for c in corners: snaps.append((c[0], c[1], "END"))
            for i in range(4):
                p1, p2 = corners[i], corners[(i + 1) % 4]
                snaps.append(((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, "MID"))
            snaps.append(((x1 + x2) / 2, (y1 + y2) / 2, "CENTER"))

        for shape in self.shapes:
            stype = shape.get("type")
            g = None
            if stype in ["line", "dimension", "arrow", "leader"]:
                p1, p2 = shape["p1"], shape["p2"]
                snaps.extend([(p1[0], p1[1], "END"), (p2[0], p2[1], "END"), ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, "MID")])
                g = LineString([p1, p2])
            elif stype == "rect":
                x1, y1, x2, y2 = shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1]
                corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                for c in corners: snaps.append((c[0], c[1], "END"))
                for i in range(4): snaps.append(((corners[i][0] + corners[(i + 1) % 4][0]) / 2, (corners[i][1] + corners[(i + 1) % 4][1]) / 2, "MID"))
                snaps.append(((x1 + x2) / 2, (y1 + y2) / 2, "CENTER"))
                g = LineString(corners + [(x1, y1)])
            elif stype == "circle":
                cx, cy, r = shape["center"][0], shape["center"][1], shape["radius"]
                snaps.append((cx, cy, "CENTER"))
                if current_pos and r > 0:
                    d = math.hypot(current_pos.x() - cx, current_pos.y() - cy)
                    if d > r:
                        a_c, a_t = math.atan2(current_pos.y() - cy, current_pos.x() - cx), math.acos(r / d)
                        for sign in [-1, 1]: snaps.append((cx + r * math.cos(a_c + sign * a_t), cy + r * math.sin(a_c + sign * a_t), "TANGENT"))
                g = Point(cx, cy).buffer(r).boundary
            elif stype == "polyline":
                pts = shape["points"]
                for p in pts: snaps.append((p[0], p[1], "END"))
                for i in range(len(pts) - 1): snaps.append(((pts[i][0] + pts[i+1][0]) / 2, (pts[i][1] + pts[i+1][1]) / 2, "MID"))
                g = LineString(pts)
            elif stype == "point":
                px, py = shape["pos"]
                snaps.append((px, py, "END"))
                g = Point(px, py)
            if g is not None: geoms.append(g)

        for i in range(len(geoms)):
            for j in range(i + 1, len(geoms)):
                try:
                    inter = geoms[i].intersection(geoms[j])
                    if not inter.is_empty:
                        if isinstance(inter, Point): snaps.append((inter.x, inter.y, "INTER"))
                        elif isinstance(inter, MultiPoint):
                            for p in inter.geoms: snaps.append((p.x, p.y, "INTER"))
                except: pass
        return snaps

    def get_snapped_pos(self, raw_pos):
        if self.mode == "SELECT": return raw_pos
        
        if self.grid_snap_enabled and self.grid_size > 0:
            gx = round(raw_pos.x() / self.grid_size) * self.grid_size
            gy = round(raw_pos.y() / self.grid_size) * self.grid_size
            grid_pt = QPointF(gx, gy)
            snaps = self.get_snap_points(raw_pos)
            if not any(math.hypot(raw_pos.x() - x, raw_pos.y() - y) < self.snap_threshold for x, y, _ in snaps):
                self.update_snap_marker(grid_pt, "GRID")
                return grid_pt

        snaps = self.get_snap_points(raw_pos)
        best_pt, best_type, min_dist = raw_pos, None, float("inf")
        for x, y, stype in snaps:
            dist = math.hypot(raw_pos.x() - x, raw_pos.y() - y)
            if dist < self.snap_threshold and dist < min_dist:
                min_dist, best_pt, best_type = dist, QPointF(x, y), stype
        self.update_snap_marker(best_pt if min_dist < float("inf") else None, best_type)
        return best_pt

    def update_snap_marker(self, pos, stype):
        if self.snap_marker:
            self.safe_remove_item(self.snap_marker)
            self.snap_marker = None
        if pos:
            size = 10
            colors = {"END": Qt.GlobalColor.red, "MID": Qt.GlobalColor.yellow, "CENTER": Qt.GlobalColor.blue, 
                      "TANGENT": Qt.GlobalColor.green, "INTER": Qt.GlobalColor.cyan, "GRID": Qt.GlobalColor.magenta}
            self.snap_marker = self.scene.addRect(pos.x() - size / 2, pos.y() - size / 2, size, size, QPen(colors.get(stype, Qt.GlobalColor.green), 2))
            self.snap_marker.setZValue(100)

    # --- 座標数値入力 & 生成系 ---
    def process_coordinate_input(self, x, y, is_relative=False):
        if is_relative:
            if self.start_point:
                base_x, base_y = self.start_point.x(), self.start_point.y()
            elif self.shapes and "pos" in self.shapes[-1]:
                base_x, base_y = self.shapes[-1]["pos"]
            else:
                base_x, base_y = 0.0, 0.0
            target_pt = QPointF(base_x + x, base_y + y)
        else:
            target_pt = QPointF(x, y)

        cx, cy = target_pt.x(), target_pt.y()
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)

        self.start_history_record()

        if self.mode == "POINT":
            r = max(2.0, self.current_thickness)
            self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen, QBrush(disp_color))
            self.shapes.append({"type": "point", "pos": (cx, cy), "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "PRESET_RECT":
            w, h = self.preset_rect_w, self.preset_rect_h
            self.scene.addRect(cx - w / 2, cy - h / 2, w, h, pen)
            self.shapes.append({"type": "rect", "p1": (cx - w/2, cy - h/2), "p2": (cx + w/2, cy + h/2), "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "PRESET_CIRCLE":
            r = self.preset_circle_r
            self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
            self.shapes.append({"type": "circle", "center": (cx, cy), "radius": r, "layer": self.active_layer, "color": self.current_color})
        elif self.mode in ["LINE", "RECT", "CIRCLE", "ARROW", "DIMENSION"]:
            if self.start_point is None:
                self.start_point = target_pt
            else:
                x1, y1 = self.start_point.x(), self.start_point.y()
                if self.mode == "LINE":
                    self.scene.addLine(x1, y1, cx, cy, pen)
                    self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (cx, cy), "layer": self.active_layer, "color": self.current_color})
                elif self.mode == "RECT":
                    rx, ry, rw, rh = min(x1, cx), min(y1, cy), abs(x1 - cx), abs(y1 - cy)
                    self.scene.addRect(rx, ry, rw, rh, pen)
                    self.shapes.append({"type": "rect", "p1": (rx, ry), "p2": (rx + rw, ry + rh), "layer": self.active_layer, "color": self.current_color})
                self.start_point = None

        self.commit_history_record()

    def generate_pitch_points(self, count, dx, dy, start_pos=None):
        if not start_pos:
            start_pos = self.mapToScene(self.viewport().rect().center())
        self.start_history_record()
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)

        for i in range(1, count + 1):
            cx = start_pos.x() + dx * i
            cy = start_pos.y() + dy * i

            if self.mode == "POINT":
                r = max(2.0, self.current_thickness)
                self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen, QBrush(disp_color))
                self.shapes.append({"type": "point", "pos": (cx, cy), "layer": self.active_layer, "color": self.current_color})
            elif self.mode == "PRESET_RECT":
                w, h = self.preset_rect_w, self.preset_rect_h
                self.scene.addRect(cx - w / 2, cy - h / 2, w, h, pen)
                self.shapes.append({"type": "rect", "p1": (cx - w/2, cy - h/2), "p2": (cx + w/2, cy + h/2), "layer": self.active_layer, "color": self.current_color})
            elif self.mode == "PRESET_CIRCLE":
                r = self.preset_circle_r
                self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
                self.shapes.append({"type": "circle", "center": (cx, cy), "radius": r, "layer": self.active_layer, "color": self.current_color})

        self.commit_history_record()

    def generate_concentric_shapes(self, shape_type, base_size, step_val, is_multiplier, count, sides=4, center=None):
        if not center:
            center = self.mapToScene(self.viewport().rect().center())
        cx, cy = center.x(), center.y()

        self.start_history_record()
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)

        current_r = base_size
        for i in range(count):
            if shape_type == "CIRCLE":
                self.scene.addEllipse(cx - current_r, cy - current_r, 2 * current_r, 2 * current_r, pen)
                self.shapes.append({"type": "circle", "center": (cx, cy), "radius": current_r, "layer": self.active_layer, "color": self.current_color})
            elif shape_type == "RECT":
                self.scene.addRect(cx - current_r, cy - current_r, 2 * current_r, 2 * current_r, pen)
                self.shapes.append({"type": "rect", "p1": (cx - current_r, cy - current_r), "p2": (cx + current_r, cy + current_r), "layer": self.active_layer, "color": self.current_color})
            elif shape_type == "POLYGON":
                pts = self._calc_regular_polygon_points_by_angle(cx, cy, current_r, sides, 0.0)
                self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in pts]), pen, QBrush(Qt.BrushStyle.NoBrush))
                self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color})

            if is_multiplier: current_r *= step_val
            else: current_r += step_val

        self.commit_history_record()

    # --- トリム & 分割・部分削除 ロジック ---
    def _shape_to_shapely(self, shape):
        stype = shape.get("type")
        if stype in ["line", "dimension", "arrow", "leader"]:
            return LineString([shape["p1"], shape["p2"]])
        elif stype == "rect":
            x1, y1, x2, y2 = shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1]
            return LineString([(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)])
        elif stype == "circle":
            cx, cy, r = shape["center"][0], shape["center"][1], shape["radius"]
            return Point(cx, cy).buffer(r).boundary
        elif stype == "polyline":
            pts = shape["points"]
            if len(pts) < 2: return None
            return LineString(pts + [pts[0]]) if shape.get("is_closed") else LineString(pts)
        return None

    def _find_trim_target_and_split(self, pos):
        click_pt = Point(pos.x(), pos.y())
        target_shape = None
        min_dist = 12.0

        for shape in self.shapes:
            geom = self._shape_to_shapely(shape)
            if geom and geom.geom_type in ['LineString', 'MultiLineString']:
                d = geom.distance(click_pt)
                if d < min_dist:
                    min_dist = d
                    target_shape = shape

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

        remaining = [s for s in segments if s != best_seg]
        return best_seg, remaining, target_shape

    def clear_trim_preview(self):
        if hasattr(self, 'trim_preview_item') and self.trim_preview_item:
            self.safe_remove_item(self.trim_preview_item)
            self.trim_preview_item = None

    def break_shape_at_points(self, shape, pt1, pt2):
        stype = shape.get("type")
        color = shape.get("color", self.current_color)
        layer = shape.get("layer", self.active_layer)
        pen = QPen(self.get_display_color(color), self.current_thickness, self.current_style)

        p1_f = QPointF(pt1.x(), pt1.y())
        p2_f = QPointF(pt2.x(), pt2.y())
        dist_1_2 = math.hypot(p2_f.x() - p1_f.x(), p2_f.y() - p1_f.y())
        is_single_point = (dist_1_2 < 5.0)

        self.start_history_record()
        if shape in self.shapes: self.shapes.remove(shape)

        if stype == "circle":
            cx, cy, r = shape["center"][0], shape["center"][1], shape["radius"]
            a1 = math.atan2(p1_f.y() - cy, p1_f.x() - cx)
            a2 = math.atan2(p2_f.y() - cy, p2_f.x() - cx) if not is_single_point else a1 + math.radians(0.5)

            deg1 = math.degrees(a1) % 360
            deg2 = math.degrees(a2) % 360
            if deg1 <= deg2: deg1 += 360
            span = deg1 - deg2

            num_pts = max(16, int(span / 4.0))
            arc_pts = [(cx + r * math.cos(math.radians(deg2 + (span * i / num_pts))), cy + r * math.sin(math.radians(deg2 + (span * i / num_pts)))) for i in range(num_pts + 1)]

            self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in arc_pts]), pen, QBrush(Qt.BrushStyle.NoBrush))
            self.shapes.append({"type": "polyline", "points": arc_pts, "is_closed": False, "layer": layer, "color": color})
        else:
            geom = self._shape_to_shapely(shape)
            if geom:
                pt1_g, pt2_g = Point(p1_f.x(), p1_f.y()), Point(p2_f.x(), p2_f.y())
                cut_geom = pt1_g.buffer(0.1).boundary if is_single_point else MultiPoint([pt1_g, pt2_g])
                snapped_g = snap(geom, cut_geom, 2.0)
                split_res = split(snapped_g, cut_geom)
                segs = list(split_res.geoms) if hasattr(split_res, 'geoms') else [split_res]

                for seg in segs:
                    if not is_single_point and (seg.distance(pt1_g) < 3.0 or seg.distance(pt2_g) < 3.0) and seg.length < dist_1_2 * 1.2:
                        continue
                    coords = list(seg.coords)
                    if len(coords) >= 2:
                        if len(coords) == 2:
                            self.scene.addLine(coords[0][0], coords[0][1], coords[1][0], coords[1][1], pen)
                            self.shapes.append({"type": "line", "p1": coords[0], "p2": coords[1], "layer": layer, "color": color})
                        else:
                            self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in coords]), pen, QBrush(Qt.BrushStyle.NoBrush))
                            self.shapes.append({"type": "polyline", "points": coords, "is_closed": False, "layer": layer, "color": color})

        self.commit_history_record()

    # --- 変形 / ハッチング / 雲マーク / 表 ---
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
            elif self.mode == "SCALE":
                item.setScale(item.scale() * self.scale_factor_val)
            elif self.mode == "MIRROR":
                item.setTransform(item.transform().scale(-1, 1))
            elif self.mode == "OFFSET":
                line = LineString([(rect.left(), rect.top()), (rect.right(), rect.bottom())])
                offset_line = line.parallel_offset(self.offset_dist, 'left')
                if not offset_line.is_empty:
                    c = list(offset_line.coords)
                    self.scene.addLine(c[0][0], c[0][1], c[1][0], c[1][1], pen)
            elif self.mode == "ARRAY":
                for r in range(self.array_rows):
                    for c in range(self.array_cols):
                        if r == 0 and c == 0: continue
                        dx, dy = c * self.array_col_gap, r * self.array_row_gap
                        if isinstance(item, QGraphicsEllipseItem): self.scene.addEllipse(rect.x() + dx, rect.y() + dy, rect.width(), rect.height(), pen)
                        else: self.scene.addRect(rect.x() + dx, rect.y() + dy, rect.width(), rect.height(), pen)
        self.commit_history_record()

    def apply_hatching(self):
        selected = self.scene.selectedItems()
        if not selected: return
        self.start_history_record()
        pen = QPen(self.get_display_color(self.hatch_color), self.hatch_thickness, self.hatch_style)

        for item in selected:
            rect = item.sceneTransform().mapRect(item.boundingRect())
            x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
            poly = Point(rect.center().x(), rect.center().y()).buffer(max(w, h)/2) if isinstance(item, QGraphicsEllipseItem) else Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])

            diag = math.hypot(w, h) * 2
            cx, cy, rad = x + w / 2, y + h / 2, math.radians(self.hatch_angle)
            num_lines = int(diag / max(2.0, self.hatch_spacing))

            for i in range(-num_lines, num_lines):
                offset = i * self.hatch_spacing
                rx1 = cx + (-diag) * math.cos(rad) - offset * math.sin(rad)
                ry1 = cy + (-diag) * math.sin(rad) + offset * math.cos(rad)
                rx2 = cx + diag * math.cos(rad) - offset * math.sin(rad)
                ry2 = cy + diag * math.sin(rad) + offset * math.cos(rad)

                try:
                    inter = poly.intersection(LineString([(rx1, ry1), (rx2, ry2)]))
                    if not inter.is_empty:
                        if isinstance(inter, LineString):
                            c = list(inter.coords)
                            self.scene.addLine(c[0][0], c[0][1], c[1][0], c[1][1], pen)
                        elif inter.geom_type == 'MultiLineString':
                            for l in inter.geoms:
                                c = list(l.coords)
                                self.scene.addLine(c[0][0], c[0][1], c[1][0], c[1][1], pen)
                except: pass
        self.commit_history_record()

    def convert_selected_to_cloud(self):
        selected = self.scene.selectedItems()
        if not selected: return
        self.start_history_record()
        pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
        step, arc_height = max(5.0, self.cloud_pitch), self.cloud_arc_height

        for item in selected:
            rect = item.sceneTransform().mapRect(item.boundingRect())
            pts = []
            if isinstance(item, QGraphicsEllipseItem):
                cx, cy, rx, ry = rect.center().x(), rect.center().y(), rect.width() / 2, rect.height() / 2
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
            cloud_path = QPainterPath()
            cloud_path.moveTo(QPointF(pts[0][0], pts[0][1]))

            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
                dx, dy = p2[0] - p1[0], p2[1] - p1[1]
                dist = math.hypot(dx, dy)
                if dist > 0: cloud_path.quadTo(QPointF(mx + (dy / dist) * arc_height, my + (-dx / dist) * arc_height), QPointF(p2[0], p2[1]))

            self.scene.addPath(cloud_path, pen)
            self.safe_remove_item(item)
        self.commit_history_record()

    def add_table_data(self, grid_data, cell_w, cell_h, pos):
        rows = len(grid_data)
        cols = max(len(r) for r in grid_data) if rows > 0 else 0
        if rows == 0 or cols == 0: return
        self.start_history_record()
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, Qt.PenStyle.SolidLine)
        sx, sy = pos.x(), pos.y()

        for r in range(rows + 1): self.scene.addLine(sx, sy + r * cell_h, sx + cols * cell_w, sy + r * cell_h, pen)
        for c in range(cols + 1): self.scene.addLine(sx + c * cell_w, sy, sx + c * cell_w, sy + rows * cell_h, pen)

        font = QFont("Meiryo", 10)
        for r in range(rows):
            for c in range(len(grid_data[r])):
                val = str(grid_data[r][c]).strip()
                if val:
                    t = self.scene.addText(val, font)
                    t.setDefaultTextColor(disp_color)
                    t.setPos(sx + c * cell_w + 5, sy + r * cell_h + 3)
        self.commit_history_record()

    # --- キー ＆ マウス イベント ---
    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        
        if modifiers & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_C:
            self.copied_items = self.scene.selectedItems()
            return
            
        if modifiers & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_V:
            self.start_history_record()
            mime = QApplication.clipboard().mimeData()
            if mime.hasImage():
                pixmap = QPixmap.fromImage(QApplication.clipboard().image())
                pixmap_item = self.scene.addPixmap(pixmap)
                is_select = (self.mode == "SELECT")
                pixmap_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_select)
                pixmap_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, is_select)
                scene_center = self.mapToScene(self.viewport().rect().center())
                pixmap_item.setPos(scene_center.x() - pixmap.width() / 2, scene_center.y() - pixmap.height() / 2)
            elif self.copied_items:
                self.scene.clearSelection()
                for item in self.copied_items:
                    cloned = self._clone_item(item)
                    if cloned:
                        cloned.moveBy(20, 20)
                        cloned.setSelected(True)
            self.commit_history_record()
            return

        if event.key() == Qt.Key.Key_Escape:
            self.set_mode("SELECT")
            return
        elif self.mode in ["POLYLINE", "POLYGON"] and len(self.poly_points) > 0:
            if event.key() == Qt.Key.Key_C and len(self.poly_points) >= 3:
                self.mode = "POLYGON"
                self.finish_polyline()
                return

        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            deleted_items = self.scene.selectedItems()
            if deleted_items:
                for item in deleted_items: self.safe_remove_item(item)
                self.undo_stack.append(("delete", deleted_items, []))
                self.redo_stack.clear()
        else: super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self.start_history_record()

        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.commit_history_record()
            return

        if event.button() == Qt.MouseButton.RightButton:
            if self.mode in ["POLYLINE", "POLYGON"] and len(self.poly_points) > 0: self.finish_polyline()
            else: self.set_mode("SELECT")
            self.commit_history_record()
            return

        raw_pos = self.mapToScene(event.pos())

        if self.mode == "PRINT_AREA" and event.button() == Qt.MouseButton.LeftButton:
            self.start_point = raw_pos
            self.commit_history_record()
            return

        if self.mode == "TRIM" and event.button() == Qt.MouseButton.LeftButton:
            self.trim_start_pos = raw_pos
            self.commit_history_record()
            return

        if self.mode == "SELECT" and event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                clicked_item = self.itemAt(event.pos())
                if clicked_item and isinstance(clicked_item, QGraphicsItem) and clicked_item not in (self.paper_guide_item, self.custom_print_rect_item):
                    if not clicked_item.isSelected():
                        self.scene.clearSelection()
                        clicked_item.setSelected(True)
                    selected = self.scene.selectedItems()
                    self.scene.clearSelection()
                    for it in selected:
                        cloned = self._clone_item(it)
                        if cloned: cloned.setSelected(True)
            super().mousePressEvent(event)
            self.commit_history_record()
            return

        if self.mode in ["ROTATE", "SCALE", "MIRROR", "OFFSET", "ARRAY"]:
            super().mousePressEvent(event); self.execute_edit_command()
            self.commit_history_record()
            return
        elif self.mode == "HATCH":
            super().mousePressEvent(event); self.apply_hatching()
            self.commit_history_record()
            return
        elif self.mode == "CLOUD_OBJECT":
            super().mousePressEvent(event); self.convert_selected_to_cloud()
            self.commit_history_record()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            pos = self.get_snapped_pos(raw_pos)
            cx, cy = pos.x(), pos.y()
            disp_color = self.get_display_color(self.current_color)
            pen = QPen(disp_color, self.current_thickness, self.current_style)

            if self.mode == "BREAK":
                if self.break_first_pt is None:
                    _, _, target_s = self._find_trim_target_and_split(pos)
                    if target_s:
                        self.break_first_pt = pos
                        self.break_target_shape = target_s
                else:
                    self.break_shape_at_points(self.break_target_shape, self.break_first_pt, pos)
                    self.break_first_pt = None
                    self.break_target_shape = None
                    self.clear_trim_preview()

            elif self.mode == "POINT":
                r = max(2.0, self.current_thickness)
                self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen, QBrush(disp_color))
                self.shapes.append({"type": "point", "pos": (cx, cy), "layer": self.active_layer, "color": self.current_color})

            elif self.mode in ["CONCENTRIC_CIRCLE", "CONCENTRIC_RECT", "CONCENTRIC_POLYGON"]:
                if self.concentric_center is None:
                    self.concentric_center = pos
                    r = max(3.0, self.current_thickness)
                    self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen, QBrush(disp_color))
                else:
                    ccx, ccy = self.concentric_center.x(), self.concentric_center.y()
                    r = math.hypot(cx - ccx, cy - ccy)
                    if r > 0:
                        if self.mode == "CONCENTRIC_CIRCLE":
                            self.scene.addEllipse(ccx - r, ccy - r, 2 * r, 2 * r, pen)
                            self.shapes.append({"type": "circle", "center": (ccx, ccy), "radius": r, "layer": self.active_layer, "color": self.current_color})
                        elif self.mode == "CONCENTRIC_RECT":
                            self.scene.addRect(ccx - r, ccy - r, 2 * r, 2 * r, pen)
                            self.shapes.append({"type": "rect", "p1": (ccx - r, ccy - r), "p2": (ccx + r, ccy + r), "layer": self.active_layer, "color": self.current_color})
                        elif self.mode == "CONCENTRIC_POLYGON":
                            sides = getattr(self, 'polygon_sides', 6)
                            pts = self._calc_regular_polygon_points_by_angle(ccx, ccy, r, sides, 0.0)
                            self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in pts]), pen, QBrush(Qt.BrushStyle.NoBrush))
                            self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color})

            elif self.mode == "PRESET_RECT":
                w, h = self.preset_rect_w, self.preset_rect_h
                self.scene.addRect(cx - w / 2, cy - h / 2, w, h, pen)
                self.shapes.append({"type": "rect", "p1": (cx - w/2, cy - h/2), "p2": (cx + w/2, cy + h/2), "layer": self.active_layer, "color": self.current_color})
            elif self.mode == "PRESET_CIRCLE":
                r = self.preset_circle_r
                self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
                self.shapes.append({"type": "circle", "center": (cx, cy), "radius": r, "layer": self.active_layer, "color": self.current_color})
            elif self.mode == "PRESET_ARC":
                r = self.preset_arc_r
                path = QPainterPath(); path.arcTo(cx - r, cy - r, 2 * r, 2 * r, self.preset_arc_start, self.preset_arc_span)
                self.scene.addPath(path, pen)
            elif self.mode == "PRESET_POLYGON":
                pts = self._calc_regular_polygon_points_by_angle(cx, cy, self.preset_poly_r, self.preset_poly_sides, self.preset_poly_angle)
                self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in pts]), pen, QBrush(Qt.BrushStyle.NoBrush))
                self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color})

            elif self.mode in ["LINE", "H_LINE", "V_LINE", "RECT", "CIRCLE", "CIRCLE_2P", "ELLIPSE", "REG_POLYGON", "CLOUD", "ARROW", "DIMENSION", "LEADER"]:
                self.start_point = pos
            elif self.mode == "TEXT": self.add_text_item(pos)
            elif self.mode in ["CIRCLE_3P", "ARC_3P", "ARC"]:
                self.click_points.append(pos)
                if self.mode == "ARC" and len(self.click_points) == 3: self.create_center_arc_shape()
                elif len(self.click_points) == 3: self.create_3pt_shape()
            elif self.mode in ["POLYLINE", "POLYGON"]:
                last_pt = QPointF(self.poly_points[-1][0], self.poly_points[-1][1]) if self.poly_points else None
                pos_angled = self.apply_angle_snap(last_pt, raw_pos) if last_pt else raw_pos
                p = self.get_snapped_pos(pos_angled)
                if len(self.poly_points) >= 3 and math.hypot(p.x() - self.poly_points[0][0], p.y() - self.poly_points[0][1]) < self.snap_threshold:
                    self.mode = "POLYGON"; self.finish_polyline()
                    self.commit_history_record()
                    return
                self.poly_points.append((p.x(), p.y()))
                if len(self.poly_points) > 1:
                    p1, p2 = self.poly_points[-2], self.poly_points[-1]
                    self.poly_temp_items.append(self.scene.addLine(p1[0], p1[1], p2[0], p2[1], pen))

        self.commit_history_record()

    def mouseMoveEvent(self, event):
        if self._is_panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return

        raw_pos = self.mapToScene(event.pos())
        pos_angled = self.apply_angle_snap(self.start_point, raw_pos) if self.start_point else raw_pos
        current_pos = self.get_snapped_pos(pos_angled)

        if self.mode == "PRINT_AREA" and self.start_point:
            if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None
            x1, y1, x2, y2 = self.start_point.x(), self.start_point.y(), current_pos.x(), current_pos.y()
            pen_area = QPen(QColor(255, 102, 0), 2, Qt.PenStyle.DashLine)
            self.temp_item = self.scene.addRect(QRectF(QPointF(x1, y1), QPointF(x2, y2)), pen_area)

        if self.mode == "TRIM" and not self._is_panning:
            self.clear_trim_preview()
            if getattr(self, 'trim_start_pos', None) and event.buttons() & Qt.MouseButton.LeftButton:
                sp = self.trim_start_pos
                pen_fence = QPen(QColor(255, 0, 0), 2, Qt.PenStyle.DashDotLine)
                self.trim_preview_item = self.scene.addLine(sp.x(), sp.y(), raw_pos.x(), raw_pos.y(), pen_fence)
            else:
                trim_seg, _, _ = self._find_trim_target_and_split(raw_pos)
                if trim_seg:
                    pen_hl = QPen(QColor(255, 50, 50), max(4, self.current_thickness + 2), Qt.PenStyle.SolidLine)
                    c = list(trim_seg.coords)
                    self.trim_preview_item = self.scene.addLine(c[0][0], c[0][1], c[-1][0], c[-1][1], pen_hl)

        if self.mode == "BREAK" and self.break_first_pt:
            self.clear_trim_preview()
            pen_preview = QPen(QColor(255, 100, 0), 3, Qt.PenStyle.DotLine)
            self.trim_preview_item = self.scene.addLine(
                self.break_first_pt.x(), self.break_first_pt.y(), current_pos.x(), current_pos.y(), pen_preview
            )

        if self.mode == "SELECT": super().mouseMoveEvent(event); return

        disp_color = self.get_display_color(self.current_color)
        pen_preview = QPen(disp_color, 1, Qt.PenStyle.DashLine)
        if self.temp_item and self.mode != "PRINT_AREA":
            self.safe_remove_item(self.temp_item)
            self.temp_item = None
            
        cx, cy = current_pos.x(), current_pos.y()

        if self.mode == "POINT":
            r = max(2.0, self.current_thickness)
            self.temp_item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen_preview, QBrush(disp_color))
        elif self.mode == "PRESET_RECT":
            w, h = self.preset_rect_w, self.preset_rect_h
            self.temp_item = self.scene.addRect(cx - w / 2, cy - h / 2, w, h, pen_preview)
        elif self.mode == "PRESET_CIRCLE":
            r = self.preset_circle_r
            self.temp_item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen_preview)
        elif self.mode == "PRESET_ARC":
            r = self.preset_arc_r
            path = QPainterPath(); path.arcTo(cx - r, cy - r, 2 * r, 2 * r, self.preset_arc_start, self.preset_arc_span)
            self.temp_item = self.scene.addPath(path, pen_preview)
        elif self.mode == "PRESET_POLYGON":
            pts = self._calc_regular_polygon_points_by_angle(cx, cy, self.preset_poly_r, self.preset_poly_sides, self.preset_poly_angle)
            self.temp_item = self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in pts]), pen_preview)

        elif self.start_point and self.mode != "PRINT_AREA":
            x1, y1, x2, y2 = self.start_point.x(), self.start_point.y(), current_pos.x(), current_pos.y()
            if self.mode in ["LINE", "ARROW", "DIMENSION", "LEADER"]: self.temp_item = self.scene.addLine(x1, y1, x2, y2, pen_preview)
            elif self.mode == "H_LINE": self.temp_item = self.scene.addLine(x1, y1, x2, y1, pen_preview)
            elif self.mode == "V_LINE": self.temp_item = self.scene.addLine(x1, y1, x1, y2, pen_preview)
            elif self.mode == "RECT": self.temp_item = self.scene.addRect(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2), pen_preview)
            elif self.mode == "CIRCLE":
                r = math.hypot(x2 - x1, y2 - y1)
                self.temp_item = self.scene.addEllipse(x1 - r, y1 - r, 2 * r, 2 * r, pen_preview)
            elif self.mode == "CIRCLE_2P":
                c_x, c_y, r = (x1 + x2) / 2, (y1 + y2) / 2, math.hypot(x2 - x1, y2 - y1) / 2
                self.temp_item = self.scene.addEllipse(c_x - r, c_y - r, 2 * r, 2 * r, pen_preview)
            elif self.mode == "ELLIPSE": self.temp_item = self.scene.addEllipse(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2), pen_preview)
            elif self.mode == "REG_POLYGON":
                pts = self._calc_regular_polygon_points(x1, y1, x2, y2, getattr(self, 'polygon_sides', 6))
                self.temp_item = self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in pts]), pen_preview)

        elif self.mode in ["POLYLINE", "POLYGON"] and len(self.poly_points) > 0:
            last_pt = QPointF(self.poly_points[-1][0], self.poly_points[-1][1])
            pos_angled = self.apply_angle_snap(last_pt, raw_pos)
            c_pos = self.get_snapped_pos(pos_angled)
            self.temp_item = self.scene.addLine(last_pt.x(), last_pt.y(), c_pos.x(), c_pos.y(), pen_preview)

    def mouseReleaseEvent(self, event):
        self.start_history_record()

        if event.button() == Qt.MouseButton.MiddleButton and self._is_panning:
            self._is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.commit_history_record()
            return

        raw_pos = self.mapToScene(event.pos())

        if self.mode == "PRINT_AREA" and self.start_point:
            if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None
            pos_angled = self.apply_angle_snap(self.start_point, raw_pos)
            end_point = self.get_snapped_pos(pos_angled)
            rect = QRectF(self.start_point, end_point).normalized()
            if rect.width() > 10 and rect.height() > 10:
                self.set_custom_print_rect(rect)
            self.start_point = None
            self.set_mode("SELECT")
            self.commit_history_record()
            return

        if self.mode == "TRIM" and event.button() == Qt.MouseButton.LeftButton:
            self.clear_trim_preview()
            sp = getattr(self, 'trim_start_pos', None)
            self.trim_start_pos = None
            if not sp: 
                self.commit_history_record()
                return

            dist = math.hypot(raw_pos.x() - sp.x(), raw_pos.y() - sp.y())
            if dist > 8.0:
                fence_ls = LineString([(sp.x(), sp.y()), (raw_pos.x(), raw_pos.y())])
                shapes_to_remove, new_shapes_to_add = [], []

                for shape in list(self.shapes):
                    geom = self._shape_to_shapely(shape)
                    if geom and geom.intersects(fence_ls):
                        intersections = []
                        for other in self.shapes:
                            if other == shape: continue
                            og = self._shape_to_shapely(other)
                            if og and geom.intersects(og):
                                inter = geom.intersection(og)
                                if isinstance(inter, Point): intersections.append(inter)
                                elif isinstance(inter, MultiPoint): intersections.extend(inter.geoms)
                        
                        f_inter = geom.intersection(fence_ls)
                        if isinstance(f_inter, Point): intersections.append(f_inter)
                        elif isinstance(f_inter, MultiPoint): intersections.extend(f_inter.geoms)

                        if intersections:
                            cut_pts = MultiPoint(intersections)
                            snapped_g = snap(geom, cut_pts, 1.0)
                            split_res = split(snapped_g, cut_pts)
                            segs = list(split_res.geoms) if hasattr(split_res, 'geoms') else [split_res]
                        else: segs = [geom]

                        shapes_to_remove.append(shape)
                        disp_color = self.get_display_color(shape["color"])
                        pen = QPen(disp_color, self.current_thickness, self.current_style)

                        for seg in segs:
                            if not seg.intersects(fence_ls) and seg.distance(fence_ls) > 2.0:
                                c = list(seg.coords)
                                self.scene.addLine(c[0][0], c[0][1], c[-1][0], c[-1][1], pen)
                                new_shapes_to_add.append({"type": "line", "p1": c[0], "p2": c[-1], "layer": shape.get("layer", self.active_layer), "color": shape["color"]})

                for s in shapes_to_remove:
                    if s in self.shapes: self.shapes.remove(s)
                self.shapes.extend(new_shapes_to_add)
            else:
                trim_seg, remaining_segs, target_shape = self._find_trim_target_and_split(raw_pos)
                if target_shape:
                    if target_shape in self.shapes: self.shapes.remove(target_shape)
                    disp_color = self.get_display_color(target_shape["color"])
                    pen = QPen(disp_color, self.current_thickness, self.current_style)
                    for seg in remaining_segs:
                        c = list(seg.coords)
                        self.scene.addLine(c[0][0], c[0][1], c[-1][0], c[-1][1], pen)
                        self.shapes.append({"type": "line", "p1": c[0], "p2": c[-1], "layer": target_shape.get("layer", self.active_layer), "color": target_shape["color"]})

            self.commit_history_record()
            return

        if self.mode == "SELECT" or event.button() != Qt.MouseButton.LeftButton or not self.start_point:
            super().mouseReleaseEvent(event)
            self.commit_history_record()
            return

        pos_angled = self.apply_angle_snap(self.start_point, raw_pos)
        end_point = self.get_snapped_pos(pos_angled)
        
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)
        x1, y1, x2, y2 = self.start_point.x(), self.start_point.y(), end_point.x(), end_point.y()

        if self.temp_item:
            self.safe_remove_item(self.temp_item)
            self.temp_item = None

        if self.mode == "LINE":
            self.scene.addLine(x1, y1, x2, y2, pen)
            self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (x2, y2), "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "H_LINE":
            self.scene.addLine(x1, y1, x2, y1, pen)
            self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (x2, y1), "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "V_LINE":
            self.scene.addLine(x1, y1, x1, y2, pen)
            self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (x1, y2), "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "RECT":
            rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2)
            self.scene.addRect(rx, ry, rw, rh, pen)
            self.shapes.append({"type": "rect", "p1": (rx, ry), "p2": (rx + rw, ry + rh), "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "CIRCLE":
            r = math.hypot(x2 - x1, y2 - y1)
            self.scene.addEllipse(x1 - r, y1 - r, 2 * r, 2 * r, pen)
            self.shapes.append({"type": "circle", "center": (x1, y1), "radius": r, "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "CIRCLE_2P":
            c_x, c_y, r = (x1 + x2) / 2, (y1 + y2) / 2, math.hypot(x2 - x1, y2 - y1) / 2
            self.scene.addEllipse(c_x - r, c_y - r, 2 * r, 2 * r, pen)
            self.shapes.append({"type": "circle", "center": (c_x, c_y), "radius": r, "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "ELLIPSE":
            rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2)
            self.scene.addEllipse(rx, ry, rw, rh, pen)
            self.shapes.append({"type": "ellipse", "center": (rx + rw / 2, ry + rh / 2), "rx": rw / 2, "ry": rh / 2, "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "REG_POLYGON":
            pts = self._calc_regular_polygon_points(x1, y1, x2, y2, getattr(self, 'polygon_sides', 6))
            self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in pts]), pen, QBrush(Qt.BrushStyle.NoBrush))
            self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "ARROW": self.add_arrow(self.start_point, end_point)
        elif self.mode == "DIMENSION": self.add_dimension(self.start_point, end_point)
        elif self.mode == "LEADER": self.add_leader(self.start_point, end_point)

        self.start_point = None
        self.commit_history_record()

    # --- 高度作図サブメソッド ---
    def add_arrow(self, p1, p2):
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)
        self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
        self._draw_arrow_head(p2, math.atan2(p2.y() - p1.y(), p2.x() - p1.x()) + math.pi)
        self.shapes.append({"type": "arrow", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "layer": self.active_layer, "color": self.current_color})

    def add_dimension(self, p1, p2):
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)
        self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
        
        val_str = f"{math.hypot(p2.x() - p1.x(), p2.y() - p1.y()) * self.scale_factor:.1f} mm"
        angle = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
        self._draw_arrow_head(p1, angle); self._draw_arrow_head(p2, angle + math.pi)
        
        t_item = self.scene.addText(val_str)
        t_item.setDefaultTextColor(disp_color)
        t_item.setFont(QFont("Meiryo", max(10, self.current_thickness * 4)))
        t_item.setPos((p1.x() + p2.x()) / 2 - 20, (p1.y() + p2.y()) / 2 - 20)
        self.shapes.append({"type": "dimension", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "val_str": val_str, "layer": self.active_layer, "color": self.current_color})

    def add_leader(self, p1, p2):
        text, ok = QInputDialog.getText(self, "引き出し線注釈", "注釈文字を入力してください:")
        if not ok or not text: return
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)
        self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
        self._draw_arrow_head(p1, math.atan2(p2.y() - p1.y(), p2.x() - p1.x()))
        landing = 40 if p2.x() >= p1.x() else -40
        p3_x = p2.x() + landing
        self.scene.addLine(p2.x(), p2.y(), p3_x, p2.y(), pen)
        t_item = self.scene.addText(text)
        t_item.setDefaultTextColor(disp_color)
        font_size = max(11, self.current_thickness * 4)
        t_item.setFont(QFont("Meiryo", font_size))
        rect = t_item.boundingRect()
        t_item.setPos(p2.x() if landing > 0 else (p3_x - rect.width()), p2.y() - rect.height() + (rect.height() * 0.15))
        self.shapes.append({"type": "leader", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "text": text, "layer": self.active_layer, "color": self.current_color})

    def _draw_arrow_head(self, pos, angle):
        disp_color = self.get_display_color(self.current_color)
        size = max(10, self.current_thickness * 3.5)
        p_a1 = QPointF(pos.x() + size * math.cos(angle - math.pi / 6), pos.y() + size * math.sin(angle - math.pi / 6))
        p_a2 = QPointF(pos.x() + size * math.cos(angle + math.pi / 6), pos.y() + size * math.sin(angle + math.pi / 6))
        self.scene.addPolygon(QPolygonF([pos, p_a1, p_a2]), QPen(disp_color, 1), QBrush(disp_color))

    def add_text_item(self, pos):
        text, ok = QInputDialog.getMultiLineText(self, "文章入力", "注釈文章を入力してください (Enterで改行):")
        if ok and text.strip():
            disp_color = self.get_display_color(self.current_color)
            t_item = self.scene.addText(text)
            t_item.setDefaultTextColor(disp_color)
            font_size = max(12, self.current_thickness * 5)
            t_item.setFont(QFont("Meiryo", font_size))
            t_item.setPos(pos)
            self.shapes.append({"type": "text", "text": text, "pos": (pos.x(), pos.y()), "font_size": font_size, "layer": self.active_layer, "color": self.current_color})

    def create_3pt_shape(self):
        p1, p2, p3 = self.click_points
        x1, y1, x2, y2, x3, y3 = p1.x(), p1.y(), p2.x(), p2.y(), p3.x(), p3.y()
        d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(d) > 1e-6:
            ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)) / d
            uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)) / d
            r = math.hypot(x1 - ux, y1 - uy)
            disp_color = self.get_display_color(self.current_color)
            pen = QPen(disp_color, self.current_thickness, self.current_style)

            if self.mode == "CIRCLE_3P":
                self.scene.addEllipse(ux - r, uy - r, 2 * r, 2 * r, pen)
                self.shapes.append({"type": "circle", "center": (ux, uy), "radius": r, "layer": self.active_layer, "color": self.current_color})
            elif self.mode == "ARC_3P":
                a1, a3 = math.atan2(y1 - uy, x1 - ux), math.atan2(y3 - uy, x3 - ux)
                path = QPainterPath(); path.arcTo(ux - r, uy - r, 2 * r, 2 * r, -math.degrees(a1), -math.degrees(a3 - a1))
                self.scene.addPath(path, pen)
        self.click_points.clear()

    def create_center_arc_shape(self):
        p1, p2, p3 = self.click_points
        cx, cy = p1.x(), p1.y()
        r = math.hypot(p2.x() - cx, p2.y() - cy)
        if r > 0:
            a1, a2 = math.atan2(p2.y() - cy, p2.x() - cx), math.atan2(p3.y() - cy, p3.x() - cx)
            path = QPainterPath(); path.arcTo(cx - r, cy - r, 2 * r, 2 * r, -math.degrees(a1), -math.degrees(a2) - (-math.degrees(a1)))
            self.scene.addPath(path, QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style))
        self.click_points.clear()

    def finish_polyline(self):
        self.start_history_record()
        if self.temp_item:
            self.safe_remove_item(self.temp_item)
            self.temp_item = None
            
        if len(self.poly_points) > 1:
            is_closed = (self.mode == "POLYGON")
            pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
            if is_closed:
                p1, p2 = self.poly_points[-1], self.poly_points[0]
                self.poly_temp_items.append(self.scene.addLine(p1[0], p1[1], p2[0], p2[1], pen))
            self.shapes.append({"type": "polyline", "points": list(self.poly_points), "is_closed": is_closed, "layer": self.active_layer, "color": self.current_color})
        elif len(self.poly_points) == 1:
            for item in self.poly_temp_items: self.safe_remove_item(item)
            
        self.poly_points.clear(); self.poly_temp_items.clear()
        self.commit_history_record()

    def finish_multi_point_mode(self):
        self.concentric_center = None
        self.break_first_pt = None
        self.break_target_shape = None
        self.click_points.clear(); self.poly_points.clear()
        
        for item in self.poly_temp_items:
            self.safe_remove_item(item)
            
        self.poly_temp_items.clear(); self.update_snap_marker(None, None)
        
        if self.temp_item:
            self.safe_remove_item(self.temp_item)
            self.temp_item = None
            
        self.start_point = None

    def _calc_regular_polygon_points(self, cx, cy, vx, vy, sides):
        r, base_angle = math.hypot(vx - cx, vy - cy), math.atan2(vy - cy, vx - cx)
        return [(cx + r * math.cos(base_angle + 2 * math.pi * i / sides), cy + r * math.sin(base_angle + 2 * math.pi * i / sides)) for i in range(sides)]

    def _calc_regular_polygon_points_by_angle(self, cx, cy, radius, sides, start_angle_deg):
        base_angle = math.radians(start_angle_deg)
        return [(cx + radius * math.cos(base_angle + 2 * math.pi * i / sides), cy + radius * math.sin(base_angle + 2 * math.pi * i / sides)) for i in range(sides)]

    # --- DXF書き出し ---
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
                    doc.layers.add(name=l_name, color=ezdxf.rgb2int((c.red(), c.green(), c.blue())))

            for b_name, b_data in self.blocks.items():
                blk = doc.blocks.new(name=b_name)
                for s in b_data["shapes"]:
                    stype = s.get("type")
                    if stype == "line":
                        blk.add_line((s["p1"][0], -s["p1"][1]), (s["p2"][0], -s["p2"][1]))
                    elif stype == "rect":
                        x1, y1, x2, y2 = s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1]
                        blk.add_lwpolyline([(x1, -y1), (x2, -y1), (x2, -y2), (x1, -y2)], close=True)
                    elif stype == "circle":
                        blk.add_circle((s["center"][0], -s["center"][1]), s["radius"])

            for shape in self.shapes:
                color = shape["color"]
                layer = shape.get("layer", "0")
                attribs = {'true_color': ezdxf.rgb2int((color.red(), color.green(), color.blue())), 'layer': layer}
                stype = shape["type"]

                if stype in ["line", "arrow", "dimension"]:
                    msp.add_line((shape["p1"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p2"][1]), dxfattribs=attribs)
                elif stype == "rect":
                    x1, y1, x2, y2 = shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1]
                    msp.add_lwpolyline([(x1, -y1), (x2, -y1), (x2, -y2), (x1, -y2)], close=True, dxfattribs=attribs)
                elif stype == "circle":
                    msp.add_circle((shape["center"][0], -shape["center"][1]), shape["radius"], dxfattribs=attribs)
                elif stype == "ellipse":
                    cx, cy, rx, ry = shape["center"][0], shape["center"][1], shape["rx"], shape["ry"]
                    major, ratio = ((rx, 0), ry / rx) if rx >= ry else ((0, -ry), rx / ry)
                    msp.add_ellipse((cx, -cy), major_axis=major, ratio=ratio, dxfattribs=attribs)
                elif stype == "polyline":
                    msp.add_lwpolyline([(x, -y) for x, y in shape["points"]], close=shape["is_closed"], dxfattribs=attribs)
                elif stype == "point":
                    msp.add_point((shape["pos"][0], -shape["pos"][1]), dxfattribs=attribs)
                elif stype == "text":
                    tx, ty, text_val = shape["pos"][0], shape["pos"][1], shape["text"]
                    if "\n" in text_val:
                        t_item = msp.add_mtext(text_val, dxfattribs={'char_height': shape["font_size"], 'true_color': attribs['true_color'], 'layer': layer})
                        t_item.set_location((tx, -ty))
                    else:
                        t_item = msp.add_text(text_val, dxfattribs={'height': shape["font_size"], 'true_color': attribs['true_color'], 'layer': layer})
                        t_item.set_placement((tx, -ty))
                elif stype == "block_ref":
                    b_name = shape["block_name"]
                    bx, by = shape["pos"]
                    msp.add_blockref(b_name, insert=(bx, -by), dxfattribs={'layer': layer})

            dxf_dir = os.path.dirname(file_path)
            dxf_name = os.path.splitext(os.path.basename(file_path))[0]
            img_count = 0

            for item in self.scene.items():
                if isinstance(item, QGraphicsPixmapItem):
                    pixmap = item.pixmap()
                    if pixmap.isNull(): continue
                    img_count += 1
                    
                    img_filename = f"{dxf_name}_img{img_count}.png"
                    img_filepath = os.path.join(dxf_dir, img_filename)
                    pixmap.save(img_filepath, "PNG")

                    image_def = doc.add_image_def(filename=img_filename, size_in_pixel=(pixmap.width(), pixmap.height()))
                    transform = item.sceneTransform()
                    top_left = transform.map(QPointF(0, 0))
                    top_right = transform.map(QPointF(pixmap.width(), 0))
                    bottom_left = transform.map(QPointF(0, pixmap.height()))

                    u_x = (top_right.x() - top_left.x()) / pixmap.width()
                    u_y = -(top_right.y() - top_left.y()) / pixmap.width()
                    v_x = (bottom_left.x() - top_left.x()) / pixmap.height()
                    v_y = -(bottom_left.y() - top_left.y()) / pixmap.height()

                    msp.add_image(
                        insert=(top_left.x(), -top_left.y()),
                        size_in_pixel=(pixmap.width(), pixmap.height()),
                        image_def=image_def,
                        u_pixel=(u_x, u_y, 0),
                        v_pixel=(v_x, v_y, 0)
                    )

            doc.saveas(file_path)
            msg = f"DXFファイルを保存しました:\n{file_path}"
            if img_count > 0: msg += f"\n\n※ {img_count}枚の画像を同フォルダに保存しました。"
            QMessageBox.information(self, "成功", msg)
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"DXF保存失敗:\n{e}")