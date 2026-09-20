import math
from shapely.geometry import LineString, Point

# PyQt6の必要なクラスを漏れなくインポート
from PyQt6.QtWidgets import (
    QGraphicsView, 
    QGraphicsScene, 
    QGraphicsItemGroup, 
    QGraphicsPixmapItem, 
    QGraphicsItem, 
    QGraphicsLineItem, 
    QGraphicsTextItem, 
    QGraphicsEllipseItem, 
    QMessageBox, 
    QInputDialog, 
    QApplication,
    QDialog          # 追加
)
from PyQt6.QtGui import QPen, QColor, QBrush, QFont, QPolygonF, QPainterPath, QPageSize, QPageLayout
from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal

# --- 分割モジュールからのインポート ---
from cad_utils import _clean_for_json
from cad_items import CustomPixmapItem
from cad_snap_history import SnapAndHistoryMixin
from cad_file_ops import FileOpsMixin
from cad_edit_ops import EditOpsMixin
from cad_dialogs import TextEditDialog, TableEditDialog


class CADCanvas(QGraphicsView, SnapAndHistoryMixin, FileOpsMixin, EditOpsMixin):
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
        """標準ブロックの初期化"""
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
        if isinstance(item, list):
            for i in item:
                if i and i.scene() == self.scene:
                    self.scene.removeItem(i)
        elif item and item.scene() == self.scene:
            self.scene.removeItem(item)

    def _shape_to_shapely(self, shape):
        stype = shape.get("type")
        if stype in ["line", "dimension", "arrow", "leader"]:
            return LineString([shape["p1"], shape["p2"]])
        elif stype == "rect":
            x1, y1, x2, y2 = shape["p1"][0], shape["p1"][1], shape["p2"][0], shape["p2"][1]
            return LineString([(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)])
        elif stype in ["circle", "arc"]:
            return Point(shape["center"][0], shape["center"][1]).buffer(shape["radius"]).boundary
        elif stype in ["polyline", "spline"]:
            pts = shape.get("points", [])
            if len(pts) < 2: return None
            return LineString(pts + [pts[0]]) if shape.get("is_closed") and len(pts) >= 3 else LineString(pts)
        return None

    def _translate_shape(self, shape, dx, dy):
        stype = shape.get("type")
        if stype in ["line", "dimension", "arrow", "leader", "rect"]:
            shape["p1"] = (shape["p1"][0] + dx, shape["p1"][1] + dy)
            shape["p2"] = (shape["p2"][0] + dx, shape["p2"][1] + dy)
        elif stype in ["circle", "ellipse", "arc"]:
            shape["center"] = (shape["center"][0] + dx, shape["center"][1] + dy)
        elif stype in ["polyline", "spline"]:
            shape["points"] = [(px + dx, py + dy) for px, py in shape["points"]]
        elif stype in ["point", "text", "block_ref", "table", "image"]:
            shape["pos"] = (shape["pos"][0] + dx, shape["pos"][1] + dy)

    def bring_selected_to_front(self): [i.setZValue(max([item.zValue() for item in self.scene.items()] or [0]) + 1) for i in self.scene.selectedItems()]
    def send_selected_to_back(self): [i.setZValue(min([item.zValue() for item in self.scene.items()] or [0]) - 1) for i in self.scene.selectedItems()]

    def zoom_in(self): self.scale(self.zoom_factor, self.zoom_factor)
    def zoom_out(self): self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
    def zoom_fit(self):
        rect = self.scene.itemsBoundingRect()
        if not rect.isEmpty(): self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        else: self.resetTransform()

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

    # --- Mouse & Context Menu Events ---
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
                input_txt, font_size, text_color, font_family, is_bold, is_italic = dlg.get_result()
                if input_txt.strip():
                    disp_color = self.get_display_color(text_color)
                    t_item = self.scene.addText(input_txt.strip())
                    t_item.setDefaultTextColor(disp_color)
                    font = QFont(font_family, int(font_size))
                    font.setBold(is_bold)
                    font.setItalic(is_italic)
                    t_item.setFont(font)
                    t_item.setPos(pos)
                    self.shapes.append({
                        "type": "text", "text": input_txt.strip(), "pos": (pos.x(), pos.y()),
                        "font_size": font_size, "layer": self.active_layer, "color": text_color,
                        "thickness": self.current_thickness, "style": self.current_style,
                        "font_family": font_family, "bold": is_bold, "italic": is_italic, "item": t_item
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
            
            is_movable = (self.mode == "SELECT")
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, is_movable)

            self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

        self.start_point = None
        self.commit_history_record()