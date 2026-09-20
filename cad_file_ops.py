import os
import json
import pymupdf
import ezdxf

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QGraphicsItemGroup
from PyQt6.QtGui import QPen, QColor, QPixmap, QImage, QFont, QPolygonF, QPainter, QPageSize, QPageLayout, QTransform
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
from PyQt6.QtCore import Qt, QPointF, QRectF

from cad_utils import _clean_for_json
from cad_items import CustomPixmapItem

class FileOpsMixin:
    """ファイル操作・印刷 Mixin"""

    def new_project(self):
        if self.shapes or any(not isinstance(i, QGraphicsItemGroup) for i in self.scene.items()):
            res = QMessageBox.question(self, "新規作成", "現在の図面を全消去して新規作成しますか？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if res != QMessageBox.StandardButton.Yes: return False

        self.scene.clear(); self.shapes.clear(); self.undo_stack.clear(); self.redo_stack.clear()
        self.paper_guide_item, self.custom_print_rect_item = None, None
        self.set_mode("SELECT")
        return True

    def save_project_json(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "プロジェクトを保存", "", "CAD Project Files (*.json)")
        if not file_path: return
        try:
            self._sync_item_transforms()
            data = {"version": "1.0", "layers": _clean_for_json(self.layers), "blocks": _clean_for_json(self.blocks), "shapes": _clean_for_json(self.shapes), "paper_scale": self.paper_scale, "active_layer": self.active_layer}
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False, default=lambda obj: obj.name() if isinstance(obj, QColor) else getattr(obj, "value", str(obj)))
            QMessageBox.information(self, "成功", f"プロジェクトを保存しました:\n{file_path}")
        except Exception as e: QMessageBox.critical(self, "エラー", f"保存失敗:\n{e}")

    def load_project_json(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "プロジェクトを開く", "", "CAD Project Files (*.json)")
        if not file_path: return
        try:
            with open(file_path, "r", encoding="utf-8") as f: data = json.load(f)
            self.scene.clear(); self.shapes.clear(); self.layers.clear()

            for name, props in data.get("layers", {}).items():
                try: style = Qt.PenStyle(int(props.get("style", 1)))
                except: style = Qt.PenStyle.SolidLine
                self.layers[name] = {"color": QColor(props.get("color", "#000000")), "thickness": props.get("thickness", 2), "style": style, "visible": props.get("visible", True), "locked": props.get("locked", False), "printable": props.get("printable", True)}

            self.active_layer, self.paper_scale, self.blocks = data.get("active_layer", "0"), data.get("paper_scale", 100), data.get("blocks", {})

            for s in data.get("shapes", []):
                s["color"] = QColor(s.get("color", "#FF0000")) if isinstance(s.get("color"), str) else s.get("color")
                try: s["style"] = Qt.PenStyle(int(s.get("style", 1)))
                except: s["style"] = Qt.PenStyle.SolidLine

                stype = s.get("type")
                color = self.get_display_color(s["color"])
                thickness = s.get("thickness", self.current_thickness)
                pen = QPen(color, thickness, s["style"])

                if stype == "image":
                    fpath = s.get("file_path", "")
                    if fpath and os.path.exists(fpath):
                        pixmap = pymupdf.open(fpath)[0].get_pixmap(dpi=150) if fpath.lower().endswith('.pdf') else None
                        pixmap = QPixmap.fromImage(QImage(pixmap.samples, pixmap.width, pixmap.height, pixmap.stride, QImage.Format.Format_RGBA8888 if pixmap.alpha else QImage.Format.Format_RGB888)) if pixmap else QPixmap(fpath)
                        if pixmap and not pixmap.isNull():
                            item = CustomPixmapItem(pixmap)
                            pos_val = s.get("pos", (0, 0))
                            item.setPos(pos_val[0], pos_val[1])
                            if "transform" in s:
                                m = s["transform"]; item.setTransform(QTransform(m[0], m[1], m[2], m[3], m[4], m[5]))
                            else:
                                item.setScale(s.get("scale", 1.0)); item.setRotation(s.get("rotation", 0.0))
                            item.setOpacity(s.get("opacity", 1.0))
                            if s.get("layer") == "背景図面": item.setZValue(-100)
                            self.scene.addItem(item); s["item"] = item; self.shapes.append(s)
                    continue

                elif stype == "dimension": self.create_autocad_dimension(QPointF(*s["p1"]), QPointF(*s["p2"])); continue
                elif stype == "arrow": item = self.scene.addLine(s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1], pen); s["item"] = item; self._update_arrow_shape_graphics(s); self.shapes.append(s); continue
                elif stype == "leader": self.add_leader_with_auto_measure(QPointF(*s["p1"]), QPointF(*s["p2"]), text=s.get("text")); continue
                elif stype == "rect": item = self.scene.addRect(min(s["p1"][0], s["p2"][0]), min(s["p1"][1], s["p2"][1]), abs(s["p1"][0] - s["p2"][0]), abs(s["p1"][1] - s["p2"][1]), pen)
                elif stype == "circle": item = self.scene.addEllipse(s["center"][0] - s["radius"], s["center"][1] - s["radius"], 2 * s["radius"], 2 * s["radius"], pen)
                elif stype == "arc":
                    path = QPainterPath(); path.arcMoveTo(s["center"][0] - s["radius"], s["center"][1] - s["radius"], 2 * s["radius"], 2 * s["radius"], s["start_angle"])
                    path.arcTo(s["center"][0] - s["radius"], s["center"][1] - s["radius"], 2 * s["radius"], 2 * s["radius"], s["start_angle"], s["span_angle"])
                    item = self.scene.addPath(path, pen)
                elif stype == "table": self.add_table_data(s.get("grid_data", [[""]]), s.get("cell_w", 100), s.get("cell_h", 30), QPointF(*s.get("pos", (0, 0))), align=s.get("align", "CENTER"), font_size=s.get("font_size", 12), color=s.get("color", self.current_color), target_shape=s); continue
                elif stype == "polyline":
                    pts = [QPointF(pt[0], pt[1]) for pt in s["points"]]
                    if s.get("is_closed"): item = self.scene.addPolygon(QPolygonF(pts), pen)
                    else:
                        path = QPainterPath(); path.moveTo(pts[0])
                        for pt in pts[1:]: path.lineTo(pt)
                        item = self.scene.addPath(path, pen)
                elif stype == "text": 
                    item = self.scene.addText(s["text"]); item.setDefaultTextColor(color)
                    font = QFont(s.get("font_family", "Meiryo"), int(s.get("font_size", 12)))
                    font.setBold(s.get("bold", False)); font.setItalic(s.get("italic", False))
                    item.setFont(font); item.setPos(s["pos"][0], s["pos"][1])
                elif stype == "line": item = self.scene.addLine(s["p1"][0], s["p1"][1], s["p2"][0], s["p2"][1], pen)

                s["item"] = item; self.shapes.append(s)

            self.apply_layer_states()
            QMessageBox.information(self, "成功", f"プロジェクトを読み込みました:\n{file_path}")
        except Exception as e: QMessageBox.critical(self, "エラー", f"読み込み失敗:\n{e}")

    def import_dxf_file(self, file_path=None):
        if not file_path: file_path, _ = QFileDialog.getOpenFileName(self, "DXFファイルを開く", "", "DXF Files (*.dxf)")
        if not file_path or not os.path.exists(file_path): return
        try:
            doc = ezdxf.readfile(file_path); msp = doc.modelspace(); self.start_history_record()
            for entity in msp:
                dxftype = entity.dxftype()
                layer_name = entity.dxf.layer if hasattr(entity.dxf, 'layer') else "0"
                if layer_name not in self.layers: self.layers[layer_name] = {"color": QColor(0, 0, 0), "thickness": 2, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True}
                color = self.layers[layer_name]["color"]; pen = QPen(self.get_display_color(color), self.current_thickness, self.current_style)

                if dxftype == 'LINE':
                    p1, p2 = (entity.dxf.start.x, -entity.dxf.start.y), (entity.dxf.end.x, -entity.dxf.end.y)
                    item = self.scene.addLine(p1[0], p1[1], p2[0], p2[1], pen)
                    self.shapes.append({"type": "line", "p1": p1, "p2": p2, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
                elif dxftype == 'CIRCLE':
                    cx, cy, r = entity.dxf.center.x, -entity.dxf.center.y, entity.dxf.radius
                    item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
                    self.shapes.append({"type": "circle", "center": (cx, cy), "radius": r, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
                elif dxftype in ['LWPOLYLINE', 'POLYLINE']:
                    pts = [(p[0], -p[1]) for p in entity.get_points()]; is_closed = entity.is_closed
                    qpts = [QPointF(px, py) for px, py in pts]
                    if is_closed: item = self.scene.addPolygon(QPolygonF(qpts), pen)
                    else:
                        path = QPainterPath()
                        if qpts: path.moveTo(qpts[0]); [path.lineTo(pt) for pt in qpts[1:]]
                        item = self.scene.addPath(path, pen)
                    self.shapes.append({"type": "polyline", "points": pts, "is_closed": is_closed, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": item})
                elif dxftype in ['TEXT', 'MTEXT']:
                    txt = entity.dxf.text if dxftype == 'TEXT' else entity.text
                    pos = (entity.dxf.insert.x, -entity.dxf.insert.y) if hasattr(entity.dxf, 'insert') else (0, 0)
                    t_item = self.scene.addText(txt); t_item.setDefaultTextColor(self.get_display_color(color)); t_item.setFont(QFont("Meiryo", 12)); t_item.setPos(pos[0], pos[1])
                    self.shapes.append({"type": "text", "text": txt, "pos": pos, "font_size": 12, "layer": layer_name, "color": color, "thickness": self.current_thickness, "style": self.current_style, "item": t_item})
                elif dxftype == 'IMAGE':
                    image_def = entity.image_def
                    if image_def and image_def.dxf.filename and os.path.exists(image_def.dxf.filename):
                        pixmap = QPixmap(image_def.dxf.filename)
                        if not pixmap.isNull():
                            img_w, img_h = pixmap.width(), pixmap.height()
                            u_pix, v_pix = getattr(entity.dxf, 'u_pixel', (1.0, 0.0)), getattr(entity.dxf, 'v_pixel', (0.0, 1.0))
                            canvas_px, canvas_py = entity.dxf.insert.x + v_pix[0] * img_h, -(entity.dxf.insert.y + v_pix[1] * img_h)
                            m11, m12, m21, m22 = u_pix[0], -u_pix[1], -v_pix[0], v_pix[1]

                            item = CustomPixmapItem(pixmap); item.setPos(canvas_px, canvas_py); item.setTransform(QTransform(m11, m12, m21, m22, 0, 0))
                            if layer_name == "背景図面": item.setZValue(-100)
                            self.scene.addItem(item)
                            self.shapes.append({"type": "image", "file_path": image_def.dxf.filename, "pos": (canvas_px, canvas_py), "transform": [m11, m12, m21, m22, 0, 0], "scale": math.hypot(m11, m12), "rotation": math.degrees(math.atan2(-m12, m11)), "opacity": 1.0, "layer": layer_name, "item": item})

            self.apply_layer_states(); self.commit_history_record()
            QMessageBox.information(self, "成功", f"DXFファイルを読み込みました:\n{os.path.basename(file_path)}")
        except Exception as e: QMessageBox.critical(self, "エラー", f"DXFインポート失敗:\n{e}")

    def save_to_dxf(self):
        self.finish_polyline()
        file_path, _ = QFileDialog.getSaveFileName(self, "DXF形式で保存", "", "DXF Files (*.dxf)")
        if not file_path: return
        try:
            self._sync_item_transforms()
            doc = ezdxf.new('R2010'); msp = doc.modelspace()
            for l_name, l_props in self.layers.items():
                if not doc.layers.has_entry(l_name): doc.layers.add(name=l_name).rgb = (l_props["color"].red(), l_props["color"].green(), l_props["color"].blue())

            for shape in self.shapes:
                color, layer = shape.get("color", self.current_color), shape.get("layer", "0")
                attribs = {'true_color': ezdxf.rgb2int((color.red(), color.green(), color.blue())), 'layer': layer}
                stype = shape.get("type")

                if stype == "image":
                    fpath = shape.get("file_path", "")
                    if fpath and os.path.exists(fpath):
                        pixmap = QPixmap(fpath)
                        img_w, img_h = pixmap.width(), pixmap.height()
                        item = shape.get("item")
                        if item:
                            scene_tf = item.sceneTransform()
                            p_bl, p_br, p_tl = scene_tf.map(QPointF(0, img_h)), scene_tf.map(QPointF(img_w, img_h)), scene_tf.map(QPointF(0, 0))
                            u_vec, v_vec = (p_br.x() - p_bl.x(), -(p_br.y() - p_bl.y())), (p_tl.x() - p_bl.x(), -(p_tl.y() - p_bl.y()))
                            image_def = doc.add_image_def(filename=fpath, size_in_pixel=(img_w, img_h))
                            image_entity = msp.add_image(image_def=image_def, insert=(p_bl.x(), -p_bl.y()), size_in_units=(math.hypot(u_vec[0], u_vec[1]), math.hypot(v_vec[0], v_vec[1])), dxfattribs=attribs)
                            image_entity.dxf.u_pixel, image_entity.dxf.v_pixel = (u_vec[0] / img_w, u_vec[1] / img_w), (v_vec[0] / img_h, v_vec[1] / img_h)

                elif stype == "line": msp.add_line((shape["p1"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p2"][1]), dxfattribs=attribs)
                elif stype == "arrow": msp.add_line((shape["p1"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p2"][1]), dxfattribs=attribs)
                elif stype == "dimension":
                    msp.add_line((shape["p1"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p2"][1]), dxfattribs=attribs)
                    if shape.get("val_str"): msp.add_text(shape["val_str"], dxfattribs={'height': 12, 'insert': ((shape["p1"][0] + shape["p2"][0])/2, -(shape["p1"][1] + shape["p2"][1])/2)} | attribs)
                elif stype == "leader":
                    p1, p2 = shape["p1"], shape["p2"]
                    p3 = shape.get("p3", (p2[0] + (40 if p2[0] >= p1[0] else -40), p2[1]))
                    msp.add_line((p1[0], -p1[1]), (p2[0], -p2[1]), dxfattribs=attribs)
                    msp.add_line((p2[0], -p2[1]), (p3[0], -p3[1]), dxfattribs=attribs)
                    ang, size = math.atan2(p1[1] - p2[1], p1[0] - p2[0]), max(10, shape.get("thickness", self.current_thickness) * 3.5)
                    pa1 = (p1[0] - size * math.cos(ang - math.pi / 6), -(p1[1] - size * math.sin(ang - math.pi / 6)))
                    pa2 = (p1[0] - size * math.cos(ang + math.pi / 6), -(p1[1] - size * math.sin(ang + math.pi / 6)))
                    if shape.get("arrow_head_type", self.arrow_head_type) == "FILLED": msp.add_solid([(p1[0], -p1[1]), pa1, pa2], dxfattribs=attribs)
                    else: msp.add_line((p1[0], -p1[1]), pa1, dxfattribs=attribs); msp.add_line((p1[0], -p1[1]), pa2, dxfattribs=attribs)
                    if shape.get("text"): msp.add_text(shape["text"], dxfattribs={'height': int(shape.get("font_size", 12)), 'insert': (p2[0] if p3[0] >= p2[0] else p3[0], -p2[1] + 2)} | attribs)
                elif stype == "rect": msp.add_lwpolyline([(shape["p1"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p1"][1]), (shape["p2"][0], -shape["p2"][1]), (shape["p1"][0], -shape["p2"][1])], close=True, dxfattribs=attribs)
                elif stype == "circle": msp.add_circle((shape["center"][0], -shape["center"][1]), shape["radius"], dxfattribs=attribs)
                elif stype == "arc": msp.add_arc((shape["center"][0], -shape["center"][1]), shape["radius"], -shape["start_angle"], -shape["start_angle"] - shape["span_angle"], dxfattribs=attribs)
                elif stype in ["polyline", "spline"]: msp.add_lwpolyline([(x, -y) for x, y in shape["points"]], close=shape.get("is_closed", False), dxfattribs=attribs)
                elif stype == "text": msp.add_text(shape.get("text", ""), dxfattribs={'height': int(shape.get("font_size", 12)), 'insert': (shape["pos"][0], -shape["pos"][1])} | attribs)

            doc.saveas(file_path); QMessageBox.information(self, "成功", f"DXFファイルを保存しました:\n{file_path}")
        except Exception as e: QMessageBox.critical(self, "エラー", f"DXF保存失敗:\n{e}")

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

    def insert_image_or_pdf(self, file_path, pos=None):
        self.start_history_record()
        pixmap = None
        if file_path.lower().endswith('.pdf'):
            try:
                doc = pymupdf.open(file_path)
                pix = doc[0].get_pixmap(dpi=150)
                fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
                pixmap = QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt))
            except Exception as e: 
                QMessageBox.critical(self, "エラー", f"PDF挿入失敗:\n{e}")
                return
        else:
            pixmap = QPixmap(file_path)
            if pixmap.isNull(): return

        pixmap_item = CustomPixmapItem(pixmap)
        is_select = (self.mode == "SELECT")
        pixmap_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_select)
        pixmap_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, is_select)

        if pos:
            p_x, p_y = pos.x(), pos.y()
        else:
            scene_center = self.mapToScene(self.viewport().rect().center())
            p_x, p_y = scene_center.x() - pixmap.width() / 2.0, scene_center.y() - pixmap.height() / 2.0
        
        pixmap_item.setPos(p_x, p_y)
        self.scene.addItem(pixmap_item)

        self.shapes.append({
            "type": "image",
            "file_path": file_path,
            "pos": (p_x, p_y),
            "scale": 1.0,
            "rotation": 0.0,
            "opacity": 1.0,
            "layer": self.active_layer,
            "item": pixmap_item
        })
        self.commit_history_record()

    def set_background_file(self, file_path):
        if not os.path.exists(file_path): return
        pixmap = None
        if file_path.lower().endswith('.pdf'):
            try:
                doc = pymupdf.open(file_path)
                pix = doc[0].get_pixmap(dpi=150)
                fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
                pixmap = QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt))
            except Exception as e: 
                QMessageBox.critical(self, "エラー", f"PDF読込失敗:\n{e}")
                return
        else:
            pixmap = QPixmap(file_path)
            if pixmap.isNull(): return

        pixmap_item = CustomPixmapItem(pixmap)
        pixmap_item.setPos(0, 0)
        pixmap_item.setZValue(-100)
        self.scene.addItem(pixmap_item)

        bg_layer = "背景図面" if "背景図面" in self.layers else self.active_layer
        self.shapes.append({
            "type": "image",
            "file_path": file_path,
            "pos": (0, 0),
            "scale": 1.0,
            "rotation": 0.0,
            "opacity": 1.0,
            "layer": bg_layer,
            "item": pixmap_item
        })
        self.scene.setSceneRect(-100000, -100000, 200000, 200000)

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
            w_px, h_px, pos_x, pos_y = custom_rect.width(), custom_rect.height(), custom_rect.x(), custom_rect.y()
        else:
            mm_sizes = {
                QPageSize.PageSizeId.A4: (297, 210), QPageSize.PageSizeId.A3: (420, 297), 
                QPageSize.PageSizeId.A2: (594, 420), QPageSize.PageSizeId.B4: (364, 257), 
                QPageSize.PageSizeId.B5: (257, 182)
            }
            w_mm, h_mm = mm_sizes.get(self.paper_size_id, (297, 210))
            if self.paper_orientation == QPageLayout.Orientation.Landscape: 
                w_mm, h_mm = max(w_mm, h_mm), min(w_mm, h_mm)
            else: 
                w_mm, h_mm = min(w_mm, h_mm), max(w_mm, h_mm)
            w_px, h_px, pos_x, pos_y = w_mm * self.paper_scale, h_mm * self.paper_scale, 0, 0

        pen = QPen(QColor(0, 120, 215), max(2, int(self.paper_scale * 0.05)) if custom_rect is None else 2, Qt.PenStyle.DashDotLine)
        self.paper_guide_item = self.scene.addRect(0, 0, w_px, h_px, pen)
        self.paper_guide_item.setPos(pos_x, pos_y)
        self.paper_guide_item.setZValue(-10)

    def fit_paper_guide_to_selected(self):
        selected = self.scene.selectedItems()
        target = selected[0] if selected else next((i for i in self.scene.items() if isinstance(i, (QGraphicsPixmapItem, CustomPixmapItem))), None)
        if target:
            self.update_paper_guide(show=True, custom_rect=target.sceneBoundingRect())
            return True
        return False

    def set_custom_print_rect(self, rect):
        if self.custom_print_rect_item: 
            self.safe_remove_item(self.custom_print_rect_item)
            self.custom_print_rect_item = None
        if rect and not rect.isEmpty():
            self.custom_print_rect_item = self.scene.addRect(rect, QPen(QColor(255, 102, 0), 2, Qt.PenStyle.DashDotDotLine))
            self.custom_print_rect_item.setZValue(99)

    def clear_custom_print_rect(self):
        if self.custom_print_rect_item: 
            self.safe_remove_item(self.custom_print_rect_item)
            self.custom_print_rect_item = None

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
        printer.setPageSize(QPageSize(page_size_id))
        printer.setPageOrientation(orientation)

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