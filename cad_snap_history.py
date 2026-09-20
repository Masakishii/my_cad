import math
from PyQt6.QtWidgets import QGraphicsItemGroup
from PyQt6.QtGui import QPen
from PyQt6.QtCore import Qt, QPointF

class SnapAndHistoryMixin:
    """履歴管理 (Undo/Redo) および スナップ機能 Mixin"""

    def start_history_record(self): 
        self._before_items, self._before_shapes_len = set(self.scene.items()), len(self.shapes)

    def commit_history_record(self):
        added_items = [i for i in (set(self.scene.items()) - self._before_items) if i not in (self.temp_item, self.snap_marker, self.paper_guide_item, getattr(self, 'custom_print_rect_item', None)) and i not in self.poly_temp_items]
        added_shapes = self.shapes[self._before_shapes_len:]
        if added_items or added_shapes: 
            self.undo_stack.append(("add", added_items, added_shapes))
            self.redo_stack.clear()

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

    def set_grid_snap(self, enabled, size=None):
        self.grid_snap_enabled = enabled
        if size: self.grid_size = float(size)

    def set_angle_snap(self, enabled): self.angle_snap_enabled = enabled
    def set_otrack_enabled(self, enabled): self.otrack_enabled = enabled; self.clear_tracking_lines()

    def apply_angle_snap(self, p1, p2):
        if not self.angle_snap_enabled or not p1: return p2
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y(); dist = math.hypot(dx, dy)
        if dist < 1e-4: return p2
        snapped_deg = round(math.degrees(math.atan2(dy, dx)) / 15.0) * 15.0
        return QPointF(p1.x() + dist * math.cos(math.radians(snapped_deg)), p1.y() + dist * math.sin(math.radians(snapped_deg)))

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
            elif stype in ["circle", "arc"]: 
                snaps.append((shape["center"][0], shape["center"][1], "CENTER"))
            elif stype in ["polyline", "spline"]:
                pts = shape["points"]
                for p in pts: snaps.append((p[0], p[1], "END"))
                for i in range(len(pts) - 1):
                    snaps.append(((pts[i][0] + pts[i+1][0]) / 2, (pts[i][1] + pts[i+1][1]) / 2, "MID"))

        return snaps

    def get_snapped_pos(self, raw_pos):
        self.clear_tracking_lines()
        if self.mode == "SELECT": return raw_pos

        raw_screen_pt = self.mapFromScene(raw_pos)
        snaps = self.get_snap_points(raw_pos)
        best_pt, best_type, min_pixel_dist = raw_pos, None, float("inf")
        MAX_SNAP_PIXELS = 12.0

        for x, y, stype in snaps:
            scene_pt = QPointF(x, y)
            screen_pt = self.mapFromScene(scene_pt)
            pixel_dist = math.hypot(raw_screen_pt.x() - screen_pt.x(), raw_screen_pt.y() - screen_pt.y())
            weight = 0.8 if stype == "END" else 1.0
            effective_dist = pixel_dist * weight

            if pixel_dist <= MAX_SNAP_PIXELS and effective_dist < min_pixel_dist:
                min_pixel_dist = effective_dist
                best_pt = scene_pt
                best_type = stype

        if best_type is not None:
            self.update_snap_marker(best_pt, best_type)
            return best_pt

        if self.grid_snap_enabled and self.grid_size > 0:
            gx = round(raw_pos.x() / self.grid_size) * self.grid_size
            gy = round(raw_pos.y() / self.grid_size) * self.grid_size
            grid_pt = QPointF(gx, gy)
            grid_screen_pt = self.mapFromScene(grid_pt)
            if math.hypot(raw_screen_pt.x() - grid_screen_pt.x(), raw_screen_pt.y() - grid_screen_pt.y()) <= MAX_SNAP_PIXELS:
                self.update_snap_marker(grid_pt, "GRID")
                return grid_pt

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