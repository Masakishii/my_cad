import math
from shapely.geometry import LineString, Point, Polygon, MultiPoint, GeometryCollection
from shapely.ops import split, snap

from PyQt6.QtWidgets import QMessageBox, QInputDialog, QGraphicsPixmapItem
from PyQt6.QtGui import QPen, QColor, QPainterPath, QFont
from PyQt6.QtCore import Qt, QPointF

class GeometryMixin:
    """幾何演算・スナップ・トリム・高度編集機能 Mixin"""

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
            return f"L = {math.hypot(p2[0] - p1[0], p2[1] - p1[1]) * self.scale_factor:.1f} mm"
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
        self.scene.addPath(path, pen)
        mid_a = math.radians(-a1 - diff / 2)
        tx, ty = vx + (r + 15) * math.cos(mid_a), vy + (r + 15) * math.sin(mid_a)
        val_str = f"{diff:.1f}°"
        t_item = self.scene.addText(val_str); t_item.setDefaultTextColor(disp_color)
        t_item.setFont(QFont("Meiryo", max(10, self.current_thickness * 4))); t_item.setPos(tx - 15, ty - 10)

        self.shapes.append({"type": "arc", "center": (vx, vy), "radius": r, "start_angle": -a1, "span_angle": -diff, "layer": self.active_layer, "color": self.current_color})
        self.shapes.append({"type": "text", "text": val_str, "pos": (tx - 15, ty - 10), "font_size": 12, "layer": self.active_layer, "color": self.current_color})

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
        self.scene.addPath(path, pen)
        self.shapes.append({"type": "arc", "center": (ux, uy), "radius": r, "start_angle": -a1, "span_angle": -span, "layer": self.active_layer, "color": self.current_color})
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
            self.scene.addPath(path, pen)
            self.shapes.append({"type": "arc", "center": (cx, cy), "radius": r, "start_angle": -a1, "span_angle": -span, "layer": self.active_layer, "color": self.current_color})
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
        self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
        self._draw_arrow_head(p1, math.atan2(p2.y() - p1.y(), p2.x() - p1.x()))
        landing = 40 if p2.x() >= p1.x() else -40
        p3_x = p2.x() + landing
        self.scene.addLine(p2.x(), p2.y(), p3_x, p2.y(), pen)
        t_item = self.scene.addText(text)
        t_item.setDefaultTextColor(disp_color)
        t_item.setFont(QFont("Meiryo", max(11, self.current_thickness * 4)))
        rect = t_item.boundingRect()
        t_item.setPos(p2.x() if landing > 0 else (p3_x - rect.width()), p2.y() - rect.height() + (rect.height() * 0.15))
        self.shapes.append({"type": "leader", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "text": text, "layer": self.active_layer, "color": self.current_color})

    def _draw_arrow_head(self, pos, angle):
        disp_color = self.get_display_color(self.current_color)
        size = max(10, self.current_thickness * 3.5)
        p_a1 = QPointF(pos.x() + size * math.cos(angle - math.pi / 6), pos.y() + size * math.sin(angle - math.pi / 6))
        p_a2 = QPointF(pos.x() + size * math.cos(angle + math.pi / 6), pos.y() + size * math.sin(angle + math.pi / 6))
        self.scene.addPolygon([pos, p_a1, p_a2], QPen(disp_color, 1), QBrush(disp_color))

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

    def clear_trim_preview(self):
        if hasattr(self, 'trim_preview_item') and self.trim_preview_item: self.safe_remove_item(self.trim_preview_item); self.trim_preview_item = None

    def _translate_shape(self, shape, dx, dy):
        stype = shape.get("type")
        if stype in ["line", "dimension", "arrow", "leader", "rect"]:
            shape["p1"] = (shape["p1"][0] + dx, shape["p1"][1] + dy)
            shape["p2"] = (shape["p2"][0] + dx, shape["p2"][1] + dy)
        elif stype in ["circle", "ellipse", "arc"]: shape["center"] = (shape["center"][0] + dx, shape["center"][1] + dy)
        elif stype in ["polyline", "spline"]: shape["points"] = [(px + dx, py + dy) for px, py in shape["points"]]
        elif stype in ["point", "text", "block_ref"]: shape["pos"] = (shape["pos"][0] + dx, shape["pos"][1] + dy)

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
        elif self.mode in ["LINE", "RECT", "CIRCLE"]:
            if self.start_point is None: self.start_point = target_pt
            else:
                x1, y1 = self.start_point.x(), self.start_point.y()
                if self.mode == "LINE":
                    self.scene.addLine(x1, y1, cx, cy, pen)
                    self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (cx, cy), "layer": self.active_layer, "color": self.current_color})
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
            current_r = current_r * step_val if is_multiplier else current_r + step_val
        self.commit_history_record()

    def auto_trace_region(self, rect):
        try: import cv2; import numpy as np
        except ImportError: return
        target_pixmap_item = next((i for i in self.scene.items(rect) if isinstance(i, QGraphicsPixmapItem)), None)
        if not target_pixmap_item: return
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
        if lines is None: return
        self.start_history_record()
        pen = QPen(self.get_display_color(self.current_color), self.current_thickness, self.current_style)
        scene_pos, scale = target_pixmap_item.scenePos(), target_pixmap_item.scale()
        for line in lines:
            x1, y1, x2, y2 = line[0]
            sx1, sy1, sx2, sy2 = scene_pos.x() + (ix + x1) * scale, scene_pos.y() + (iy + y1) * scale, scene_pos.x() + (ix + x2) * scale, scene_pos.y() + (iy + y2) * scale
            self.scene.addLine(sx1, sy1, sx2, sy2, pen)
            self.shapes.append({"type": "line", "p1": (sx1, sy1), "p2": (sx2, sy2), "layer": self.active_layer, "color": self.current_color})
        self.commit_history_record()

    def _calc_regular_polygon_points_by_angle(self, cx, cy, radius, sides, start_angle_deg):
        base_angle = math.radians(start_angle_deg)
        return [(cx + radius * math.cos(base_angle + 2 * math.pi * i / sides), cy + radius * math.sin(base_angle + 2 * math.pi * i / sides)) for i in range(sides)]
4. canvas/base.py の完全版コード
Python
import math
from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QInputDialog, QMessageBox, 
                             QGraphicsItem, QGraphicsItemGroup, QGraphicsPixmapItem, 
                             QApplication, QMenu)
from PyQt6.QtGui import QPen, QColor, QPolygonF, QBrush, QFont, QPainterPath
from PyQt6.QtCore import Qt, QPointF

from canvas.geometry import GeometryMixin
from canvas.io_manager import IOMixin

class CADCanvasBase(QGraphicsView):
    """CADCanvas の基底クラス（UIイベント・表示・履歴・オブジェクト管理）"""

    def __init__(self):
        super().__init__()
        # このクラスは CADCanvas で多重継承されて使用されます
        pass

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
                pen = item.pen(); pen.setColor(disp_color); item.setPen(pen)
            elif hasattr(item, "setDefaultTextColor"):
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
                pen = item.pen(); pen.setColor(disp_color); pen.setWidth(props["thickness"]); pen.setStyle(props["style"]); item.setPen(pen)
            elif hasattr(item, "setDefaultTextColor"):
                item.setDefaultTextColor(disp_color)
        self.refresh_display_colors(is_export=is_export)

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

    def set_color(self, color):
        self.current_color = color; self.apply_property_to_selected(color=color)
    def set_thickness(self, thickness):
        self.current_thickness = thickness; self.apply_property_to_selected(thickness=thickness)
    def set_style(self, style):
        self.current_style = style; self.apply_property_to_selected(style=style)

    def apply_property_to_selected(self, color=None, thickness=None, style=None):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        self.start_history_record()
        for item in selected_items:
            if hasattr(item, "pen") and hasattr(item, "setPen"):
                pen = item.pen()
                if color is not None: pen.setColor(self.get_display_color(color))
                if thickness is not None: pen.setWidth(thickness)
                if style is not None: pen.setStyle(style)
                item.setPen(pen)
            elif hasattr(item, "setDefaultTextColor") and color is not None:
                item.setDefaultTextColor(self.get_display_color(color))
        self.commit_history_record()

    def set_angle_snap(self, enabled): self.angle_snap_enabled = enabled
    def set_grid_snap(self, enabled, size=None):
        self.grid_snap_enabled = enabled
        if size: self.grid_size = float(size)

    def apply_angle_snap(self, p1, p2):
        if not self.angle_snap_enabled or not p1: return p2
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-4: return p2
        angle_rad = math.atan2(dy, dx)
        snapped_deg = round(math.degrees(angle_rad) / 15.0) * 15.0
        return QPointF(p1.x() + dist * math.cos(math.radians(snapped_deg)), p1.y() + dist * math.sin(math.radians(snapped_deg)))


class CADCanvas(CADCanvasBase, GeometryMixin, IOMixin):
    """統合 CADCanvas クラス"""

    def __init__(self):
        QGraphicsView.__init__(self)
        CADCanvasBase.__init__(self)
        
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.scene.setSceneRect(0, 0, 1200, 800)
        self.setAcceptDrops(True)
        
        # 属性・フラグ初期化
        self.current_color = QColor(0, 0, 0)
        self.current_thickness = 2
        self.current_style = Qt.PenStyle.SolidLine
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

    def zoom_in(self): self.scale(self.zoom_factor, self.zoom_factor)
    def zoom_out(self): self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
    def zoom_fit(self):
        rect = self.scene.itemsBoundingRect()
        if not rect.isEmpty(): self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        else: self.resetTransform()