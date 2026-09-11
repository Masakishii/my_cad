import math
import pymupdf
import ezdxf
from shapely.geometry import LineString, Point, Polygon, MultiPoint

from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QInputDialog, QMessageBox, 
                             QFileDialog, QGraphicsItem)
from PyQt6.QtGui import (QPen, QColor, QPixmap, QPolygonF, QBrush, QFont, QImage, 
                         QPainterPath)
from PyQt6.QtCore import Qt, QPointF

class CADCanvas(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.scene.setSceneRect(0, 0, 1200, 800)
        
        # 描画プロパティ
        self.current_color = QColor(0, 0, 0)
        self.current_thickness = 2
        self.current_style = Qt.PenStyle.SolidLine
        self.mode = "SELECT"
        self.scale_factor = 1.0
        
        # 角度スナップ (15度刻み) のフラグ
        self.angle_snap_enabled = True
        
        # 状態管理
        self.start_point = None
        self.temp_item = None
        self.click_points = []
        self.poly_points = []
        self.poly_temp_items = []
        
        # スナップ管理
        self.snap_marker = None
        self.snap_threshold = 15.0
        
        # 保存用データ
        self.shapes = []

    def set_color(self, color): self.current_color = color
    def set_thickness(self, thickness): self.current_thickness = thickness
    def set_style(self, style): self.current_style = style
    def set_angle_snap(self, enabled): self.angle_snap_enabled = enabled

    def set_mode(self, mode):
        self.finish_multi_point_mode()
        self.mode = mode
        
        is_select = (mode in ["SELECT", "CLOUD_OBJECT"])
        for item in self.scene.items():
            if isinstance(item, QGraphicsItem):
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, is_select)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, mode == "SELECT")

    def set_background_file(self, file_path):
        if file_path.lower().endswith('.pdf'):
            doc = pymupdf.open(file_path)
            page = doc[0]
            pix = page.get_pixmap(dpi=150)
            fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
            qimg = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
            pixmap = QPixmap.fromImage(qimg)
        else:
            pixmap = QPixmap(file_path)

        self.scene.addPixmap(pixmap)
        self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            for item in self.scene.selectedItems():
                self.scene.removeItem(item)
        else:
            super().keyPressEvent(event)

    # --- 15度刻み角度補正 ---
    def apply_angle_snap(self, p1, p2):
        if not self.angle_snap_enabled or not p1:
            return p2
        
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-4:
            return p2
        
        angle_rad = math.atan2(dy, dx)
        angle_deg = math.degrees(angle_rad)
        
        # 15度単位に丸める (15, 30, 45, 60, 75, 90...)
        snapped_deg = round(angle_deg / 15.0) * 15.0
        snapped_rad = math.radians(snapped_deg)
        
        nx = p1.x() + dist * math.cos(snapped_rad)
        ny = p1.y() + dist * math.sin(snapped_rad)
        return QPointF(nx, ny)

    # --- 高精度スナップ（端点・中点・中心点・交点） ---
    def get_snap_points(self):
        pts = []
        geoms = []

        for shape in self.shapes:
            stype = shape.get("type")
            g = None
            if stype in ["line", "dimension", "arrow"]:
                p1, p2 = shape["p1"], shape["p2"]
                pts.extend([p1, p2, ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)])
                g = LineString([p1, p2])
            elif stype == "rect":
                x1, y1 = shape["p1"]
                x2, y2 = shape["p2"]
                corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                pts.extend(corners)
                pts.append(((x1 + x2) / 2, (y1 + y2) / 2))
                g = LineString(corners + [(x1, y1)])
            elif stype == "circle":
                cx, cy = shape["center"]
                r = shape["radius"]
                pts.append((cx, cy))
                g = Point(cx, cy).buffer(r).boundary
            elif stype == "polyline":
                poly_pts = shape["points"]
                pts.extend(poly_pts)
                for i in range(len(poly_pts) - 1):
                    p1, p2 = poly_pts[i], poly_pts[i+1]
                    pts.append(((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2))
                g = LineString(poly_pts)

            if g is not None:
                geoms.append(g)

        # Shapelyを使用した「交点 (Intersection)」自動計算
        for i in range(len(geoms)):
            for j in range(i + 1, len(geoms)):
                try:
                    inter = geoms[i].intersection(geoms[j])
                    if not inter.is_empty:
                        if isinstance(inter, Point):
                            pts.append((inter.x, inter.y))
                        elif isinstance(inter, MultiPoint):
                            for p in inter.geoms:
                                pts.append((p.x, p.y))
                except Exception:
                    pass

        return pts

    def get_snapped_pos(self, raw_pos):
        if self.mode == "SELECT":
            return raw_pos
        
        snap_points = self.get_snap_points()
        best_pt = raw_pos
        min_dist = float("inf")

        for pt in snap_points:
            dist = math.hypot(raw_pos.x() - pt[0], raw_pos.y() - pt[1])
            if dist < self.snap_threshold and dist < min_dist:
                min_dist = dist
                best_pt = QPointF(pt[0], pt[1])

        self.update_snap_marker(best_pt if min_dist < float("inf") else None)
        return best_pt

    def update_snap_marker(self, pos):
        if self.snap_marker:
            self.scene.removeItem(self.snap_marker)
            self.snap_marker = None
        if pos:
            size = 8
            self.snap_marker = self.scene.addRect(
                pos.x() - size / 2, pos.y() - size / 2, size, size, QPen(Qt.GlobalColor.green, 2)
            )
            self.snap_marker.setZValue(100)

# --- 選択図形を雲マーク化する機能（外向き修正版） ---
    def convert_selected_to_cloud(self):
        selected_items = self.scene.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "通知", "雲マークに変換したい図形を画面上で選択してください。")
            return

        pen = QPen(self.current_color, self.current_thickness, self.current_style)

        for item in selected_items:
            rect = item.boundingRect()
            path = QPainterPath()
            
            step = 25.0  # モコモコの幅ピッチ
            pts = []
            
            x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
            corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
            for i in range(len(corners) - 1):
                p1, p2 = corners[i], corners[i+1]
                length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                count = max(1, int(length / step))
                for c in range(count):
                    t = c / count
                    pts.append((p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])))
            pts.append(corners[0])

            path.moveTo(QPointF(pts[0][0], pts[0][1]))
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
                dx, dy = p2[0] - p1[0], p2[1] - p1[1]
                dist = math.hypot(dx, dy)
                if dist > 0:
                    # 法線ベクトルを反転させて外向きに修正 (dy/dist, -dx/dist)
                    nx, ny = dy / dist, -dx / dist
                    arc_height = 12.0  # 膨らみの高さ
                    cx, cy = mx + nx * arc_height, my + ny * arc_height
                    path.quadTo(QPointF(cx, cy), QPointF(p2[0], p2[1]))

            self.scene.addPath(path, pen)
            self.scene.removeItem(item)  # 元のガイド図形を削除

            # 点群間を外向きの膨らみ曲線（ベジェ）で結ぶ
            path.moveTo(QPointF(pts[0][0], pts[0][1]))
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
                dx, dy = p2[0] - p1[0], p2[1] - p1[1]
                dist = math.hypot(dx, dy)
                if dist > 0:
                    # 法線方向（外向き）にオフセットした制御点
                    nx, ny = -dy / dist, dx / dist
                    cx, cy = mx + nx * 10, my + ny * 10
                    path.quadTo(QPointF(cx, cy), QPointF(p2[0], p2[1]))

            self.scene.addPath(path, pen)
            self.scene.removeItem(item)  # 元の図形を削除して置き換え

    # --- マウス操作 ---
    def mousePressEvent(self, event):
        raw_pos = self.mapToScene(event.pos())
        
        if self.mode == "CLOUD_OBJECT":
            super().mousePressEvent(event)
            self.convert_selected_to_cloud()
            return

        pos = self.get_snapped_pos(raw_pos)

        if event.button() == Qt.MouseButton.RightButton:
            if self.mode in ["POLYLINE", "POLYGON"]:
                self.finish_polyline()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if self.mode == "SELECT":
                super().mousePressEvent(event)
                return

            if self.mode in ["LINE", "H_LINE", "V_LINE", "RECT", "CIRCLE_2P", "ELLIPSE", "CLOUD", "ARROW", "DIMENSION", "LEADER"]:
                self.start_point = pos
            elif self.mode == "TEXT":
                self.add_text_item(pos)
            elif self.mode in ["CIRCLE_3P", "ARC_3P"]:
                self.click_points.append(pos)
                if len(self.click_points) == 3:
                    self.create_3pt_shape()
            elif self.mode in ["POLYLINE", "POLYGON"]:
                self.poly_points.append((pos.x(), pos.y()))
                if len(self.poly_points) > 1:
                    p1, p2 = self.poly_points[-2], self.poly_points[-1]
                    pen = QPen(self.current_color, self.current_thickness, self.current_style)
                    self.poly_temp_items.append(self.scene.addLine(p1[0], p1[1], p2[0], p2[1], pen))

    def mouseMoveEvent(self, event):
        raw_pos = self.mapToScene(event.pos())

        if self.mode == "SELECT":
            super().mouseMoveEvent(event)
            return

        # 1. 角度スナップの適用
        pos_angled = self.apply_angle_snap(self.start_point, raw_pos) if self.start_point else raw_pos
        # 2. 端点・交点スナップの適用
        current_pos = self.get_snapped_pos(pos_angled)

        pen_preview = QPen(self.current_color, 1, Qt.PenStyle.DashLine)

        if self.start_point:
            if self.temp_item:
                self.scene.removeItem(self.temp_item)
                self.temp_item = None

            x1, y1 = self.start_point.x(), self.start_point.y()
            x2, y2 = current_pos.x(), current_pos.y()

            if self.mode in ["LINE", "ARROW", "DIMENSION", "LEADER"]:
                self.temp_item = self.scene.addLine(x1, y1, x2, y2, pen_preview)
            elif self.mode == "H_LINE":
                self.temp_item = self.scene.addLine(x1, y1, x2, y1, pen_preview)
            elif self.mode == "V_LINE":
                self.temp_item = self.scene.addLine(x1, y1, x1, y2, pen_preview)
            elif self.mode == "RECT":
                self.temp_item = self.scene.addRect(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2), pen_preview)
            elif self.mode == "CIRCLE_2P":
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                r = math.hypot(x2 - x1, y2 - y1) / 2
                self.temp_item = self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen_preview)
            elif self.mode == "ELLIPSE":
                self.temp_item = self.scene.addEllipse(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2), pen_preview)
            elif self.mode == "CLOUD":
                path = QPainterPath()
                path.moveTo(self.start_point)
                path.quadTo((x1 + x2) / 2, y1 - 15, x2, y2)
                self.temp_item = self.scene.addPath(path, pen_preview)

        elif self.mode in ["POLYLINE", "POLYGON"] and len(self.poly_points) > 0:
            if self.temp_item:
                self.scene.removeItem(self.temp_item)
            last_pt = self.poly_points[-1]
            self.temp_item = self.scene.addLine(last_pt[0], last_pt[1], current_pos.x(), current_pos.y(), pen_preview)

    def mouseReleaseEvent(self, event):
        if self.mode == "SELECT":
            super().mouseReleaseEvent(event)
            return

        if event.button() != Qt.MouseButton.LeftButton or not self.start_point:
            return

        raw_pos = self.mapToScene(event.pos())
        pos_angled = self.apply_angle_snap(self.start_point, raw_pos)
        end_point = self.get_snapped_pos(pos_angled)
        
        pen = QPen(self.current_color, self.current_thickness, self.current_style)
        x1, y1 = self.start_point.x(), self.start_point.y()
        x2, y2 = end_point.x(), end_point.y()

        if self.temp_item:
            self.scene.removeItem(self.temp_item)
            self.temp_item = None

        if self.mode == "LINE":
            self.scene.addLine(x1, y1, x2, y2, pen)
            self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (x2, y2), "color": self.current_color})
        elif self.mode == "H_LINE":
            self.scene.addLine(x1, y1, x2, y1, pen)
            self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (x2, y1), "color": self.current_color})
        elif self.mode == "V_LINE":
            self.scene.addLine(x1, y1, x1, y2, pen)
            self.shapes.append({"type": "line", "p1": (x1, y1), "p2": (x1, y2), "color": self.current_color})
        elif self.mode == "RECT":
            rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2)
            self.scene.addRect(rx, ry, rw, rh, pen)
            self.shapes.append({"type": "rect", "p1": (x1, y1), "p2": (x2, y2), "color": self.current_color})
        elif self.mode == "CIRCLE_2P":
            cx, cy, r = (x1 + x2) / 2, (y1 + y2) / 2, math.hypot(x2 - x1, y2 - y1) / 2
            self.scene.addEllipse(cx - r, cy - r, 2 * r, 2 * r, pen)
            self.shapes.append({"type": "circle", "center": (cx, cy), "radius": r, "color": self.current_color})
        elif self.mode == "ELLIPSE":
            rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2)
            self.scene.addEllipse(rx, ry, rw, rh, pen)
            self.shapes.append({"type": "ellipse", "center": (rx + rw / 2, ry + rh / 2), "rx": rw / 2, "ry": rh / 2, "color": self.current_color})
        elif self.mode == "CLOUD":
            path = QPainterPath()
            path.moveTo(self.start_point)
            path.quadTo((x1 + x2) / 2, y1 - 15, x2, y2)
            self.scene.addPath(path, pen)
        elif self.mode == "ARROW":
            self.add_arrow(self.start_point, end_point)
        elif self.mode == "DIMENSION":
            self.add_dimension(self.start_point, end_point)
        elif self.mode == "LEADER":
            self.add_leader(self.start_point, end_point)

        self.start_point = None

    def add_arrow(self, p1, p2):
        pen = QPen(self.current_color, self.current_thickness, self.current_style)
        self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
        angle = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
        self._draw_arrow_head(p2, angle + math.pi)
        self.shapes.append({"type": "arrow", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "color": self.current_color})

    def add_dimension(self, p1, p2):
        pen = QPen(self.current_color, self.current_thickness, self.current_style)
        self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
        
        dist_px = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
        val_str = f"{dist_px * self.scale_factor:.1f}"

        angle = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
        self._draw_arrow_head(p1, angle)
        self._draw_arrow_head(p2, angle + math.pi)

        mid_x, mid_y = (p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2
        text_item = self.scene.addText(val_str)
        text_item.setDefaultTextColor(self.current_color)
        text_item.setFont(QFont("Meiryo", max(10, self.current_thickness * 4)))
        text_item.setPos(mid_x - 15, mid_y - 20)

        self.shapes.append({"type": "dimension", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "val_str": val_str, "color": self.current_color})

    def add_leader(self, p1, p2):
        text, ok = QInputDialog.getText(self, "引き出し線注釈", "注釈文字を入力してください:")
        if not ok or not text: return

        pen = QPen(self.current_color, self.current_thickness, self.current_style)
        self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
        angle = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
        self._draw_arrow_head(p1, angle)

        landing_length = 40 if p2.x() >= p1.x() else -40
        p3_x = p2.x() + landing_length
        self.scene.addLine(p2.x(), p2.y(), p3_x, p2.y(), pen)

        text_item = self.scene.addText(text)
        text_item.setDefaultTextColor(self.current_color)
        font_size = max(11, self.current_thickness * 4)
        text_item.setFont(QFont("Meiryo", font_size))
        
        rect = text_item.boundingRect()
        tx = p2.x() if landing_length > 0 else (p3_x - rect.width())
        ty = p2.y() - rect.height() + (rect.height() * 0.15)
        text_item.setPos(tx, ty)

        self.shapes.append({"type": "leader", "p1": (p1.x(), p1.y()), "p2": (p2.x(), p2.y()), "text": text, "color": self.current_color})

    def _draw_arrow_head(self, pos, angle):
        arrow_size = max(10, self.current_thickness * 3.5)
        arrow_angle = math.pi / 6
        p_a1 = QPointF(pos.x() + arrow_size * math.cos(angle - arrow_angle), pos.y() + arrow_size * math.sin(angle - arrow_angle))
        p_a2 = QPointF(pos.x() + arrow_size * math.cos(angle + arrow_angle), pos.y() + arrow_size * math.sin(angle + arrow_angle))
        pen = QPen(self.current_color, 1, Qt.PenStyle.SolidLine)
        polygon = QPolygonF([pos, p_a1, p_a2])
        self.scene.addPolygon(polygon, pen, QBrush(self.current_color))

    def add_text_item(self, pos):
        text, ok = QInputDialog.getText(self, "テキスト入力", "注釈文字を入力してください:")
        if ok and text:
            text_item = self.scene.addText(text)
            text_item.setDefaultTextColor(self.current_color)
            font_size = max(12, self.current_thickness * 5)
            text_item.setFont(QFont("Meiryo", font_size))
            text_item.setPos(pos)
            self.shapes.append({"type": "text", "text": text, "pos": (pos.x(), pos.y()), "font_size": font_size, "color": self.current_color})

    def create_3pt_shape(self):
        p1, p2, p3 = self.click_points
        x1, y1, x2, y2, x3, y3 = p1.x(), p1.y(), p2.x(), p2.y(), p3.x(), p3.y()
        d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        
        if abs(d) > 1e-6:
            ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)) / d
            uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)) / d
            r = math.hypot(x1 - ux, y1 - uy)
            pen = QPen(self.current_color, self.current_thickness, self.current_style)

            if self.mode == "CIRCLE_3P":
                self.scene.addEllipse(ux - r, uy - r, 2 * r, 2 * r, pen)
                self.shapes.append({"type": "circle", "center": (ux, uy), "radius": r, "color": self.current_color})
            elif self.mode == "ARC_3P":
                a1, a3 = math.atan2(y1 - uy, x1 - ux), math.atan2(y3 - uy, x3 - ux)
                path = QPainterPath()
                path.arcTo(ux - r, uy - r, 2 * r, 2 * r, -math.degrees(a1), -math.degrees(a3 - a1))
                self.scene.addPath(path, pen)

        self.click_points.clear()

    def finish_polyline(self):
        if self.temp_item:
            self.scene.removeItem(self.temp_item)
            self.temp_item = None

        if len(self.poly_points) > 1:
            is_closed = (self.mode == "POLYGON")
            if is_closed:
                p1, p2 = self.poly_points[-1], self.poly_points[0]
                pen = QPen(self.current_color, self.current_thickness, self.current_style)
                self.poly_temp_items.append(self.scene.addLine(p1[0], p1[1], p2[0], p2[1], pen))

            self.shapes.append({"type": "polyline", "points": list(self.poly_points), "is_closed": is_closed, "color": self.current_color})
        elif len(self.poly_points) == 1:
            for item in self.poly_temp_items:
                self.scene.removeItem(item)

        self.poly_points.clear()
        self.poly_temp_items.clear()

    def finish_multi_point_mode(self):
        self.click_points.clear()
        self.update_snap_marker(None)
        if self.temp_item:
            self.scene.removeItem(self.temp_item)
            self.temp_item = None

    def save_to_dxf(self):
        self.finish_polyline()
        if not self.shapes:
            QMessageBox.warning(self, "警告", "保存する図形がありません。")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "DXF形式で保存", "", "DXF Files (*.dxf)")
        if not file_path: return

        try:
            doc = ezdxf.new('R2010')
            msp = doc.modelspace()

            for shape in self.shapes:
                color = shape["color"]
                attribs = {'true_color': ezdxf.rgb2int((color.red(), color.green(), color.blue()))}
                stype = shape["type"]

                if stype in ["line", "arrow", "dimension"]:
                    x1, y1 = shape["p1"]
                    x2, y2 = shape["p2"]
                    msp.add_line((x1, -y1), (x2, -y2), dxfattribs=attribs)
                elif stype == "rect":
                    x1, y1 = shape["p1"]
                    x2, y2 = shape["p2"]
                    msp.add_lwpolyline([(x1, -y1), (x2, -y1), (x2, -y2), (x1, -y2)], close=True, dxfattribs=attribs)
                elif stype == "circle":
                    cx, cy = shape["center"]
                    msp.add_circle((cx, -cy), shape["radius"], dxfattribs=attribs)
                elif stype == "ellipse":
                    cx, cy = shape["center"]
                    rx, ry = shape["rx"], shape["ry"]
                    major = (rx, 0) if rx >= ry else (0, -ry)
                    ratio = (ry / rx) if rx >= ry else (rx / ry)
                    msp.add_ellipse((cx, -cy), major_axis=major, ratio=ratio, dxfattribs=attribs)
                elif stype == "polyline":
                    pts = [(x, -y) for x, y in shape["points"]]
                    msp.add_lwpolyline(pts, close=shape["is_closed"], dxfattribs=attribs)
                elif stype == "text":
                    tx, ty = shape["pos"]
                    t_item = msp.add_text(shape["text"], dxfattribs={'height': shape["font_size"], 'true_color': attribs['true_color']})
                    t_item.set_placement((tx, -ty))

            doc.saveas(file_path)
            QMessageBox.information(self, "成功", f"DXFファイルを保存しました:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"DXF保存失敗:\n{e}")