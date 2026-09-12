import math
from shapely.geometry import LineString, Point, Polygon, MultiPoint, GeometryCollection
from shapely.ops import split, snap

from PyQt6.QtWidgets import QMessageBox, QInputDialog, QGraphicsEllipseItem, QGraphicsPixmapItem
from PyQt6.QtGui import QPen, QColor, QPainterPath, QFont, QPolygonF, QBrush, QImage
from PyQt6.QtCore import Qt, QPointF

class GeometryMixin:
    """幾何演算・スナップ・各種編集エンジン"""

    def set_otrack_enabled(self, enabled):
        self.otrack_enabled = enabled
        self.clear_tracking_lines()

    def clear_tracking_lines(self):
        for item in self.tracking_items:
            self.safe_remove_item(item)
        self.tracking_items.clear()

    def get_snap_points(self, current_pos=None):
        snaps = []
        geoms = []
        for shape in self.shapes:
            stype = shape.get("type")
            g = None
            if stype in ["line", "dimension", "arrow", "leader"]:
                p1, p2 = shape["p1"], shape["p2"]
                snaps.extend([
                    (p1[0], p1[1], "END"), (p2[0], p2[1], "END"),
                    ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, "MID")
                ])
                g = LineString([p1, p2])
            elif stype == "rect":
                x1, y1 = shape["p1"][0], shape["p1"][1]
                x2, y2 = shape["p2"][0], shape["p2"][1]
                corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                for c in corners: snaps.append((c[0], c[1], "END"))
                for i in range(4):
                    p_a, p_b = corners[i], corners[(i + 1) % 4]
                    snaps.append(((p_a[0] + p_b[0]) / 2, (p_a[1] + p_b[1]) / 2, "MID"))
                snaps.append(((x1 + x2) / 2, (y1 + y2) / 2, "CENTER"))
                g = LineString(corners + [(x1, y1)])
            elif stype in ["circle", "arc"]:
                cx, cy, r = shape["center"][0], shape["center"][1], shape["radius"]
                snaps.append((cx, cy, "CENTER"))
                g = Point(cx, cy).buffer(r).boundary
            elif stype in ["polyline", "spline"]:
                pts = shape["points"]
                for p in pts: snaps.append((p[0], p[1], "END"))
                for i in range(len(pts) - 1):
                    snaps.append(((pts[i][0] + pts[i+1][0]) / 2, (pts[i][1] + pts[i+1][1]) / 2, "MID"))
                g = LineString(pts)

            if g is not None: geoms.append(g)
        return snaps

    def get_snapped_pos(self, raw_pos):
        self.clear_tracking_lines()
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
        best_pt = raw_pos
        best_type = None
        min_dist = float("inf")

        for x, y, stype in snaps:
            dist = math.hypot(raw_pos.x() - x, raw_pos.y() - y)
            if dist < self.snap_threshold and dist < min_dist:
                min_dist, best_pt, best_type = dist, QPointF(x, y), stype

        if min_dist < float("inf"):
            self.update_snap_marker(best_pt, best_type)
            return best_pt

        if self.otrack_enabled and snaps:
            rx, ry = raw_pos.x(), raw_pos.y()
            track_x, track_y = None, None
            ref_x_pt, ref_y_pt = None, None

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
        if self.snap_marker:
            self.safe_remove_item(self.snap_marker)
            self.snap_marker = None

        if pos:
            size = 10
            colors = {
                "END": Qt.GlobalColor.red, "MID": Qt.GlobalColor.yellow,
                "CENTER": Qt.GlobalColor.blue, "INTER": Qt.GlobalColor.cyan,
                "GRID": Qt.GlobalColor.magenta
            }
            marker_color = colors.get(stype, Qt.GlobalColor.green)
            pen = QPen(marker_color, 2)
            self.snap_marker = self.scene.addRect(pos.x() - size/2, pos.y() - size/2, size, size, pen)
            self.snap_marker.setZValue(100)

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
            elif shape_type == "RECT":
                self.scene.addRect(cx - current_r, cy - current_r, 2 * current_r, 2 * current_r, pen)
                self.shapes.append({"type": "rect", "p1": (cx - current_r, cy - current_r), "p2": (cx + current_r, cy + current_r), "layer": self.active_layer, "color": self.current_color})
            elif shape_type == "POLYGON":
                pts = self._calc_regular_polygon_points_by_angle(cx, cy, current_r, sides, 0.0)
                self.scene.addPolygon(QPolygonF([QPointF(px, py) for px, py in pts]), pen, QBrush(Qt.BrushStyle.NoBrush))
                self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color})
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

        self.scene.addLine(s1["p1"][0], s1["p1"][1], px, py, pen1)
        self.scene.addLine(px, py, s2["p2"][0], s2["p2"][1], pen2)

        if s1 in self.shapes: self.shapes.remove(s1)
        if s2 in self.shapes: self.shapes.remove(s2)
        
        self.shapes.append({"type": "line", "p1": s1["p1"], "p2": (px, py), "layer": s1.get("layer", self.active_layer), "color": s1.get("color", self.current_color)})
        self.shapes.append({"type": "line", "p1": (px, py), "p2": s2["p2"], "layer": s2.get("layer", self.active_layer), "color": s2.get("color", self.current_color)})
        self.commit_history_record()