import math
from PyQt6.QtWidgets import (QGraphicsView, QMessageBox, QInputDialog, QMenu, 
                             QGraphicsItem, QGraphicsItemGroup, QGraphicsPixmapItem, 
                             QGraphicsLineItem, QGraphicsRectItem, QGraphicsEllipseItem, 
                             QGraphicsPolygonItem, QGraphicsPathItem, QGraphicsTextItem)
from PyQt6.QtGui import QPen, QColor, QBrush, QFont, QPainterPath, QPolygonF
from PyQt6.QtCore import Qt, QPointF

class CADCanvasBase(QGraphicsView):
    """CADCanvas の基底クラス（基本操作・履歴・属性・ブロック・グループ管理）"""

    def __init__(self):
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
        if not is_export and getattr(self, 'is_dark_mode', False):
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

    # --- グループ・ブロック・コンテキストメニュー操作 ---
    def group_selected_items(self):
        selected = self.scene.selectedItems()
        if len(selected) < 2:
            QMessageBox.warning(self, "通知", "グループ化するには2つ以上の要素を選択してください。")
            return False
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
            pen = QPen(color, self.current_thickness, self.current_style)
            if stype == "line": item = self.scene.addLine(px + s["p1"][0], py + s["p1"][1], px + s["p2"][0], py + s["p2"][1], pen)
            elif stype == "rect": item = self.scene.addRect(px + min(s["p1"][0], s["p2"][0]), py + min(s["p1"][1], s["p2"][1]), abs(s["p2"][0] - s["p1"][0]), abs(s["p2"][1] - s["p1"][1]), pen)
            elif stype == "circle": item = self.scene.addEllipse(px + s["center"][0] - s["radius"], py + s["center"][1] - s["radius"], 2 * s["radius"], 2 * s["radius"], pen)
            else: continue
            group_items.append(item)

        if group_items:
            group = self.scene.createItemGroup(group_items)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            self.shapes.append({"type": "block_ref", "block_name": block_name, "pos": (px, py), "layer": self.active_layer, "color": self.current_color})
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