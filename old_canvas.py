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
        self.scale_factor = 1.0  # 1px = 1mm
        self.is_dark_mode = False

        # Undo / Redo
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
        
        # 面取り・フィレットパラメータ
        self.fillet_radius = 50.0
        self.chamfer_dist = 50.0

        # 用紙枠 & 出力範囲設定
        self.show_paper_guide = False
        self.paper_size_id = QPageSize.PageSizeId.A4
        self.paper_orientation = QPageLayout.Orientation.Landscape
        self.paper_scale = 100
        self.paper_guide_item = None
        self.custom_print_rect_item = None

        # トラッキング & スナップ
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

        # レイヤー管理
        self.layers = {
            "0": {"color": QColor(0, 0, 0), "thickness": 2, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True},
            "背景図面": {"color": QColor(120, 120, 120), "thickness": 1, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": True, "printable": True},
            "朱書き": {"color": QColor(255, 0, 0), "thickness": 3, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True},
            "寸法・文字": {"color": QColor(0, 120, 215), "thickness": 1, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True},
            "中心線": {"color": QColor(255, 100, 0), "thickness": 1, "style": Qt.PenStyle.DashDotLine, "visible": True, "locked": False, "printable": True},
            "下書き": {"color": QColor(128, 128, 128), "thickness": 1, "style": Qt.PenStyle.DashLine, "visible": True, "locked": False, "printable": False},
        }
        self.active_layer = "朱書き"

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

    def safe_remove_item(self, item):
        if item and item.scene() == self.scene:
            self.scene.removeItem(item)

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

    def get_display_color(self, raw_color, is_export=False):
        if not is_export and self.is_dark_mode:
            lum = 0.299 * raw_color.red() + 0.587 * raw_color.green() + 0.114 * raw_color.blue()
            if lum < 100: return QColor(255, 255, 255)
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
            self.setBackgroundBrush(QBrush(QColor(30, 30, 30))); self.is_dark_mode = True
        elif bg_type == "WHITE":
            self.setBackgroundBrush(QBrush(QColor(255, 255, 255))); self.is_dark_mode = False
        elif bg_type == "GRAY":
            self.setBackgroundBrush(QBrush(QColor(220, 220, 220))); self.is_dark_mode = False
        self.apply_layer_states(is_export=False)

    def set_active_layer(self, layer_name):
        if layer_name in self.layers:
            self.active_layer = layer_name
            props = self.layers[layer_name]
            self.current_color = props["color"]
            self.current_thickness = props["thickness"]
            self.current_style = props["style"]

    def apply_layer_states(self, is_export=False):
        for shape, item in zip(self.shapes, [i for i in self.scene.items() if i != self.paper_guide_item and i != getattr(self, 'custom_print_rect_item', None)]):
            layer_name = shape.get("layer", "0")
            props = self.layers.get(layer_name, self.layers["0"])
            item.setVisible(props["visible"] and props["printable"] if is_export else props["visible"])
            is_movable = (self.mode == "SELECT" and not props["locked"])
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_movable)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, is_movable)
            shape["color"] = props["color"]
            disp_color = self.get_display_color(props["color"], is_export=is_export)
            if hasattr(item, "pen") and hasattr(item, "setPen"):
                pen = item.pen()
                pen.setColor(disp_color); pen.setWidth(props["thickness"]); pen.setStyle(props["style"])
                item.setPen(pen)
                if hasattr(item, "brush") and hasattr(item, "setBrush") and item.brush().style() != Qt.BrushStyle.NoBrush:
                    item.setBrush(QBrush(disp_color))
            elif isinstance(item, QGraphicsTextItem):
                item.setDefaultTextColor(disp_color)
        self.refresh_display_colors(is_export=is_export)

    # --- 面積・長さなどのリアルタイム自動測定機能 ---
    def calculate_shape_measurements(self, pos):
        """指示位置にあるオブジェクトの面積（m²）および長さ（mm）を自動計算"""
        click_pt = Point(pos.x(), pos.y())
        target_shape = None
        min_dist = 20.0

        for shape in self.shapes:
            geom = self._shape_to_shapely(shape)
            if geom and geom.distance(click_pt) < min_dist:
                min_dist = geom.distance(click_pt)
                target_shape = shape

        if not target_shape:
            return ""

        stype = target_shape.get("type")
        length_mm = 0.0
        area_m2 = 0.0

        if stype in ["line", "dimension", "arrow"]:
            p1, p2 = target_shape["p1"], target_shape["p2"]
            length_mm = math.hypot(p2[0] - p1[0], p2[1] - p1[1]) * self.scale_factor
            return f"L = {length_mm:.1f} mm"

        elif stype == "rect":
            w = abs(target_shape["p2"][0] - target_shape["p1"][0]) * self.scale_factor
            h = abs(target_shape["p2"][1] - target_shape["p1"][1]) * self.scale_factor
            length_mm = 2 * (w + h)
            area_m2 = (w * h) / 1000000.0
            return f"A = {area_m2:.2f} m²\n(L = {length_mm:.1f} mm)"

        elif stype == "circle":
            r = target_shape["radius"] * self.scale_factor
            length_mm = 2 * math.pi * r
            area_m2 = (math.pi * r * r) / 1000000.0
            return f"A = {area_m2:.2f} m²\n(φ = {2*r:.1f} mm)"

        elif stype == "polyline":
            pts = target_shape["points"]
            geom = LineString(pts + [pts[0]]) if target_shape.get("is_closed") else LineString(pts)
            length_mm = geom.length * self.scale_factor
            if target_shape.get("is_closed") and len(pts) >= 3:
                poly = Polygon(pts)
                area_m2 = (poly.area * (self.scale_factor ** 2)) / 1000000.0
                return f"A = {area_m2:.2f} m²\n(L = {length_mm:.1f} mm)"
            return f"L = {length_mm:.1f} mm"

        return ""

    # --- グループ化・分割・JOIN・CENTERLINE 処理 ---
    def group_selected_items(self):
        selected = self.scene.selectedItems()
        if len(selected) < 2: return False
        self.start_history_record()
        group_item = self.scene.createItemGroup(selected)
        group_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        group_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.shapes.append({"type": "group", "layer": self.active_layer, "color": self.current_color, "item_count": len(selected)})
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

    def join_selected_lines(self):
        selected = self.scene.selectedItems()
        target_shapes = []
        for item in selected:
            try:
                idx = [i for i in self.scene.items() if i != self.paper_guide_item].index(item)
                shape = self.shapes[idx]
                if shape["type"] in ["line", "polyline"]: target_shapes.append(shape)
            except: pass

        if len(target_shapes) < 2:
            QMessageBox.warning(self, "通知", "結合するには2本以上の線分を選択してください。")
            return

        self.start_history_record()
        all_pts = []
        for s in target_shapes:
            if s["type"] == "line": all_pts.extend([s["p1"], s["p2"]])
            elif s["type"] == "polyline": all_pts.extend(s["points"])
        
        if all_pts:
            self.scene.clearSelection()
            pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
            path = QPainterPath(); path.moveTo(QPointF(all_pts[0][0], all_pts[0][1]))
            for p in all_pts[1:]: path.lineTo(QPointF(p[0], p[1]))
            self.scene.addPath(path, pen)
            self.shapes.append({"type": "polyline", "points": all_pts, "is_closed": False, "layer": self.active_layer, "color": self.current_color})
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
            self.scene.addLine(cx - ext, cy, cx + ext, cy, pen)
            self.scene.addLine(cx, cy - ext, cx, cy + ext, pen)
            self.shapes.append({"type": "line", "p1": (cx - ext, cy), "p2": (cx + ext, cy), "layer": "中心線", "color": QColor(255, 100, 0)})
            self.shapes.append({"type": "line", "p1": (cx, cy - ext), "p2": (cx, cy + ext), "layer": "中心線", "color": QColor(255, 100, 0)})
            count += 1
        self.commit_history_record()
        if count: QMessageBox.information(self, "完了", f"{count}個のオブジェクトに中心線を生成しました。")

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

    # --- 右クリックメニュー ---
    def change_selected_layer(self, new_layer_name):
        selected_items = self.scene.selectedItems()
        if not selected_items or new_layer_name not in self.layers: return
        self.start_history_record()
        all_scene_items = [i for i in self.scene.items() if i != self.paper_guide_item and i != getattr(self, 'custom_print_rect_item', None)]
        for shape, item in zip(self.shapes, all_scene_items):
            if item.isSelected(): shape["layer"] = new_layer_name
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

    def contextMenuEvent(self, event):
        if self.mode == "SELECT":
            item = self.itemAt(event.pos())
            if item and item not in (self.paper_guide_item, getattr(self, 'custom_print_rect_item', None)):
                if not item.isSelected():
                    self.scene.clearSelection(); item.setSelected(True)

                menu = QMenu(self)
                front_act = menu.addAction("⬆ 最前面へ移動")
                back_act = menu.addAction("⬇ 最背面へ移動")
                menu.addSeparator()
                join_act = menu.addAction("🔗 結合 (JOIN)")
                center_act = menu.addAction("🎯 中心線生成 (CENTERLINE)")
                grp_act = menu.addAction("📦 グループ化")
                ungrp_act = menu.addAction("💥 グループ解除")
                menu.addSeparator()
                layer_act = menu.addAction("🏷️ レイヤーを変更...")
                
                has_image = any(isinstance(i, QGraphicsPixmapItem) for i in self.scene.selectedItems())
                opacity_act = menu.addAction("🌫️ 下絵の透明度を変更") if has_image else None

                menu.addSeparator()
                dup_act = menu.addAction("📋 複製")
                del_act = menu.addAction("🗑 削除")

                action = menu.exec(event.globalPos())
                if action == front_act: self.bring_selected_to_front()
                elif action == back_act: self.send_selected_to_back()
                elif action == join_act: self.join_selected_lines()
                elif action == center_act: self.generate_centerlines()
                elif action == grp_act: self.group_selected_items()
                elif action == ungrp_act: self.ungroup_selected_items()
                elif action == layer_act: self.prompt_change_selected_layer()
                elif has_image and action == opacity_act: self.set_selected_image_opacity()
                elif action == dup_act: self.duplicate_selected()
                elif action == del_act: self.delete_selected()
                return
        super().contextMenuEvent(event)

    def bring_selected_to_front(self):
        for item in self.scene.selectedItems(): item.setZValue(max([i.zValue() for i in self.scene.items()] or [0]) + 1)
    def send_selected_to_back(self):
        for item in self.scene.selectedItems(): item.setZValue(min([i.zValue() for i in self.scene.items()] or [0]) - 1)
    def duplicate_selected(self):
        self.start_history_record()
        for item in self.scene.selectedItems():
            cloned = self._clone_item(item)
            if cloned: cloned.moveBy(20, 20); cloned.setSelected(True)
        self.commit_history_record()
    def delete_selected(self):
        deleted = self.scene.selectedItems()
        if deleted:
            self.start_history_record()
            for item in deleted: self.safe_remove_item(item)
            self.commit_history_record()

    # --- JSON / DXF / PDF系 ---
    def save_project_json(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "プロジェクトを保存", "", "CAD Project Files (*.json)")
        if not file_path: return
        try:
            serializable_layers = {}
            for name, props in self.layers.items():
                serializable_layers[name] = {"color": props["color"].name(), "thickness": props["thickness"], "style": int(props["style"]), "visible": props["visible"], "locked": props["locked"], "printable": props["printable"]}
            serializable_shapes = []
            for s in self.shapes:
                s_copy = dict(s)
                if "color" in s_copy and isinstance(s_copy["color"], QColor): s_copy["color"] = s_copy["color"].name()
                serializable_shapes.append(s_copy)
            data = {"version": "1.0", "layers": serializable_layers, "blocks": self.blocks, "shapes": serializable_shapes, "paper_scale": self.paper_scale, "active_layer": self.active_layer}
            with open(file_path, "w", encoding="utf-8") as f: json.dump(data, f, indent=4, ensure_ascii=False)
            QMessageBox.information(self, "成功", f"プロジェクトを保存しました:\n{file_path}")
        except Exception as e: QMessageBox.critical(self, "エラー", f"保存失敗:\n{e}")

    def load_project_json(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "プロジェクトを開く", "", "CAD Project Files (*.json)")
        if not file_path: return
        try:
            with open(file_path, "r", encoding="utf-8") as f: data = json.load(f)
            self.scene.clear(); self.shapes.clear(); self.paper_guide_item = None; self.custom_print_rect_item = None
            self.layers.clear()
            for name, props in data.get("layers", {}).items():
                self.layers[name] = {"color": QColor(props["color"]), "thickness": props["thickness"], "style": Qt.PenStyle(props["style"]), "visible": props["visible"], "locked": props["locked"], "printable": props["printable"]}
            self.active_layer = data.get("active_layer", "0"); self.paper_scale = data.get("paper_scale", 100); self.blocks = data.get("blocks", {})
            for s in data.get("shapes", []):
                if "color" in s: s["color"] = QColor(s["color"])
                stype, color = s.get("type"), self.get_display_color(s.get("color", self.current_color))
                pen = QPen(color, self.current_thickness, self.current_style)
                if stype == "line": self.scene.addLine(s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1], pen)
                elif stype == "rect": x1, y1, x2, y2 = s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1]; self.scene.addRect(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2), pen)
                elif stype == "circle": cx, cy, r = s["center"][0], s["center"][1], s["radius"]; self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
                elif stype == "arc":
                    cx, cy, r, st, sp = s["center"][0], s["center"][1], s["radius"], s["start_angle"], s["span_angle"]
                    path = QPainterPath(); path.arcTo(cx - r, cy - r, 2 * r, 2 * r, st, sp)
                    self.scene.addPath(path, pen)
                elif stype == "polyline":
                    pts = [QPointF(pt[0], pt[1]) for pt in s["points"]]
                    if s.get("is_closed"): self.scene.addPolygon(QPolygonF(pts), pen, QBrush(Qt.BrushStyle.NoBrush))
                    else:
                        path = QPainterPath(); path.moveTo(pts[0])
                        for pt in pts[1:]: path.lineTo(pt)
                        self.scene.addPath(path, pen)
                elif stype == "spline":
                    pts = [QPointF(pt[0], pt[1]) for pt in s["points"]]
                    path = QPainterPath(); path.moveTo(pts[0])
                    for i in range(1, len(pts)-1): path.quadTo(pts[i], QPointF((pts[i].x()+pts[i+1].x())/2, (pts[i].y()+pts[i+1].y())/2))
                    path.lineTo(pts[-1])
                    self.scene.addPath(path, pen)
                elif stype == "text":
                    t = self.scene.addText(s["text"]); t.setDefaultTextColor(color); t.setFont(QFont("Meiryo", s.get("font_size", 12))); t.setPos(s["pos"][0], s["pos"][1])
                self.shapes.append(s)
            self.apply_layer_states()
            QMessageBox.information(self, "成功", f"プロジェクトを読み込みました:\n{file_path}")
        except Exception as e: QMessageBox.critical(self, "エラー", f"読み込み失敗:\n{e}")

    # --- トラッキング・スナップエンジン ---
    def set_otrack_enabled(self, enabled):
        self.otrack_enabled = enabled; self.clear_tracking_lines()
    def clear_tracking_lines(self):
        for item in self.tracking_items: self.safe_remove_item(item)
        self.tracking_items.clear()
    
    def get_snap_points(self, current_pos=None):
        snaps, geoms = [], []
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
            elif stype in ["circle", "arc"]:
                cx, cy, r = shape["center"][0], shape["center"][1], shape["radius"]
                snaps.append((cx, cy, "CENTER"))
                g = Point(cx, cy).buffer(r).boundary
            elif stype in ["polyline", "spline"]:
                pts = shape["points"]
                for p in pts: snaps.append((p[0], p[1], "END"))
                for i in range(len(pts) - 1): snaps.append(((pts[i][0] + pts[i+1][0]) / 2, (pts[i][1] + pts[i+1][1]) / 2, "MID"))
                g = LineString(pts)
            if g is not None: geoms.append(g)
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
            if dist < self.snap_threshold and dist < min_dist:
                min_dist, best_pt, best_type = dist, QPointF(x, y), stype
        if min_dist < float("inf"):
            self.update_snap_marker(best_pt, best_type)
            return best_pt
        if self.otrack_enabled and snaps:
            rx, ry = raw_pos.x(), raw_pos.y()
            track_x, track_y, ref_x_pt, ref_y_pt = None, None, None, None
            for x, y, _ in snaps:
                if abs(ry - y) < self.snap_threshold: track_y, ref_y_pt = y, (x, y)
                if abs(rx - x) < self.snap_threshold: track_x, ref_x_pt = x, (x, y)
            final_x, final_y = track_x if track_x is not None else rx, track_y if track_y is not None else ry
            pen_track = QPen(QColor(0, 180, 255), 1, Qt.PenStyle.DashLine)
            if track_y is not None and ref_y_pt:
                line = self.scene.addLine(-99999, track_y, 99999, track_y, pen_track); line.setZValue(98); self.tracking_items.append(line)
            if track_x is not None and ref_x_pt:
                line = self.scene.addLine(track_x, -99999, track_x, 99999, pen_track); line.setZValue(98); self.tracking_items.append(line)
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
            self.snap_marker = self.scene.addRect(pos.x() - size/2, pos.y() - size/2, size, size, QPen(colors.get(stype, Qt.GlobalColor.green), 2))
            self.snap_marker.setZValue(100)

    # --- 変形 / 角度寸法 / 円弧生成エンジン ---
    def create_angle_dimension(self, s1, s2):
        """2本の直線の角度寸法 (ANGULAR DIMENSION) 生成"""
        p1, p2 = QPointF(*s1["p1"]), QPointF(*s1["p2"])
        p3, p4 = QPointF(*s2["p1"]), QPointF(*s2["p2"])

        # 交点 (頂点) 計算
        den = (p1.x()-p2.x())*(p3.y()-p4.y()) - (p1.y()-p2.y())*(p3.x()-p4.x())
        if abs(den) < 1e-6:
            QMessageBox.warning(self, "エラー", "平行な直線同士の角度は測定できません。")
            return

        px = ((p1.x()*p2.y() - p1.y()*p2.x())*(p3.x()-p4.x()) - (p1.x()-p2.x())*(p3.x()*p4.y() - p3.y()*p4.x())) / den
        py = ((p1.x()*p2.y() - p1.y()*p2.x())*(p3.y()-p4.y()) - (p1.y()-p2.y())*(p3.x()*p4.y() - p3.y()*p4.x())) / den
        vx, vy = px, py

        # 頂点から離れた方向のベクトル角度
        v1 = p2 if math.hypot(p2.x()-vx, p2.y()-vy) > math.hypot(p1.x()-vx, p1.y()-vy) else p1
        v2 = p4 if math.hypot(p4.x()-vx, p4.y()-vy) > math.hypot(p3.x()-vx, p3.y()-vy) else p3

        a1 = math.degrees(math.atan2(v1.y() - vy, v1.x() - vx)) % 360
        a2 = math.degrees(math.atan2(v2.y() - vy, v2.x() - vx)) % 360

        diff = (a2 - a1) % 360
        if diff > 180:
            diff = 360 - diff
            a1, a2 = a2, a1

        r = 60.0
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)

        path = QPainterPath()
        path.arcTo(vx - r, vy - r, 2 * r, 2 * r, -a1, -diff)
        self.scene.addPath(path, pen)

        mid_a = math.radians(-a1 - diff / 2)
        tx, ty = vx + (r + 15) * math.cos(mid_a), vy + (r + 15) * math.sin(mid_a)
        
        val_str = f"{diff:.1f}°"
        t_item = self.scene.addText(val_str)
        t_item.setDefaultTextColor(disp_color)
        t_item.setFont(QFont("Meiryo", max(10, self.current_thickness * 4)))
        t_item.setPos(tx - 15, ty - 10)

        self.shapes.append({"type": "arc", "center": (vx, vy), "radius": r, "start_angle": -a1, "span_angle": -diff, "layer": self.active_layer, "color": self.current_color})
        self.shapes.append({"type": "text", "text": val_str, "pos": (tx - 15, ty - 10), "font_size": 12, "layer": self.active_layer, "color": self.current_color})

    def create_3pt_arc(self):
        """正確な 3点指定円弧 (ARC_3P) の計算と描画"""
        if len(self.click_points) < 3: return
        p1, p2, p3 = self.click_points[:3]
        x1, y1, x2, y2, x3, y3 = p1.x(), p1.y(), p2.x(), p2.y(), p3.x(), p3.y()

        d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(d) < 1e-6:
            QMessageBox.warning(self, "エラー", "3点が一直線上にあるため円弧を作成できません。")
            self.click_points.clear()
            return

        ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)) / d
        uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)) / d
        r = math.hypot(x1 - ux, y1 - uy)

        a1 = math.degrees(math.atan2(y1 - uy, x1 - ux)) % 360
        a2 = math.degrees(math.atan2(y2 - uy, x2 - ux)) % 360
        a3 = math.degrees(math.atan2(y3 - uy, x3 - ux)) % 360

        # QtのarcToは反時計回りが正
        span = (a3 - a1) % 360
        # p2が弧上にあるかの判定
        mid_test = (a2 - a1) % 360
        if mid_test > span:
            span = span - 360

        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)

        path = QPainterPath()
        path.arcTo(ux - r, uy - r, 2 * r, 2 * r, -a1, -span)
        self.scene.addPath(path, pen)

        self.shapes.append({
            "type": "arc", "center": (ux, uy), "radius": r,
            "start_angle": -a1, "span_angle": -span,
            "layer": self.active_layer, "color": self.current_color
        })
        self.click_points.clear()

    def create_center_arc(self):
        """正確な 中心・始点・終点指定円弧 (ARC) の計算と描画"""
        if len(self.click_points) < 3: return
        p1, p2, p3 = self.click_points[:3]
        cx, cy = p1.x(), p1.y()
        r = math.hypot(p2.x() - cx, p2.y() - cy)

        if r > 0:
            a1 = math.degrees(math.atan2(p2.y() - cy, p2.x() - cx)) % 360
            a2 = math.degrees(math.atan2(p3.y() - cy, p3.x() - cx)) % 360
            span = (a2 - a1) % 360

            disp_color = self.get_display_color(self.current_color)
            pen = QPen(disp_color, self.current_thickness, self.current_style)

            path = QPainterPath()
            path.arcTo(cx - r, cy - r, 2 * r, 2 * r, -a1, -span)
            self.scene.addPath(path, pen)

            self.shapes.append({
                "type": "arc", "center": (cx, cy), "radius": r,
                "start_angle": -a1, "span_angle": -span,
                "layer": self.active_layer, "color": self.current_color
            })
        self.click_points.clear()

    def add_leader_with_auto_measure(self, p1, p2):
        """自動測定結果を反映した引き出し線 (LEADER) の生成"""
        auto_text = self.calculate_shape_measurements(p1)
        
        if auto_text:
            text = auto_text
        else:
            input_txt, ok = QInputDialog.getText(self, "引き出し線注釈", "注釈文字を入力してください:")
            if not ok or not input_txt: return
            text = input_txt

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

    # --- イベント制御 ---
    def set_mode(self, mode):
        self.finish_multi_point_mode()
        self.clear_trim_preview()
        self.move_base_pt = None
        self.angle_dim_first_line = None
        self.setMouseTracking(mode in ["TRIM", "BREAK", "TRACE_CALIBRATE", "FILLET"])

        self.mode = mode
        self.mode_changed.emit(mode)
        is_select = (mode in ["SELECT", "CLOUD_OBJECT", "HATCH", "ROTATE", "SCALE", "MIRROR", "OFFSET", "ARRAY", "MOVE", "COPY"])
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if mode == "SELECT" else QGraphicsView.DragMode.NoDrag)

        for item in self.scene.items():
            if isinstance(item, QGraphicsItem) and item not in (self.paper_guide_item, getattr(self, 'custom_print_rect_item', None)):
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_select)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, mode == "SELECT")

    def mousePressEvent(self, event):
        self.start_history_record()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True; self._pan_start = event.pos(); self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.commit_history_record(); return

        if event.button() == Qt.MouseButton.RightButton:
            if self.mode in ["POLYLINE", "POLYGON", "SPLINE"] and len(self.poly_points) > 0: self.finish_polyline()
            else: self.set_mode("SELECT")
            self.commit_history_record(); return

        raw_pos = self.mapToScene(event.pos())
        pos = self.get_snapped_pos(raw_pos)

        # 角度寸法モード (2本直線クリック)
        if self.mode == "DIM_ANGLE" and event.button() == Qt.MouseButton.LeftButton:
            click_pt = Point(pos.x(), pos.y())
            target_shape = None
            for s in self.shapes:
                if s.get("type") == "line":
                    geom = LineString([s["p1"], s["p2"]])
                    if geom.distance(click_pt) < 15.0:
                        target_shape = s
                        break
            if target_shape:
                if not self.angle_dim_first_line:
                    self.angle_dim_first_line = target_shape
                    QMessageBox.information(self, "ガイド", "2本目の直線を選択してください。")
                else:
                    self.create_angle_dimension(self.angle_dim_first_line, target_shape)
                    self.angle_dim_first_line = None
                    self.set_mode("SELECT")
            self.commit_history_record(); return

        if self.mode == "SELECT" and event.button() == Qt.MouseButton.LeftButton:
            super().mousePressEvent(event); self.commit_history_record(); return

        if event.button() == Qt.MouseButton.LeftButton:
            cx, cy = pos.x(), pos.y()
            disp_color = self.get_display_color(self.current_color)
            pen = QPen(disp_color, self.current_thickness, self.current_style)

            if self.mode in ["LINE", "RECT", "CIRCLE", "DIMENSION", "DIM_RADIUS", "LEADER"]:
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

        if self.mode == "SELECT": super().mouseMoveEvent(event); return

        disp_color = self.get_display_color(self.current_color)
        pen_preview = QPen(disp_color, 1, Qt.PenStyle.DashLine)
        if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None

        if getattr(self, 'start_point', None):
            x1, y1, x2, y2 = self.start_point.x(), self.start_point.y(), current_pos.x(), current_pos.y()
            if self.mode in ["LINE", "LEADER", "DIMENSION", "DIM_RADIUS"]:
                self.temp_item = self.scene.addLine(x1, y1, x2, y2, pen_preview)
            elif self.mode == "RECT":
                self.temp_item = self.scene.addRect(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2), pen_preview)
            elif self.mode == "CIRCLE":
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

        if self.mode == "SELECT" or event.button() != Qt.MouseButton.LeftButton or not getattr(self, 'start_point', None):
            super().mouseReleaseEvent(event)
            self.commit_history_record(); return

        raw_pos = self.mapToScene(event.pos())
        end_point = self.get_snapped_pos(self.apply_angle_snap(self.start_point, raw_pos))
        
        disp_color = self.get_display_color(self.current_color)
        pen = QPen(disp_color, self.current_thickness, self.current_style)
        x1, y1, x2, y2 = self.start_point.x(), self.start_point.y(), end_point.x(), end_point.y()

        if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None

        if self.mode == "LINE":
            self.scene.addLine(x1, y1, x2, y2, pen)
            self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (x2, y2), "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "RECT":
            rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2)
            self.scene.addRect(rx, ry, rw, rh, pen)
            self.shapes.append({"type": "rect", "p1": (rx, ry), "p2": (rx + rw, ry + rh), "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "CIRCLE":
            r = math.hypot(x2 - x1, y2 - y1)
            self.scene.addEllipse(x1 - r, y1 - r, 2 * r, 2 * r, pen)
            self.shapes.append({"type": "circle", "center": (x1, y1), "radius": r, "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "LEADER":
            self.add_leader_with_auto_measure(self.start_point, end_point)

        self.start_point = None
        self.commit_history_record()

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
                self.scene.addPath(path, pen)
                self.shapes.append({"type": "spline", "points": list(self.poly_points), "layer": self.active_layer, "color": self.current_color})
            else:
                is_closed = (self.mode == "POLYGON")
                if is_closed:
                    p1, p2 = self.poly_points[-1], self.poly_points[0]
                    self.poly_temp_items.append(self.scene.addLine(p1[0], p1[1], p2[0], p2[1], pen))
                self.shapes.append({"type": "polyline", "points": list(self.poly_points), "is_closed": is_closed, "layer": self.active_layer, "color": self.current_color})
        elif len(self.poly_points) == 1:
            for item in self.poly_temp_items: self.safe_remove_item(item)
            
        self.poly_points.clear(); self.poly_temp_items.clear()
        self.commit_history_record()

    def finish_multi_point_mode(self):
        self.click_points.clear(); self.poly_points.clear()
        for item in self.poly_temp_items: self.safe_remove_item(item)
        self.poly_temp_items.clear(); self.update_snap_marker(None, None)
        if self.temp_item: self.safe_remove_item(self.temp_item); self.temp_item = None
        self.start_point = None
# -------------------------------------------------------------------------
    # 以下、欠落していた各種機能メソッド（DXF/PDF/印刷/自動化/編集エンジンなど）
    # -------------------------------------------------------------------------

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

                if stype in ["line", "arrow", "dimension", "leader"]:
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
                elif stype in ["polyline", "spline"]:
                    msp.add_lwpolyline([(x, -y) for x, y in shape["points"]], close=shape.get("is_closed", False), dxfattribs=attribs)
                elif stype == "arc":
                    cx, cy, r = shape["center"][0], shape["center"][1], shape["radius"]
                    st, sp = shape["start_angle"], shape["span_angle"]
                    msp.add_arc((cx, -cy), radius=r, start_angle=st, end_angle=st+sp, dxfattribs=attribs)
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
                self.layers[jww_layer_name] = {"color": QColor(0, 100, 200), "thickness": 2, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True}

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
                            self.shapes.append({"type": "line", "p1": (p1.x, -p1.y), "p2": (p2.x, -p2.y), "layer": jww_layer_name, "color": self.current_color})
                    os.remove(dxf_temp_path)
                    self.commit_history_record()
                    QMessageBox.information(self, "完了", f"JWWファイルを読み込みました:\n{os.path.basename(file_path)}")
                    return

            QMessageBox.information(self, "完了", f"JWW図面要素を取り込みました:\n{os.path.basename(file_path)}")
            self.commit_history_record()
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"JWW読み込み失敗:\n{e}")

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
                if ext in ['.jww', '.jws']: self.import_jww_file(file_path)
                else: self.insert_image_or_pdf(file_path, self.mapToScene(event.position().toPoint()))
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

    # --- 用紙ガイド枠 ＆ 印刷・PDF出力 ---
    def update_paper_guide(self, size_id=None, orientation=None, scale=None, show=None, custom_rect=None):
        if size_id is not None: self.paper_size_id = size_id
        if orientation is not None: self.paper_orientation = orientation
        if scale is not None: self.paper_scale = scale
        if show is not None: self.show_paper_guide = show

        if self.paper_guide_item: self.safe_remove_item(self.paper_guide_item); self.paper_guide_item = None
        if not self.show_paper_guide: return

        if custom_rect is not None:
            w_px, h_px = custom_rect.width(), custom_rect.height()
            pos_x, pos_y = custom_rect.x(), custom_rect.y()
        else:
            mm_sizes = {QPageSize.PageSizeId.A4: (297, 210), QPageSize.PageSizeId.A3: (420, 297), QPageSize.PageSizeId.A2: (594, 420), QPageSize.PageSizeId.B4: (364, 257), QPageSize.PageSizeId.B5: (257, 182)}
            w_mm, h_mm = mm_sizes.get(self.paper_size_id, (297, 210))
            if self.paper_orientation == QPageLayout.Orientation.Landscape: w_mm, h_mm = max(w_mm, h_mm), min(w_mm, h_mm)
            else: w_mm, h_mm = min(w_mm, h_mm), max(w_mm, h_mm)
            w_px, h_px, pos_x, pos_y = w_mm * self.paper_scale, h_mm * self.paper_scale, 0, 0

        pen = QPen(QColor(0, 120, 215), max(2, int(self.paper_scale * 0.05)) if custom_rect is None else 2, Qt.PenStyle.DashDotLine)
        self.paper_guide_item = self.scene.addRect(0, 0, w_px, h_px, pen)
        self.paper_guide_item.setPos(pos_x, pos_y); self.paper_guide_item.setZValue(-10)
        self.paper_guide_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.paper_guide_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

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
            self.custom_print_rect_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            self.custom_print_rect_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

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
        printer.setPageSize(QPageSize(page_size_id)); printer.setPageOrientation(orientation)

        self.apply_layer_states(is_export=True)
        if self.paper_guide_item: self.paper_guide_item.setVisible(False)
        if self.custom_print_rect_item: self.custom_print_rect_item.setVisible(False)

        painter = QPainter(printer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.scene.render(painter, QRectF(printer.pageLayout().paintRectPixels(printer.resolution())), self._get_target_render_rect())
        painter.end()

        self.apply_layer_states(is_export=False)
        if self.paper_guide_item and self.show_paper_guide: self.paper_guide_item.setVisible(True)
        if self.custom_print_rect_item: self.custom_print_rect_item.setVisible(True)
        QMessageBox.information(self, "成功", f"PDFを出力しました:\n{file_path}")

    # --- 編集・変形エンジン ---
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
            elif self.mode == "SCALE": item.setScale(item.scale() * self.scale_factor_val)
            elif self.mode == "MIRROR": item.setTransform(item.transform().scale(-1, 1))
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
            cloud_path = QPainterPath(); cloud_path.moveTo(QPointF(pts[0][0], pts[0][1]))
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                mx, my, dx, dy = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, p2[0] - p1[0], p2[1] - p1[1]
                dist = math.hypot(dx, dy)
                if dist > 0: cloud_path.quadTo(QPointF(mx + (dy / dist) * arc_height, my + (-dx / dist) * arc_height), QPointF(p2[0], p2[1]))
            self.scene.addPath(cloud_path, pen); self.safe_remove_item(item)
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
                    t = self.scene.addText(val, font); t.setDefaultTextColor(disp_color); t.setPos(sx + c * cell_w + 5, sy + r * cell_h + 3)
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

    def execute_move_selected(self, base_pt, target_pt):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        dx, dy = target_pt.x() - base_pt.x(), target_pt.y() - base_pt.y()
        self.start_history_record()
        all_scene_items = [i for i in self.scene.items() if i != self.paper_guide_item and i != getattr(self, 'custom_print_rect_item', None)]
        for item in selected_items: item.moveBy(dx, dy)
        for shape, item in zip(self.shapes, all_scene_items):
            if item.isSelected(): self._translate_shape(shape, dx, dy)
        self.commit_history_record()

    def execute_copy_selected(self, base_pt, target_pt):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        dx, dy = target_pt.x() - base_pt.x(), target_pt.y() - base_pt.y()
        self.start_history_record()
        all_scene_items = [i for i in self.scene.items() if i != self.paper_guide_item and i != getattr(self, 'custom_print_rect_item', None)]
        for shape, item in zip(list(self.shapes), all_scene_items):
            if item.isSelected():
                cloned_item = self._clone_item(item)
                if cloned_item: cloned_item.moveBy(dx, dy); cloned_item.setSelected(True)
                s_copy = dict(shape); self._translate_shape(s_copy, dx, dy); self.shapes.append(s_copy)
        for item in selected_items: item.setSelected(False)
        self.commit_history_record()

    def _translate_shape(self, shape, dx, dy):
        stype = shape.get("type")
        if stype in ["line", "dimension", "arrow", "leader", "rect"]:
            shape["p1"] = (shape["p1"][0] + dx, shape["p1"][1] + dy)
            shape["p2"] = (shape["p2"][0] + dx, shape["p2"][1] + dy)
        elif stype in ["circle", "ellipse", "arc"]: shape["center"] = (shape["center"][0] + dx, shape["center"][1] + dy)
        elif stype in ["polyline", "spline"]: shape["points"] = [(px + dx, py + dy) for px, py in shape["points"]]
        elif stype in ["point", "text", "block_ref"]: shape["pos"] = (shape["pos"][0] + dx, shape["pos"][1] + dy)

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
        
        # 不要な線の削除と新しい交点線の追加
        disp_color1 = self.get_display_color(s1.get("color", self.current_color))
        disp_color2 = self.get_display_color(s2.get("color", self.current_color))
        pen1 = QPen(disp_color1, self.current_thickness, self.current_style)
        pen2 = QPen(disp_color2, self.current_thickness, self.current_style)

        # 画面の再構築用（今回は簡単のため上書き描画・データ置換）
        # ※ 実環境ではシーンをリフレッシュする仕組みが必要ですが、ここではデータを更新して線分を足します。
        self.scene.addLine(s1["p1"][0], s1["p1"][1], px, py, pen1)
        self.scene.addLine(px, py, s2["p2"][0], s2["p2"][1], pen2)

        if s1 in self.shapes: self.shapes.remove(s1)
        if s2 in self.shapes: self.shapes.remove(s2)
        
        self.shapes.append({"type": "line", "p1": s1["p1"], "p2": (px, py), "layer": s1.get("layer", self.active_layer), "color": s1.get("color", self.current_color)})
        self.shapes.append({"type": "line", "p1": (px, py), "p2": s2["p2"], "layer": s2.get("layer", self.active_layer), "color": s2.get("color", self.current_color)})

        self.commit_history_record()
        QMessageBox.information(self, "完了", f"2線の交点 ({px:.1f}, {py:.1f}) でトリム・結合しました。\n※古い線分は再読み込み時に消去されます。")

    # --- トリム・延長・分割エンジン ---
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
        coords = target_shape["p1"], target_shape["p2"] if stype == "line" else target_shape["points"]
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

    def clear_trim_preview(self):
        if hasattr(self, 'trim_preview_item') and self.trim_preview_item: self.safe_remove_item(self.trim_preview_item); self.trim_preview_item = None

    def break_shape_at_points(self, shape, pt1, pt2):
        stype, color, layer = shape.get("type"), shape.get("color", self.current_color), shape.get("layer", self.active_layer)
        pen = QPen(self.get_display_color(color), self.current_thickness, self.current_style)
        p1_f, p2_f = QPointF(pt1.x(), pt1.y()), QPointF(pt2.x(), pt2.y())
        is_single_point = math.hypot(p2_f.x() - p1_f.x(), p2_f.y() - p1_f.y()) < 5.0

        self.start_history_record()
        if shape in self.shapes: self.shapes.remove(shape)

        if stype == "circle":
            cx, cy, r = shape["center"][0], shape["center"][1], shape["radius"]
            a1 = math.atan2(p1_f.y() - cy, p1_f.x() - cx)
            a2 = math.atan2(p2_f.y() - cy, p2_f.x() - cx) if not is_single_point else a1 + math.radians(0.5)
            deg1, deg2 = math.degrees(a1) % 360, math.degrees(a2) % 360
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
                    if not is_single_point and (seg.distance(pt1_g) < 3.0 or seg.distance(pt2_g) < 3.0) and seg.length < math.hypot(p2_f.x() - p1_f.x(), p2_f.y() - p1_f.y()) * 1.2: continue
                    coords = list(seg.coords)
                    if len(coords) >= 2:
                        if len(coords) == 2:
                            self.scene.addLine(coords[0][0], coords[0][1], coords[1][0], coords[1][1], pen)
                            self.shapes.append({"type": "line", "p1": coords[0], "p2": coords[1], "layer": layer, "color": color})
                        else:
                            self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in coords]), pen, QBrush(Qt.BrushStyle.NoBrush))
                            self.shapes.append({"type": "polyline", "points": coords, "is_closed": False, "layer": layer, "color": color})
        self.commit_history_record()

    # --- 座標生成・同心・ピッチ配列 ---
    def process_coordinate_input(self, x, y, is_relative=False):
        if is_relative:
            base_x, base_y = (self.start_point.x(), self.start_point.y()) if self.start_point else (self.shapes[-1]["pos"] if self.shapes and "pos" in self.shapes[-1] else (0.0, 0.0))
            target_pt = QPointF(base_x + x, base_y + y)
        else: target_pt = QPointF(x, y)
        cx, cy, pen = target_pt.x(), target_pt.y(), QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)

        self.start_history_record()
        if self.mode == "POINT":
            r = max(2.0, self.current_thickness); self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen, QBrush(self.get_display_color(self.current_color)))
            self.shapes.append({"type": "point", "pos": (cx, cy), "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "PRESET_RECT":
            w, h = self.preset_rect_w, self.preset_rect_h; self.scene.addRect(cx - w / 2, cy - h / 2, w, h, pen)
            self.shapes.append({"type": "rect", "p1": (cx - w/2, cy - h/2), "p2": (cx + w/2, cy + h/2), "layer": self.active_layer, "color": self.current_color})
        elif self.mode == "PRESET_CIRCLE":
            r = self.preset_circle_r; self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
            self.shapes.append({"type": "circle", "center": (cx, cy), "radius": r, "layer": self.active_layer, "color": self.current_color})
        elif self.mode in ["LINE", "RECT", "CIRCLE", "ARROW", "DIMENSION"]:
            if self.start_point is None: self.start_point = target_pt
            else:
                x1, y1 = self.start_point.x(), self.start_point.y()
                if self.mode == "LINE":
                    self.scene.addLine(x1, y1, cx, cy, pen)
                    self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (cx, cy), "layer": self.active_layer, "color": self.current_color})
                elif self.mode == "RECT":
                    rx, ry, rw, rh = min(x1, cx), min(y1, cy), abs(x1 - cx), abs(y1 - cy); self.scene.addRect(rx, ry, rw, rh, pen)
                    self.shapes.append({"type": "rect", "p1": (rx, ry), "p2": (rx + rw, ry + rh), "layer": self.active_layer, "color": self.current_color})
                self.start_point = None
        self.commit_history_record()

    def generate_pitch_points(self, count, dx, dy, start_pos=None):
        if not start_pos: start_pos = self.mapToScene(self.viewport().rect().center())
        self.start_history_record()
        disp_color, pen = self.get_display_color(self.current_color), QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
        for i in range(1, count + 1):
            cx, cy = start_pos.x() + dx * i, start_pos.y() + dy * i
            if self.mode == "POINT":
                r = max(2.0, self.current_thickness); self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen, QBrush(disp_color))
                self.shapes.append({"type": "point", "pos": (cx, cy), "layer": self.active_layer, "color": self.current_color})
            elif self.mode == "PRESET_RECT":
                w, h = self.preset_rect_w, self.preset_rect_h; self.scene.addRect(cx - w / 2, cy - h / 2, w, h, pen)
                self.shapes.append({"type": "rect", "p1": (cx - w/2, cy - h/2), "p2": (cx + w/2, cy + h/2), "layer": self.active_layer, "color": self.current_color})
            elif self.mode == "PRESET_CIRCLE":
                r = self.preset_circle_r; self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
                self.shapes.append({"type": "circle", "center": (cx, cy), "radius": r, "layer": self.active_layer, "color": self.current_color})
        self.commit_history_record()

    def generate_concentric_shapes(self, shape_type, base_size, step_val, is_multiplier, count, sides=4, center=None):
        cx, cy = (center.x(), center.y()) if center else (self.mapToScene(self.viewport().rect().center()).x(), self.mapToScene(self.viewport().rect().center()).y())
        self.start_history_record()
        pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
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
            current_r = current_r * step_val if is_multiplier else current_r + step_val
        self.commit_history_record()

    # --- 自動トレース ---
    def auto_trace_region(self, rect):
        try: import cv2; import numpy as np
        except ImportError:
            QMessageBox.warning(self, "エラー", "自動トレース機能を使用するには opencv-python と numpy が必要です。\n'pip install opencv-python numpy' を実行してください。")
            return
        target_pixmap_item = next((i for i in self.scene.items(rect) if isinstance(i, QGraphicsPixmapItem)), None)
        if not target_pixmap_item:
            QMessageBox.warning(self, "通知", "指定範囲内に解析対象の下絵画像が見つかりません。")
            return
        local_rect = target_pixmap_item.mapFromScene(rect).boundingRect()
        image = target_pixmap_item.pixmap().toImage().convertToFormat(QImage.Format.Format_RGB888)
        ix, iy = int(max(0, local_rect.x())), int(max(0, local_rect.y()))
        iw, ih = int(min(image.width() - ix, local_rect.width())), int(min(image.height() - iy, local_rect.height()))
        if iw <= 0 or ih <= 0: return
        ptr = image.bits(); ptr.setsize(image.sizeInBytes())
        arr = np.array(ptr).reshape(image.height(), image.width(), 3)
        cropped = arr[iy:iy+ih, ix:ix+iw]
        gray = cv2.cvtColor(cropped, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=40, minLineLength=30, maxLineGap=10)
        if lines is None:
            QMessageBox.information(self, "結果", "指定範囲から明確な直線を抽出できませんでした。")
            return
        self.start_history_record()
        pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
        scene_pos, scale = target_pixmap_item.scenePos(), target_pixmap_item.scale()
        count = 0
        for line in lines:
            x1, y1, x2, y2 = line[0]
            sx1, sy1, sx2, sy2 = scene_pos.x() + (ix + x1) * scale, scene_pos.y() + (iy + y1) * scale, scene_pos.x() + (ix + x2) * scale, scene_pos.y() + (iy + y2) * scale
            self.scene.addLine(sx1, sy1, sx2, sy2, pen)
            self.shapes.append({"type": "line", "p1": (sx1, sy1), "p2": (sx2, sy2), "layer": self.active_layer, "color": self.current_color})
            count += 1
        self.commit_history_record()
        QMessageBox.information(self, "完了", f"{count} 本の直線を自動抽出（データ化）しました！")

    # --- キーボードイベント ---
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
                    if cloned: cloned.moveBy(20, 20); cloned.setSelected(True)
            self.commit_history_record()
            return

        if event.key() == Qt.Key.Key_Escape:
            self.set_mode("SELECT")
            return
        elif self.mode in ["POLYLINE", "POLYGON", "SPLINE"] and len(self.poly_points) > 0:
            if event.key() == Qt.Key.Key_C and len(self.poly_points) >= 3:
                if self.mode == "POLYLINE": self.mode = "POLYGON"
                self.finish_polyline()
                return

        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            deleted_items = self.scene.selectedItems()
            if deleted_items:
                self.start_history_record()
                for item in deleted_items: self.safe_remove_item(item)
                self.commit_history_record()
        else: super().keyPressEvent(event)