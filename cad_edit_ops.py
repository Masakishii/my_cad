import math
import copy
from shapely.geometry import LineString, Point, Polygon, MultiPoint, GeometryCollection, MultiPolygon
from shapely.ops import split, snap, unary_union, polygonize

from PyQt6.QtWidgets import QMessageBox, QInputDialog, QGraphicsItem, QGraphicsItemGroup, QColorDialog, QGraphicsLineItem, QGraphicsTextItem, QGraphicsEllipseItem, QGraphicsPixmapItem
from PyQt6.QtGui import QPen, QColor, QFont, QPainterPath, QPolygonF, QBrush
from PyQt6.QtCore import Qt, QPointF

from cad_items import CustomPixmapItem
from cad_dialogs import TextEditDialog, TableEditDialog

class EditOpsMixin:
    """図形編集・グループ化・ブロック・ハッチング等用 Mixin"""

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
            is_movable = (self.mode == "SELECT")
            c_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            c_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, is_movable)

            self.shapes.append({"type": "polyline", "points": pts, "is_closed": True, "layer": self.active_layer, "color": self.current_color, "item": c_item})
            self.safe_remove_item(item)
        self.commit_history_record()

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
        dlg = TextEditDialog(
            self, text=shape.get("text", ""), font_size=shape.get("font_size", 12),
            color=shape.get("color", self.current_color),
            font_family=shape.get("font_family", "Meiryo"),
            bold=shape.get("bold", False), italic=shape.get("italic", False)
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_txt, new_size, new_color, font_family, is_bold, is_italic = dlg.get_result()
            if new_txt.strip():
                self.start_history_record()
                shape.update({"text": new_txt.strip(), "font_size": new_size, "color": new_color,
                              "font_family": font_family, "bold": is_bold, "italic": is_italic})
                item = shape.get("item")
                if item and isinstance(item, QGraphicsTextItem):
                    item.setPlainText(new_txt.strip())
                    font = QFont(font_family, int(new_size))
                    font.setBold(is_bold)
                    font.setItalic(is_italic)
                    item.setFont(font)
                    item.setDefaultTextColor(self.get_display_color(new_color))
                self.commit_history_record()

    def edit_table_shape(self, shape):
        if not shape or shape.get("type") != "table": return
        grp_item = shape.get("item")
        if grp_item and isinstance(grp_item, QGraphicsItemGroup):
            pos = grp_item.scenePos()
        else:
            pos = QPointF(*shape.get("pos", (0, 0)))

        dlg = TableEditDialog(
            self, grid_data=shape.get("grid_data", [[""]]), cell_w=shape.get("cell_w", 100),
            cell_h=shape.get("cell_h", 30), align=shape.get("align", "CENTER"),
            font_size=shape.get("font_size", 12), color=shape.get("color", self.current_color),
            font_family=shape.get("font_family", "Meiryo"), bold=shape.get("bold", False), italic=shape.get("italic", False)
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_grid, new_w, new_h, new_align, new_fsize, new_color, f_family, is_bold, is_italic = dlg.get_result()
            shape.update({"font_family": f_family, "bold": is_bold, "italic": is_italic})
            self.add_table_data(new_grid, new_w, new_h, pos, align=new_align, font_size=new_fsize, color=new_color, target_shape=shape)

    def add_table_data(self, grid_data, cell_w, cell_h, pos, align="CENTER", font_size=None, color=None, target_shape=None):
        rows, cols = len(grid_data), max(len(r) for r in grid_data) if grid_data else 0
        if rows == 0 or cols == 0: return
        align = str(align).upper()
        font_size = int(font_size) if font_size is not None else max(10, int(self.current_thickness * 3.5))
        table_color = QColor(color) if color else self.current_color
        
        f_family = target_shape.get("font_family", "Meiryo") if target_shape else "Meiryo"
        is_bold = target_shape.get("bold", False) if target_shape else False
        is_italic = target_shape.get("italic", False) if target_shape else False
        
        font = QFont(f_family, font_size)
        font.setBold(is_bold)
        font.setItalic(is_italic)

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
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            if target_shape and target_shape in self.shapes:
                if target_shape.get("item"): self.safe_remove_item(target_shape["item"])
                target_shape.update({"grid_data": grid_data, "cell_w": cell_w, "cell_h": cell_h, "align": align, "font_size": font_size, "color": table_color, "pos": (sx, sy), "item": group})
            else:
                self.shapes.append({"type": "table", "grid_data": grid_data, "cell_w": cell_w, "cell_h": cell_h, "align": align, "font_size": font_size, "pos": (sx, sy), "layer": self.active_layer, "color": table_color, "thickness": self.current_thickness, "style": self.current_style, "font_family": f_family, "bold": is_bold, "italic": is_italic, "item": group})
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
        
        arrow_angle = math.atan2(p1.y() - p2.y(), p1.x() - p2.x())
        leader_items.extend(self._draw_arrow_head_shape(p1, arrow_angle))

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

    def _add_cloned_shape_record(self, orig_item, cloned_item):
        for shape in list(self.shapes):
            if shape.get("item") == orig_item:
                s_copy = copy.deepcopy({k: v for k, v in shape.items() if k != "item"})
                s_copy["item"] = cloned_item
                self.shapes.append(s_copy)
                break

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
                s_copy = copy.deepcopy({k: v for k, v in shape.items() if k != "item"})
                self._translate_shape(s_copy, dx, dy)
                new_item = self._recreate_shape_item(s_copy)
                if new_item:
                    new_item.setSelected(True)
                    s_copy["item"] = new_item
                    self.shapes.append(s_copy)
        self.commit_history_record()

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
                        s_copy = copy.deepcopy({k: v for k, v in shape.items() if k != "item"})
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