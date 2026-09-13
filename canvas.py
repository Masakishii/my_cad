import os
import math
import json
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
        self.copied_items = []
        self._before_items = set()
        self._before_shapes_len = 0

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

    def set_mode(self, mode):
        self.finish_multi_point_mode()
        self.clear_trim_preview()
        self.move_base_pt = None
        self.angle_dim_first_line = None
        self.setMouseTracking(mode in ["TRIM", "BREAK", "TRACE_CALIBRATE", "FILLET"])

        if mode in ["ROTATE", "SCALE", "MIRROR", "OFFSET", "ARRAY"]:
            self.mode = mode
            self.execute_edit_command()
            self.set_mode("SELECT")
            return
        elif mode == "HATCH":
            self.apply_hatching()
            self.set_mode("SELECT")
            return
        elif mode == "CLOUD_OBJECT":
            self.convert_selected_to_cloud()
            self.set_mode("SELECT")
            return

        if mode == "TRACE_CALIBRATE":
            selected = self.scene.selectedItems()
            pixmaps = [i for i in selected if isinstance(i, QGraphicsPixmapItem)] or [i for i in self.scene.items() if isinstance(i, QGraphicsPixmapItem)]
            if not pixmaps:
                QMessageBox.warning(self, "通知", "対象となる下絵（画像やPDF）がありません。")
                mode = "SELECT"
            else:
                self.calibrate_target_item = pixmaps[0]

        self.mode = mode
        self.mode_changed.emit(mode)
        is_select = (mode in ["SELECT", "CLOUD_OBJECT", "HATCH", "ROTATE", "SCALE", "MIRROR", "OFFSET", "ARRAY", "MOVE", "COPY"])
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if mode == "SELECT" else QGraphicsView.DragMode.NoDrag)

        for item in self.scene.items():
            if item not in (self.paper_guide_item, getattr(self, 'custom_print_rect_item', None)):
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_select)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, mode == "SELECT")

    def clear_trim_preview(self):
        if getattr(self, 'trim_preview_item', None) and self.trim_preview_item.scene() == self.scene:
            self.scene.removeItem(self.trim_preview_item)
            self.trim_preview_item = None

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0: self.zoom_in()
        else: self.zoom_out()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace): self.delete_selected()
        else: super().keyPressEvent(event)

    def set_angle_snap(self, enabled): self.angle_snap_enabled = enabled

    def apply_angle_snap(self, p1, p2):
        if not self.angle_snap_enabled or not p1: return p2
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-4: return p2
        angle_rad = math.atan2(dy, dx)
        snapped_deg = round(math.degrees(angle_rad) / 15.0) * 15.0
        return QPointF(p1.x() + dist * math.cos(math.radians(snapped_deg)), p1.y() + dist * math.sin(math.radians(snapped_deg)))

    def set_grid_snap(self, enabled, size=None):
        self.grid_snap_enabled = enabled
        if size: self.grid_size = float(size)

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
                            self.shapes.append({"type": "rect", "p1": (rect.x()+dx, rect.y()+dy), "p2": (rect.x()+dx+rect.width(), rect.y()+dy+rect.height()), "layer": self.active_layer, "color": self.current_color, "item": new_item})
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
                            h_item = self.scene.addLine(c[0][0], c[0][1], c[1][0], c[1][1], pen)
                            self.shapes.append({"type": "line", "p1": c[0], "p2": c[-1], "layer": self.active_layer, "color": self.hatch_color, "item": h_item})
                        elif inter.geom_type == 'MultiLineString':
                            for l in inter.geoms:
                                c = list(l.coords)
                                h_item = self.scene.addLine(c[0][0], c[0][1], c[1][0], c[1][1], pen)
                                self.shapes.append({"type": "line", "p1": c[0], "p2": c[-1], "layer": self.active_layer, "color": self.hatch_color, "item": h_item})
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
            c_item = self.scene.addPath(cloud_path, pen)
            self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color, "item": c_item})
            self.safe_remove_item(item)
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

        path = QPainterPath(); path.arcTo(vx - r, vy - r, 2 * r, 2 * r, -a1, -diff)
        item = self.scene.addPath(path, pen)
        mid_a = math.radians(-a1 - diff / 2)
        tx, ty = vx + (r + 15) * math.cos(mid_a), vy + (r + 15) * math.sin(mid_a)
        val_str = f"{diff:.1f}°"
        
        t_item = self.scene.addText(val_str); t_item.setDefaultTextColor(disp_color)
        t_item.setFont(QFont("Meiryo", max(10, self.current_thickness * 4))); t_item.setPos(tx - 15, ty - 10)

        self.shapes.append({"type": "arc", "center": (vx, vy), "radius": r, "start_angle": -a1, "span_angle": -diff, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
        self.shapes.append({"type": "text", "text": val_str, "pos": (tx - 15, ty - 10), "font_size": 12, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": t_item})

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
        path = QPainterPath(); path.arcTo(ux - r, uy - r, 2 * r, 2 * r, -a1, -span)
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
            path = QPainterPath(); path.arcTo(cx - r, cy - r, 2 * r, 2 * r, -a1, -span)
            item = self.scene.addPath(path, pen)
            self.shapes.append({"type": "arc", "center": (cx, cy), "radius": r, "start_angle": -a1, "span_angle": -span, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
        self.click_points.clear()

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
        self._draw_arrow_head(p1, math.atan2(p2.y() - p1.y(), p2.x() - p1.x()))
        landing = 40 if p2.x() >= p1.x() else -40
        p3_x = p2.x() + landing
        self.scene.addLine(p2.x(), p2.y(), p3_x, p2.y(), pen)
        t_item = self.scene.addText(text)
        t_item.setDefaultTextColor(disp_color)
        t_item.setFont(QFont("Meiryo", max(11, self.current_thickness * 4)))
        rect = t_item.boundingRect()
        t_item.setPos(p2.x() if landing > 0 else (p3_x - rect.width()), p2.y() - rect.height() + (rect.height() * 0.15))
        self.shapes.append({"type": "leader", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "text": text, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": l_item})

    def _draw_arrow_head(self, pos, angle):
        disp_color = self.get_display_color(self.current_color)
        size = max(10, self.current_thickness * 3.5)
        p_a1 = QPointF(pos.x() + size * math.cos(angle - math.pi / 6), pos.y() + size * math.sin(angle - math.pi / 6))
        p_a2 = QPointF(pos.x() + size * math.cos(angle + math.pi / 6), pos.y() + size * math.sin(angle + math.pi / 6))
        self.scene.addPolygon(QPolygonF([pos, p_a1, p_a2]), QPen(disp_color, 1), QBrush(disp_color))

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

    def mousePressEvent(self, event):
        self.start_history_record()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True; self._pan_start = event.pos(); self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.commit_history_record(); return

        if event.button() == Qt.MouseButton.RightButton:
            if self.mode in ["POLYLINE", "POLYGON", "SPLINE"] and len(self.poly_points) > 0:
                self.finish_polyline()
                self.commit_history_record()
                return
            elif self.mode != "SELECT":
                self.set_mode("SELECT")
                self.commit_history_record()
                return
            else:
                super().mousePressEvent(event)
                return

        raw_pos = self.mapToScene(event.pos())
        pos = self.get_snapped_pos(raw_pos)

        if self.mode == "POINT" and event.button() == Qt.MouseButton.LeftButton:
            disp_color = self.get_display_color(self.current_color)
            pen = QPen(disp_color, self.current_thickness, self.current_style)
            r = max(2.0, self.current_thickness)
            item = self.scene.addEllipse(pos.x() - r, pos.y() - r, 2 * r, 2 * r, pen, QBrush(disp_color))
            self.shapes.append({"type": "point", "pos": (pos.x(), pos.y()), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
            self.commit_history_record(); return

        elif self.mode == "TEXT" and event.button() == Qt.MouseButton.LeftButton:
            input_txt, ok = QInputDialog.getText(self, "文章入力", "挿入するテキスト:")
            if ok and input_txt.strip():
                disp_color = self.get_display_color(self.current_color)
                t_item = self.scene.addText(input_txt.strip())
                t_item.setDefaultTextColor(disp_color)
                t_item.setFont(QFont("Meiryo", max(11, self.current_thickness * 4)))
                t_item.setPos(pos)
                self.shapes.append({"type": "text", "text": input_txt.strip(), "pos": (pos.x(), pos.y()), "font_size": 12, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": t_item})
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
                path = QPainterPath(); path.arcTo(cx - r, cy - r, 2 * r, 2 * r, st, sp)
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

            if self.mode in ["LINE", "H_LINE", "V_LINE", "RECT", "CIRCLE", "CIRCLE_2P", "CIRCLE_3P", "ELLIPSE", "DIMENSION", "DIM_RADIUS", "LEADER", "CLOUD", "ARROW"]:
                self.start_point = pos

            elif self.mode in ["ARC_3P", "ARC"]:
                self.click_points.append(pos)
                if len(self.click_points) == 3:
                    if self.mode == "ARC_3P": self.create_3pt_arc()
                    elif self.mode == "ARC": self.create_center_arc()

            elif self.mode in ["POLYLINE", "POLYGON", "SPLINE", "REG_POLYGON"]:
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

        if self.mode == "TRIM":
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
        if self.temp_item and self.mode not in ["PRINT_AREA", "AUTO_TRACE"]: self.safe_remove_item(self.temp_item); self.temp_item = None

        if getattr(self, 'start_point', None) and self.mode not in ["PRINT_AREA", "AUTO_TRACE"]:
            x1, y1, x2, y2 = self.start_point.x(), self.start_point.y(), current_pos.x(), current_pos.y()
            if self.mode in ["LINE", "LEADER", "DIMENSION", "DIM_RADIUS", "CLOUD", "ARROW"]:
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

        elif self.mode in ["POLYLINE", "POLYGON", "SPLINE", "REG_POLYGON"] and len(self.poly_points) > 0:
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

        if self.mode == "SELECT" or event.button() != Qt.MouseButton.LeftButton or not getattr(self, 'start_point', None):
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

        elif self.mode == "ARROW":
            item = self.scene.addLine(x1, y1, x2, y2, pen)
            self._draw_arrow_head(QPointF(x2, y2), math.atan2(y2 - y1, x2 - x1))
            self.shapes.append({"type": "arrow", "p1": (x1, y1), "p2": (x2, y2), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

        elif self.mode == "DIMENSION":
            dist = math.hypot(x2 - x1, y2 - y1) * self.scale_factor
            val_str = f"{dist:.1f}"
            item = self.scene.addLine(x1, y1, x2, y2, pen)
            t_item = self.scene.addText(val_str)
            t_item.setDefaultTextColor(disp_color)
            t_item.setFont(QFont("Meiryo", max(10, self.current_thickness * 4)))
            t_item.setPos((x1 + x2) / 2, (y1 + y2) / 2 - 15)
            self.shapes.append({"type": "dimension", "p1": (x1, y1), "p2": (x2, y2), "val_str": val_str, "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})

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

    # --- 基本・ヘルパー処理 ---
    def safe_remove_item(self, item):
        if isinstance(item, list):
            for i in item:
                if i and i.scene() == self.scene: self.scene.removeItem(i)
        elif item and item.scene() == self.scene:
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

    def set_canvas_bg_color(self, bg_type):
        if bg_type == "DARK":
            self.setBackgroundBrush(QBrush(QColor(30, 30, 30))); self.is_dark_mode = True
        elif bg_type == "WHITE":
            self.setBackgroundBrush(QBrush(QColor(255, 255, 255))); self.is_dark_mode = False
        elif bg_type == "GRAY":
            self.setBackgroundBrush(QBrush(QColor(220, 220, 220))); self.is_dark_mode = False
        self.apply_layer_states(is_export=False)

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

    # --- アクション操作 ---
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

    # --- 数値指定・連続配置・表データ挿入 ---
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
        if not start_pos: start_pos = self.mapToScene(self.viewport().rect().center())
        self.start_history_record()
        disp_color, pen = self.get_display_color(self.current_color), QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
        for i in range(1, count + 1):
            cx, cy = start_pos.x() + dx * i, start_pos.y() + dy * i
            if self.mode == "POINT":
                r = max(2.0, self.current_thickness)
                item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen, QBrush(disp_color))
                self.shapes.append({"type": "point", "pos": (cx, cy), "layer": self.active_layer, "color": self.current_color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
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

    def add_table_data(self, grid_data, cell_w, cell_h, pos):
        rows, cols = len(grid_data), max(len(r) for r in grid_data) if grid_data else 0
        if rows == 0 or cols == 0: return
        self.start_history_record()
        disp_color, pen = self.get_display_color(self.current_color), QPen(self.get_display_color(self.current_color), self.current_thickness, Qt.PenStyle.SolidLine)
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

    # --- 入出力 & 印刷機能 ---
    def save_project_json(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "プロジェクトを保存", "", "CAD Project Files (*.json)")
        if not file_path: return
        try:
            serializable_layers = {}
            for name, props in self.layers.items():
                serializable_layers[name] = {
                    "color": props["color"].name(), "thickness": props["thickness"],
                    "style": int(props["style"]), "visible": props["visible"],
                    "locked": props["locked"], "printable": props["printable"]
                }
            serializable_shapes = []
            for s in self.shapes:
                s_copy = dict(s)
                s_copy.pop("item", None)
                if "color" in s_copy and isinstance(s_copy["color"], QColor):
                    s_copy["color"] = s_copy["color"].name()
                serializable_shapes.append(s_copy)
            data = {
                "version": "1.0", "layers": serializable_layers, "blocks": self.blocks,
                "shapes": serializable_shapes, "paper_scale": self.paper_scale, "active_layer": self.active_layer
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
            self.scene.clear(); self.shapes.clear()
            self.paper_guide_item, self.custom_print_rect_item = None, None
            self.layers.clear()
            for name, props in data.get("layers", {}).items():
                self.layers[name] = {
                    "color": QColor(props["color"]), "thickness": props["thickness"],
                    "style": Qt.PenStyle(props["style"]), "visible": props["visible"],
                    "locked": props["locked"], "printable": props["printable"]
                }
            self.active_layer = data.get("active_layer", "0")
            self.paper_scale = data.get("paper_scale", 100)
            self.blocks = data.get("blocks", {})
            for s in data.get("shapes", []):
                if "color" in s: s["color"] = QColor(s["color"])
                stype = s.get("type")
                color = self.get_display_color(s.get("color", self.current_color))
                thickness = s.get("thickness", self.current_thickness)
                style = Qt.PenStyle(s.get("style", self.current_style))
                pen = QPen(color, thickness, style)
                item = None
                if stype == "line": item = self.scene.addLine(s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1], pen)
                elif stype == "rect": x1, y1, x2, y2 = s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1]; item = self.scene.addRect(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2), pen)
                elif stype == "circle": cx, cy, r = s["center"][0], s["center"][1], s["radius"]; item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
                elif stype == "polyline":
                    pts = [QPointF(pt[0], pt[1]) for pt in s["points"]]
                    if s.get("is_closed"): item = self.scene.addPolygon(pts, pen)
                    else:
                        path = QPainterPath(); path.moveTo(pts[0])
                        for pt in pts[1:]: path.lineTo(pt)
                        item = self.scene.addPath(path, pen)
                elif stype == "text":
                    item = self.scene.addText(s["text"]); item.setDefaultTextColor(color); item.setFont(QFont("Meiryo", s.get("font_size", 12))); item.setPos(s["pos"][0], s["pos"][1])
                s["item"] = item
                self.shapes.append(s)
            self.apply_layer_states()
            QMessageBox.information(self, "成功", f"プロジェクトを読み込みました:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"読み込み失敗:\n{e}")

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
            painter = QPainter(printer)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.scene.render(painter, QRectF(printer.pageLayout().paintRectPixels(printer.resolution())), self._get_target_render_rect())
            painter.end()
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
        painter = QPainter(printer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.scene.render(painter, QRectF(printer.pageLayout().paintRectPixels(printer.resolution())), self._get_target_render_rect())
        painter.end()
        self.apply_layer_states(is_export=False)
        QMessageBox.information(self, "成功", f"PDFを出力しました:\n{file_path}")

    def zoom_in(self): self.scale(self.zoom_factor, self.zoom_factor)
    def zoom_out(self): self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
    def zoom_fit(self):
        rect = self.scene.itemsBoundingRect()
        if not rect.isEmpty(): self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        else: self.resetTransform()