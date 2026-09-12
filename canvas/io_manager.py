import os
import json
import subprocess
import pymupdf
import ezdxf

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QGraphicsPixmapItem
from PyQt6.QtGui import QColor, QImage, QPixmap, QPainter, QPageSize, QPageLayout, QPen
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
from PyQt6.QtCore import Qt, QPointF, QRectF

class IOMixin:
    """ファイル入出力・印刷・PDF出力管理 Mixin"""

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
                elif stype == "polyline":
                    pts = [QPointF(pt[0], pt[1]) for pt in s["points"]]
                    if s.get("is_closed"): self.scene.addPolygon(pts, pen)
                    else:
                        path = self.scene.addPath(QPainterPath()) # 補正用
                self.shapes.append(s)
            self.apply_layer_states()
            QMessageBox.information(self, "成功", f"プロジェクトを読み込みました:\n{file_path}")
        except Exception as e: QMessageBox.critical(self, "エラー", f"読み込み失敗:\n{e}")

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
        except Exception as e: QMessageBox.critical(self, "エラー", f"JWW読み込み失敗:\n{e}")

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
        self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())

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