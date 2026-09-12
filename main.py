import sys, csv, os, json, traceback, urllib.request
from PyQt6.QtWidgets import (QApplication, QMainWindow, QToolBar, QFileDialog, 
                             QPushButton, QComboBox, QColorDialog, QLabel, QCheckBox, 
                             QDialog, QVBoxLayout, QHBoxLayout, QDoubleSpinBox, QSpinBox, 
                             QFormLayout, QTabWidget, QDialogButtonBox, QTableWidget, 
                             QTableWidgetItem, QMessageBox, QWidget, QLineEdit, QGraphicsTextItem,
                             QInputDialog, QListWidget, QListWidgetItem)
from PyQt6.QtGui import QAction, QPageSize, QPageLayout, QKeySequence, QColor, QFont
from PyQt6.QtCore import Qt, QSize

from canvas import CADCanvas

try:
    from secret import DISCORD_WEBHOOK_URL
except ImportError:
    DISCORD_WEBHOOK_URL = None

def global_exception_handler(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    if DISCORD_WEBHOOK_URL:
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
        full_error_msg = "".join(tb_lines)
        err_text = full_error_msg[-1800:]
        payload = {
            "username": "朱書きCAD エラー通知",
            "content": f"🚨 **エラーが発生しました**\n```python\n{err_text}\n```"
        }
        try:
            req = urllib.request.Request(
                DISCORD_WEBHOOK_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass

    sys.__excepthook__(exc_type, exc_value, exc_traceback)

sys.excepthook = global_exception_handler

# =========================================================
# 各種ダイアログ定義
# =========================================================

class CloudSettingsDialog(QDialog):
    def __init__(self, current_pitch, current_height, parent=None):
        super().__init__(parent)
        self.setWindowTitle("雲マーク設定")
        self.setFixedSize(240, 170)
        layout = QVBoxLayout()
        layout.addWidget(QLabel("モコモコの間隔 (mm):"))
        self.pitch_spin = QSpinBox(); self.pitch_spin.setRange(5, 500); self.pitch_spin.setValue(int(current_pitch))
        layout.addWidget(self.pitch_spin)
        layout.addWidget(QLabel("モコモコの高さ (mm):"))
        self.height_spin = QSpinBox(); self.height_spin.setRange(2, 200); self.height_spin.setValue(int(current_height))
        layout.addWidget(self.height_spin)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

class ShapePresetDialog(QDialog):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setWindowTitle("数値指定作図パラメータ設定")
        self.setFixedSize(300, 260)
        layout, tabs = QVBoxLayout(), QTabWidget()

        r_tab, r_form = QWidget(), QFormLayout()
        self.w_spin = QDoubleSpinBox(); self.w_spin.setRange(1, 20000); self.w_spin.setValue(canvas.preset_rect_w)
        self.h_spin = QDoubleSpinBox(); self.h_spin.setRange(1, 20000); self.h_spin.setValue(canvas.preset_rect_h)
        r_form.addRow("幅 (mm):", self.w_spin); r_form.addRow("高さ (mm):", self.h_spin)
        r_tab.setLayout(r_form); tabs.addTab(r_tab, "四角形")

        c_tab, c_form = QWidget(), QFormLayout()
        self.cr_spin = QDoubleSpinBox(); self.cr_spin.setRange(1, 20000); self.cr_spin.setValue(canvas.preset_circle_r)
        c_form.addRow("半径 (mm):", self.cr_spin); c_tab.setLayout(c_form); tabs.addTab(c_tab, "円")

        a_tab, a_form = QWidget(), QFormLayout()
        self.ar_spin = QDoubleSpinBox(); self.ar_spin.setRange(1, 20000); self.ar_spin.setValue(canvas.preset_arc_r)
        self.astart_spin = QDoubleSpinBox(); self.astart_spin.setRange(-360, 360); self.astart_spin.setValue(canvas.preset_arc_start)
        self.aspan_spin = QDoubleSpinBox(); self.aspan_spin.setRange(-360, 360); self.aspan_spin.setValue(canvas.preset_arc_span)
        a_form.addRow("半径 (mm):", self.ar_spin); a_form.addRow("開始角度 (°):", self.astart_spin); a_form.addRow("展開角度 (°):", self.aspan_spin)
        a_tab.setLayout(a_form); tabs.addTab(a_tab, "円弧")

        p_tab, p_form = QWidget(), QFormLayout()
        self.psides_spin = QSpinBox(); self.psides_spin.setRange(3, 32); self.psides_spin.setValue(canvas.preset_poly_sides)
        self.pr_spin = QDoubleSpinBox(); self.pr_spin.setRange(1, 20000); self.pr_spin.setValue(canvas.preset_poly_r)
        self.pangle_spin = QDoubleSpinBox(); self.pangle_spin.setRange(-360, 360); self.pangle_spin.setValue(canvas.preset_poly_angle)
        p_form.addRow("頂点数 (角):", self.psides_spin); p_form.addRow("外接半径 (mm):", self.pr_spin); p_form.addRow("回転角度 (°):", self.pangle_spin)
        p_tab.setLayout(p_form); tabs.addTab(p_tab, "正多角形")

        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons); self.setLayout(layout)

    def save(self):
        self.canvas.preset_rect_w, self.canvas.preset_rect_h = self.w_spin.value(), self.h_spin.value()
        self.canvas.preset_circle_r = self.cr_spin.value()
        self.canvas.preset_arc_r, self.canvas.preset_arc_start, self.canvas.preset_arc_span = self.ar_spin.value(), self.astart_spin.value(), self.aspan_spin.value()
        self.canvas.preset_poly_sides, self.canvas.preset_poly_r, self.canvas.preset_poly_angle = self.psides_spin.value(), self.pr_spin.value(), self.pangle_spin.value()
        self.accept()

class CADTransformDialog(QDialog):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setWindowTitle("編集・変形ツール パラメータ設定")
        self.setFixedSize(320, 300)
        layout, tabs = QVBoxLayout(), QTabWidget()

        off_tab, f1 = QWidget(), QFormLayout()
        self.off_spin = QDoubleSpinBox(); self.off_spin.setRange(0.1, 50000); self.off_spin.setValue(canvas.offset_dist)
        f1.addRow("オフセット距離 (mm):", self.off_spin); off_tab.setLayout(f1); tabs.addTab(off_tab, "平行線")

        trans_tab, f2 = QWidget(), QFormLayout()
        self.rot_spin = QDoubleSpinBox(); self.rot_spin.setRange(-360, 360); self.rot_spin.setValue(canvas.rotate_angle)
        self.scale_spin = QDoubleSpinBox(); self.scale_spin.setRange(0.01, 100); self.scale_spin.setValue(canvas.scale_factor_val)
        f2.addRow("回転角度 (°):", self.rot_spin); f2.addRow("拡大縮小倍率 (倍):", self.scale_spin)
        trans_tab.setLayout(f2); tabs.addTab(trans_tab, "回転・縮小")

        arr_tab, f3 = QWidget(), QFormLayout()
        self.rows_spin = QSpinBox(); self.rows_spin.setRange(1, 100); self.rows_spin.setValue(canvas.array_rows)
        self.cols_spin = QSpinBox(); self.cols_spin.setRange(1, 100); self.cols_spin.setValue(canvas.array_cols)
        self.rgap_spin = QDoubleSpinBox(); self.rgap_spin.setRange(1, 50000); self.rgap_spin.setValue(canvas.array_row_gap)
        self.cgap_spin = QDoubleSpinBox(); self.cgap_spin.setRange(1, 50000); self.cgap_spin.setValue(canvas.array_col_gap)
        f3.addRow("行数 (Y方向):", self.rows_spin); f3.addRow("列数 (X方向):", self.cols_spin)
        f3.addRow("行間隔 (mm):", self.rgap_spin); f3.addRow("列間隔 (mm):", self.cgap_spin)
        arr_tab.setLayout(f3); tabs.addTab(arr_tab, "配列複写")

        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons); self.setLayout(layout)

    def save(self):
        self.canvas.offset_dist = self.off_spin.value()
        self.canvas.rotate_angle, self.canvas.scale_factor_val = self.rot_spin.value(), self.scale_spin.value()
        self.canvas.array_rows, self.canvas.array_cols = self.rows_spin.value(), self.cols_spin.value()
        self.canvas.array_row_gap, self.canvas.array_col_gap = self.rgap_spin.value(), self.cgap_spin.value()
        self.accept()

class HatchSettingsDialog(QDialog):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas, self.current_color = canvas, canvas.hatch_color
        self.setWindowTitle("ハッチング詳細設定")
        self.setFixedSize(280, 240)
        layout = QFormLayout()
        self.angle_spin = QDoubleSpinBox(); self.angle_spin.setRange(0, 360); self.angle_spin.setValue(canvas.hatch_angle)
        self.spacing_spin = QDoubleSpinBox(); self.spacing_spin.setRange(2, 500); self.spacing_spin.setValue(canvas.hatch_spacing)
        self.thick_spin = QSpinBox(); self.thick_spin.setRange(1, 10); self.thick_spin.setValue(canvas.hatch_thickness)
        self.style_combo = QComboBox()
        self.style_combo.addItem("実線", Qt.PenStyle.SolidLine)
        self.style_combo.addItem("破線", Qt.PenStyle.DashLine)
        self.style_combo.addItem("点線", Qt.PenStyle.DotLine)
        self.color_btn = QPushButton("色選択"); self.color_btn.clicked.connect(self.choose_color)

        layout.addRow("線の角度 (°):", self.angle_spin); layout.addRow("線同士の間隔 (mm):", self.spacing_spin)
        layout.addRow("線の太さ (mm):", self.thick_spin); layout.addRow("線種:", self.style_combo); layout.addRow("描画色:", self.color_btn)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject)
        layout.addRow(buttons); self.setLayout(layout)

    def choose_color(self):
        c = QColorDialog.getColor(self.current_color, self, "ハッチング色")
        if c.isValid(): self.current_color = c

    def save(self):
        self.canvas.hatch_angle, self.canvas.hatch_spacing = self.angle_spin.value(), self.spacing_spin.value()
        self.canvas.hatch_thickness, self.canvas.hatch_style = self.thick_spin.value(), self.style_combo.itemData(self.style_combo.currentIndex())
        self.canvas.hatch_color = self.current_color; self.accept()

class TableSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("表作成・データ連携")
        self.setGeometry(150, 150, 550, 400)
        main_layout = QVBoxLayout()

        ctrl_layout = QHBoxLayout()
        ctrl_layout.addWidget(QLabel("行数:"))
        self.rows_spin = QSpinBox(); self.rows_spin.setValue(4); self.rows_spin.valueChanged.connect(self.update_grid)
        ctrl_layout.addWidget(self.rows_spin)
        ctrl_layout.addWidget(QLabel("列数:"))
        self.cols_spin = QSpinBox(); self.cols_spin.setValue(3); self.cols_spin.valueChanged.connect(self.update_grid)
        ctrl_layout.addWidget(self.cols_spin)
        ctrl_layout.addWidget(QLabel("セル幅:"))
        self.cw_spin = QSpinBox(); self.cw_spin.setRange(20, 500); self.cw_spin.setValue(80)
        ctrl_layout.addWidget(self.cw_spin)
        ctrl_layout.addWidget(QLabel("セル高:"))
        self.ch_spin = QSpinBox(); self.ch_spin.setRange(10, 200); self.ch_spin.setValue(30)
        ctrl_layout.addWidget(self.ch_spin)
        main_layout.addLayout(ctrl_layout)

        self.table_widget = QTableWidget(4, 3); main_layout.addWidget(self.table_widget)

        file_layout = QHBoxLayout()
        imp_btn = QPushButton("📁 CSVインポート"); imp_btn.clicked.connect(self.import_csv)
        exp_btn = QPushButton("💾 CSVエクスポート"); exp_btn.clicked.connect(self.export_csv)
        file_layout.addWidget(imp_btn); file_layout.addWidget(exp_btn)
        main_layout.addLayout(file_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons); self.setLayout(main_layout)

    def update_grid(self):
        self.table_widget.setRowCount(self.rows_spin.value())
        self.table_widget.setColumnCount(self.cols_spin.value())

    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "CSVを開く", "", "CSV Files (*.csv);;Text Files (*.txt)")
        if path:
            with open(path, 'r', encoding='utf-8-sig') as f:
                reader = list(csv.reader(f))
                if reader:
                    self.rows_spin.setValue(len(reader)); self.cols_spin.setValue(max(len(r) for r in reader))
                    for r, row in enumerate(reader):
                        for c, val in enumerate(row): self.table_widget.setItem(r, c, QTableWidgetItem(val))

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "CSVで保存", "", "CSV Files (*.csv)")
        if path:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                csv.writer(f).writerows(self.get_table_data())
            QMessageBox.information(self, "完了", "CSVファイルを出力しました。")

    def get_table_data(self):
        return [[self.table_widget.item(r, c).text() if self.table_widget.item(r, c) else "" for c in range(self.table_widget.columnCount())] for r in range(self.table_widget.rowCount())]

class PdfExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PDF出力設定"); self.setFixedSize(250, 150)
        layout = QFormLayout()
        self.size_combo = QComboBox()
        self.size_combo.addItem("A4", QPageSize.PageSizeId.A4); self.size_combo.addItem("A3", QPageSize.PageSizeId.A3)
        self.size_combo.addItem("A2", QPageSize.PageSizeId.A2); self.size_combo.addItem("B4", QPageSize.PageSizeId.B4)
        self.size_combo.addItem("B5", QPageSize.PageSizeId.B5)
        self.ori_combo = QComboBox()
        self.ori_combo.addItem("横 (Landscape)", QPageLayout.Orientation.Landscape)
        self.ori_combo.addItem("縦 (Portrait)", QPageLayout.Orientation.Portrait)
        layout.addRow("用紙サイズ:", self.size_combo); layout.addRow("向き:", self.ori_combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons); self.setLayout(layout)

    def get_settings(self): return self.size_combo.currentData(), self.ori_combo.currentData()

class PaperGuideDialog(QDialog):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setWindowTitle("用紙枠・印刷縮尺設定")
        self.setFixedSize(300, 260)
        layout = QFormLayout()

        self.show_chk = QCheckBox("用紙枠を表示する")
        self.show_chk.setChecked(canvas.show_paper_guide)

        self.size_combo = QComboBox()
        self.size_combo.addItem("A4", QPageSize.PageSizeId.A4); self.size_combo.addItem("A3", QPageSize.PageSizeId.A3)
        self.size_combo.addItem("A2", QPageSize.PageSizeId.A2); self.size_combo.addItem("B4", QPageSize.PageSizeId.B4)
        self.size_combo.addItem("B5", QPageSize.PageSizeId.B5)
        idx = self.size_combo.findData(canvas.paper_size_id)
        if idx >= 0: self.size_combo.setCurrentIndex(idx)

        self.ori_combo = QComboBox()
        self.ori_combo.addItem("横 (Landscape)", QPageLayout.Orientation.Landscape)
        self.ori_combo.addItem("縦 (Portrait)", QPageLayout.Orientation.Portrait)
        idx_ori = self.ori_combo.findData(canvas.paper_orientation)
        if idx_ori >= 0: self.ori_combo.setCurrentIndex(idx_ori)

        self.scale_combo = QComboBox()
        scales = [
            ("1 : 1 (原寸)", 1), ("1 : 5 (詳細納まり)", 5), ("1 : 10 (詳細図)", 10),
            ("1 : 20 (詳細図/展開図)", 20), ("1 : 30", 30), ("1 : 50 (平面詳細図)", 50),
            ("1 : 100 (基本平面/立面図)", 100), ("1 : 200 (配置図/全体図)", 200), ("1 : 500 (広域図)", 500)
        ]
        for label, val in scales: self.scale_combo.addItem(label, val)
        idx_scale = self.scale_combo.findData(canvas.paper_scale)
        if idx_scale >= 0: self.scale_combo.setCurrentIndex(idx_scale)

        fit_btn = QPushButton("🎯 選択中の画像/PDF枠に合わせる")
        fit_btn.clicked.connect(self.fit_to_image)

        layout.addRow(self.show_chk)
        layout.addRow("用紙サイズ:", self.size_combo)
        layout.addRow("向き:", self.ori_combo)
        layout.addRow("出力縮尺:", self.scale_combo)
        layout.addRow(fit_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons); self.setLayout(layout)

    def fit_to_image(self):
        if self.canvas.fit_paper_guide_to_selected():
            self.show_chk.setChecked(True)
            QMessageBox.information(self, "完了", "画像/PDFの範囲に用紙枠を合わせました。")
        else:
            QMessageBox.warning(self, "通知", "対象の画像やPDFが選択・配置されていません。")

class CoordinateInputDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("座標指定入力"); self.setFixedSize(280, 180)
        layout = QFormLayout()

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("絶対座標 (X, Y)", "absolute")
        self.mode_combo.addItem("相対座標 (ΔX, ΔY)", "relative")
        
        self.x_spin = QDoubleSpinBox(); self.x_spin.setRange(-999999.0, 999999.0); self.x_spin.setDecimals(2)
        self.y_spin = QDoubleSpinBox(); self.y_spin.setRange(-999999.0, 999999.0); self.y_spin.setDecimals(2)

        layout.addRow("指定方式:", self.mode_combo)
        layout.addRow("X 座標 (mm):", self.x_spin)
        layout.addRow("Y 座標 (mm):", self.y_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons); self.setLayout(layout)

    def get_values(self): return self.x_spin.value(), self.y_spin.value(), (self.mode_combo.currentData() == "relative")

class PitchArrayDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("一定間隔 連続配置"); self.setFixedSize(280, 200)
        layout = QFormLayout()

        self.count_spin = QSpinBox(); self.count_spin.setRange(1, 100); self.count_spin.setValue(5)
        self.dx_spin = QDoubleSpinBox(); self.dx_spin.setRange(-9999.0, 9999.0); self.dx_spin.setValue(50.0)
        self.dy_spin = QDoubleSpinBox(); self.dy_spin.setRange(-9999.0, 9999.0); self.dy_spin.setValue(0.0)

        layout.addRow("配置個数:", self.count_spin)
        layout.addRow("Xピッチ (ΔX):", self.dx_spin)
        layout.addRow("Yピッチ (ΔY):", self.dy_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons); self.setLayout(layout)

    def get_values(self): return self.count_spin.value(), self.dx_spin.value(), self.dy_spin.value()

class ConcentricShapesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("同心・多層図形 一括生成"); self.setFixedSize(300, 260)
        layout = QFormLayout()

        self.type_combo = QComboBox()
        self.type_combo.addItem("円 (Concentric Circle)", "CIRCLE")
        self.type_combo.addItem("正方形/四角 (Concentric Rect)", "RECT")
        self.type_combo.addItem("正多角形 (Concentric Polygon)", "POLYGON")

        self.sides_spin = QSpinBox(); self.sides_spin.setRange(3, 32); self.sides_spin.setValue(6)
        self.base_spin = QDoubleSpinBox(); self.base_spin.setRange(1.0, 5000.0); self.base_spin.setValue(20.0)
        self.step_mode_combo = QComboBox()
        self.step_mode_combo.addItem("等間隔 加算 (+mm)", "add")
        self.step_mode_combo.addItem("倍率 拡大 (×倍)", "multiply")

        self.step_spin = QDoubleSpinBox(); self.step_spin.setRange(0.1, 1000.0); self.step_spin.setValue(20.0)
        self.count_spin = QSpinBox(); self.count_spin.setRange(1, 50); self.count_spin.setValue(4)

        layout.addRow("図形種別:", self.type_combo)
        layout.addRow("角の数 (多角形時):", self.sides_spin)
        layout.addRow("初期半径/サイズ:", self.base_spin)
        layout.addRow("変化方式:", self.step_mode_combo)
        layout.addRow("ピッチ/倍率値:", self.step_spin)
        layout.addRow("生成数:", self.count_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons); self.setLayout(layout)

    def get_values(self): return self.type_combo.currentData(), self.base_spin.value(), self.step_spin.value(), (self.step_mode_combo.currentData() == "multiply"), self.count_spin.value(), self.sides_spin.value()

class LayerCountDialog(QDialog):
    def __init__(self, counts_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("レイヤー別 オブジェクト集計")
        self.setGeometry(150, 150, 700, 380)
        layout = QVBoxLayout()
        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["レイヤー名", "合計数", "基本図形", "ブロック数", "グループ数", "ブロック内訳 (名前: 個数)"])
        table.setRowCount(len(counts_data))
        for row, (lyr_name, data) in enumerate(counts_data.items()):
            table.setItem(row, 0, QTableWidgetItem(lyr_name))
            table.setItem(row, 1, QTableWidgetItem(str(data["total"])))
            table.setItem(row, 2, QTableWidgetItem(str(data["basic"])))
            table.setItem(row, 3, QTableWidgetItem(str(data["block_ref"])))
            table.setItem(row, 4, QTableWidgetItem(str(data["group"])))
            details = [f"{name}×{cnt}" for name, cnt in data["blocks_detail"].items()]
            table.setItem(row, 5, QTableWidgetItem(", ".join(details) if details else "-"))
        table.resizeColumnsToContents()
        layout.addWidget(table)
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)
        self.setLayout(layout)

class FilletChamferDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("フィレット / 面取り設定")
        self.setFixedSize(280, 150)
        layout = QFormLayout()
        self.radius_spin = QDoubleSpinBox(); self.radius_spin.setRange(0, 10000); self.radius_spin.setValue(50.0)
        self.chamfer_spin = QDoubleSpinBox(); self.chamfer_spin.setRange(0, 10000); self.chamfer_spin.setValue(50.0)
        layout.addRow("フィレット半径 (R):", self.radius_spin)
        layout.addRow("面取り距離 (C):", self.chamfer_spin)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons); self.setLayout(layout)

class LayerManagerDialog(QDialog):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setWindowTitle("レイヤープロパティ管理")
        self.setGeometry(100, 100, 750, 350)
        layout = QVBoxLayout()

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["現在", "レイヤー名", "表示 💡", "ロック 🔒", "印刷 🖨️", "色", "太さ", "線種"])
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("➕ 新規レイヤー"); add_btn.clicked.connect(self.add_layer)
        del_btn = QPushButton("🗑 削除"); del_btn.clicked.connect(self.delete_layer)
        btn_layout.addWidget(add_btn); btn_layout.addWidget(del_btn); btn_layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_and_apply); buttons.rejected.connect(self.reject)
        btn_layout.addWidget(buttons); layout.addLayout(btn_layout); self.setLayout(layout)
        self.load_layers()

    def load_layers(self):
        self.table.setRowCount(0)
        for row, (name, props) in enumerate(self.canvas.layers.items()):
            self.table.insertRow(row)
            active_lbl = "★" if name == self.canvas.active_layer else ""
            self.table.setItem(row, 0, QTableWidgetItem(active_lbl))
            name_item = QTableWidgetItem(name)
            if name == "0": name_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 1, name_item)
            for col_idx, key in enumerate(["visible", "locked", "printable"], start=2):
                chk = QCheckBox(); chk.setChecked(props[key])
                cell_widget = QWidget(); l = QHBoxLayout(cell_widget); l.addWidget(chk); l.setAlignment(Qt.AlignmentFlag.AlignCenter); l.setContentsMargins(0,0,0,0)
                self.table.setCellWidget(row, col_idx, cell_widget)
            color_btn = QPushButton(); color_btn.setStyleSheet(f"background-color: {props['color'].name()}; border: 1px solid #777;")
            color_btn.clicked.connect(lambda _, r=row: self.pick_color(r))
            self.table.setCellWidget(row, 5, color_btn)
            thick_spin = QSpinBox(); thick_spin.setRange(1, 20); thick_spin.setValue(props["thickness"]); self.table.setCellWidget(row, 6, thick_spin)
            style_combo = QComboBox(); style_combo.addItem("実線", Qt.PenStyle.SolidLine); style_combo.addItem("破線", Qt.PenStyle.DashLine); style_combo.addItem("点線", Qt.PenStyle.DotLine)
            idx_s = style_combo.findData(props.get("style", Qt.PenStyle.SolidLine))
            if idx_s >= 0: style_combo.setCurrentIndex(idx_s)
            self.table.setCellWidget(row, 7, style_combo)

    def pick_color(self, row):
        btn = self.table.cellWidget(row, 5)
        current_color = QColor(btn.styleSheet().split(":")[1].split(";")[0].strip())
        c = QColorDialog.getColor(current_color, self, "レイヤー色選択")
        if c.isValid(): btn.setStyleSheet(f"background-color: {c.name()}; border: 1px solid #777;")

    def add_layer(self):
        name, ok = QInputDialog.getText(self, "新規レイヤー", "レイヤー名を入力してください:")
        if ok and name.strip() and name not in self.canvas.layers:
            self.canvas.layers[name] = {"color": QColor(0,0,0), "thickness": 2, "style": Qt.PenStyle.SolidLine, "visible": True, "locked": False, "printable": True}
            self.load_layers()

    def delete_layer(self):
        row = self.table.currentRow()
        if row < 0: return
        name = self.table.item(row, 1).text()
        if name == "0": QMessageBox.warning(self, "警告", "0レイヤーは削除できません。"); return
        del self.canvas.layers[name]
        if self.canvas.active_layer == name: self.canvas.active_layer = "0"
        self.load_layers()

    def save_and_apply(self):
        new_layers = {}
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 1).text()
            c_name = self.table.cellWidget(row, 5).styleSheet().split(":")[1].split(";")[0].strip()
            visible = self.table.cellWidget(row, 2).layout().itemAt(0).widget().isChecked()
            locked = self.table.cellWidget(row, 3).layout().itemAt(0).widget().isChecked()
            printable = self.table.cellWidget(row, 4).layout().itemAt(0).widget().isChecked()
            thickness = self.table.cellWidget(row, 6).value()
            style = self.table.cellWidget(row, 7).currentData()
            new_layers[name] = {"color": QColor(c_name), "thickness": thickness, "style": style, "visible": visible, "locked": locked, "printable": printable}
        self.canvas.layers = new_layers
        self.canvas.apply_layer_states()
        self.accept()

class CreateBlockDialog(QDialog):
    def __init__(self, existing_categories, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ブロック定義 (BLOCK)"); self.setFixedSize(320, 160)
        layout = QFormLayout()
        self.name_input = QLineEdit(); self.name_input.setPlaceholderText("例: 特注カウンター_1500")

        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        for cat in existing_categories: self.category_combo.addItem(cat)
        if "カスタム" not in existing_categories: self.category_combo.addItem("カスタム")

        layout.addRow("ブロック名:", self.name_input)
        layout.addRow("分類カテゴリ:", self.category_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons); self.setLayout(layout)

    def get_data(self): return self.name_input.text().strip(), self.category_combo.currentText().strip()

class BlockLibraryDialog(QDialog):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setWindowTitle("2D CAD ブロックライブラリ")
        self.setGeometry(120, 120, 600, 450)

        main_layout = QVBoxLayout()
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("🔍 検索:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("パーツ名で検索 (例: ドア, コンセント)...")
        self.search_input.textChanged.connect(self.filter_blocks)
        top_layout.addWidget(self.search_input)

        top_layout.addWidget(QLabel("分類:"))
        self.cat_combo = QComboBox()
        self.cat_combo.currentIndexChanged.connect(self.filter_blocks)
        top_layout.addWidget(self.cat_combo)
        main_layout.addLayout(top_layout)

        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(32, 32))
        self.list_widget.itemDoubleClicked.connect(self.insert_selected)
        main_layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        insert_btn = QPushButton("📥 選択したブロックを挿入")
        insert_btn.clicked.connect(self.insert_selected)
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)
        
        btn_layout.addWidget(insert_btn)
        btn_layout.addWidget(close_btn)
        main_layout.addLayout(btn_layout)

        self.setLayout(main_layout)
        self.init_categories()
        self.filter_blocks()

    def init_categories(self):
        categories = sorted(list(set(b.get("category", "カスタム") for b in self.canvas.blocks.values())))
        self.cat_combo.addItem("すべてのカテゴリ")
        for cat in categories:
            self.cat_combo.addItem(cat)

    def filter_blocks(self):
        self.list_widget.clear()
        query = self.search_input.text().strip().lower()
        selected_cat = self.cat_combo.currentText()

        for name, bdata in self.canvas.blocks.items():
            cat = bdata.get("category", "カスタム")
            if (selected_cat == "すべてのカテゴリ" or selected_cat == cat):
                if not query or query in name.lower():
                    item = QListWidgetItem(f"[{cat}]  {name}")
                    item.setData(Qt.ItemDataRole.UserRole, name)
                    self.list_widget.addItem(item)

    def insert_selected(self):
        current_item = self.list_widget.currentItem()
        if not current_item:
            QMessageBox.warning(self, "通知", "挿入するブロックを選択してください。")
            return
        
        block_name = current_item.data(Qt.ItemDataRole.UserRole)
        if block_name:
            center_pos = self.canvas.mapToScene(self.canvas.viewport().rect().center())
            self.canvas.insert_block_ref(block_name, center_pos)
            self.accept()

# =========================================================
# メインウィンドウ
# =========================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("建築・施工用 朱書きCADシステム Pro")
        self.setGeometry(50, 50, 1600, 850)
        
        self.canvas = CADCanvas()
        self.canvas.mode_changed.connect(self.on_canvas_mode_changed)
        self.canvas.scene.selectionChanged.connect(self.on_selection_changed)
        self.setCentralWidget(self.canvas)
        
        # --- メニューバー構築 ---
        menubar = self.menuBar()
        
        file_menu = menubar.addMenu("ファイル(&F)")
        open_json_act = QAction("📂 プロジェクトを開く (.json)", self); open_json_act.setShortcut(QKeySequence("Ctrl+O")); open_json_act.triggered.connect(self.canvas.load_project_json); file_menu.addAction(open_json_act)
        save_json_act = QAction("💾 プロジェクトを保存 (.json)", self); save_json_act.setShortcut(QKeySequence("Ctrl+S")); save_json_act.triggered.connect(self.canvas.save_project_json); file_menu.addAction(save_json_act)
        file_menu.addSeparator()
        jww_act = QAction("📐 Jw_cadファイルを開く (.jww)", self); jww_act.triggered.connect(self.open_jww_file); file_menu.addAction(jww_act)
        dxf_act = QAction("DXF形式で保存", self); dxf_act.setShortcut(QKeySequence("Ctrl+Shift+S")); dxf_act.triggered.connect(self.canvas.save_to_dxf); file_menu.addAction(dxf_act)
        file_menu.addSeparator()
        bg_act = QAction("背景としてPDF/画像を読み込み", self); bg_act.triggered.connect(self.open_background); file_menu.addAction(bg_act)
        img_act = QAction("🖼 画像/PDFを挿入", self); img_act.triggered.connect(self.open_insert_dialog); file_menu.addAction(img_act)
        file_menu.addSeparator()
        pdf_act = QAction("📄 PDF出力", self); pdf_act.triggered.connect(self.execute_pdf_export); file_menu.addAction(pdf_act)
        print_act = QAction("🖨 印刷", self); print_act.setShortcut(QKeySequence.StandardKey.Print); print_act.triggered.connect(self.canvas.print_scene); file_menu.addAction(print_act)

        edit_menu = menubar.addMenu("編集(&E)")
        undo_act = QAction("↶ 元に戻す", self); undo_act.setShortcut(QKeySequence.StandardKey.Undo); undo_act.triggered.connect(self.canvas.undo); edit_menu.addAction(undo_act)
        redo_act = QAction("↷ やり直し", self); redo_act.setShortcut(QKeySequence.StandardKey.Redo); redo_act.triggered.connect(self.canvas.redo); edit_menu.addAction(redo_act)
        edit_menu.addSeparator()
        coord_act = QAction("📍 座標指定入力", self); coord_act.setShortcut(QKeySequence("F2")); coord_act.triggered.connect(self.open_coordinate_input); edit_menu.addAction(coord_act)
        edit_menu.addSeparator()
        group_act = QAction("🔗 グループ化", self); group_act.setShortcut(QKeySequence("Ctrl+G")); group_act.triggered.connect(self.canvas.group_selected_items); edit_menu.addAction(group_act)
        ungroup_act = QAction("💥 グループ分割", self); ungroup_act.setShortcut(QKeySequence("Ctrl+Shift+G")); ungroup_act.triggered.connect(self.canvas.ungroup_selected_items); edit_menu.addAction(ungroup_act)
        edit_menu.addSeparator()
        count_act = QAction("📊 レイヤー別オブジェクト集計", self); count_act.triggered.connect(self.open_layer_count_dialog); edit_menu.addAction(count_act)

        setting_menu = menubar.addMenu("ツール・設定(&T)")
        area_act = QAction("📐 出力範囲の指定(詳細図)", self); area_act.triggered.connect(lambda: self.canvas.set_mode("PRINT_AREA")); setting_menu.addAction(area_act)
        clear_area_act = QAction("❌ 出力範囲の解除", self); clear_area_act.triggered.connect(self.canvas.clear_custom_print_rect); setting_menu.addAction(clear_area_act)
        setting_menu.addSeparator()
        pitch_act = QAction("🔁 連続ピッチ生成", self); pitch_act.triggered.connect(self.open_pitch_dialog); setting_menu.addAction(pitch_act)
        conc_act = QAction("🎯 同心・多層図形生成", self); conc_act.triggered.connect(self.open_concentric_dialog); setting_menu.addAction(conc_act)
        tbl_act = QAction("📊 表・CSV連携", self); tbl_act.triggered.connect(self.open_table_dialog); setting_menu.addAction(tbl_act)
        setting_menu.addSeparator()
        c_act = QAction("⚙ 雲マーク設定", self); c_act.triggered.connect(self.open_cloud_settings); setting_menu.addAction(c_act)
        p_act = QAction("📏 数値指定作図設定", self); p_act.triggered.connect(self.open_preset_settings); setting_menu.addAction(p_act)
        t_act = QAction("🛠 変形ツール設定", self); t_act.triggered.connect(self.open_transform_settings); setting_menu.addAction(t_act)
        h_act = QAction("🎨 ハッチング設定", self); h_act.triggered.connect(self.open_hatch_settings); setting_menu.addAction(h_act)
        f_act = QAction("✂️ フィレット・面取り設定", self); f_act.triggered.connect(self.open_fillet_dialog); setting_menu.addAction(f_act)
        guide_act = QAction("📄 用紙枠・印刷縮尺設定", self); guide_act.triggered.connect(self.open_paper_guide_settings); setting_menu.addAction(guide_act)

        # --- ツールバー 第1段 ---
        toolbar1 = QToolBar("作図ツールバー")
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar1)
        toolbar1.addAction(undo_act); toolbar1.addAction(redo_act); toolbar1.addSeparator()

        toolbar1.addWidget(QLabel(" ツール: "))
        self.tool_combo = QComboBox()
        tools = [
            ("【 選択・編集ツール 】", None),
            ("選択・移動・削除", "SELECT"),
            ("基点移動 (MOVE)", "MOVE"),
            ("基点複写 (COPY)", "COPY"),
            ("トリム / 延長", "TRIM"),
            ("フィレット / 面取り", "FILLET"),
            ("分割・部分削除", "BREAK"),
            ("回転", "ROTATE"),
            ("拡大縮小", "SCALE"),
            ("ミラー", "MIRROR"),
            ("平行線", "OFFSET"),
            ("配列複写", "ARRAY"),
            ("ハッチング", "HATCH"),
            
            ("【 基本作図ツール 】", None),
            ("直線", "LINE"), 
            ("水平線", "H_LINE"), 
            ("垂直線", "V_LINE"),
            ("連続線", "POLYLINE"), 
            ("スプライン (自由曲線)", "SPLINE"), 
            ("四角形", "RECT"), 
            ("多角形", "POLYGON"), 
            ("正多角形", "REG_POLYGON"),
            ("円 (中心・半径)", "CIRCLE"), 
            ("2点指定円", "CIRCLE_2P"), 
            ("3点指定円", "CIRCLE_3P"),
            ("円弧 (中心・始・終)", "ARC"), 
            ("3点指定円弧", "ARC_3P"), 
            ("楕円", "ELLIPSE"),
            ("点", "POINT"),

            ("【 注釈・寸法・自動計測 】", None),
            ("引き出し線 (長/面積自動計測)", "LEADER"),
            ("角度寸法 (ANGULAR)", "DIM_ANGLE"),
            ("半径・直径寸法 (R/φ)", "DIM_RADIUS"),
            ("寸法線", "DIMENSION"), 
            ("矢印注釈", "ARROW"),
            ("文章入力", "TEXT"),
            ("雲マーク", "CLOUD"),
            ("選択図形を雲マーク化", "CLOUD_OBJECT"),

            ("【 トレース・下絵連携 】", None),
            ("指定範囲の直線を抽出 (自動化)", "AUTO_TRACE"),
            ("2点間で縮尺合わせ", "TRACE_CALIBRATE"),

            ("【 高度・一括作図 】", None),
            ("固定寸法: 四角形", "PRESET_RECT"),
            ("固定寸法: 円", "PRESET_CIRCLE"),
            ("固定寸法: 円弧", "PRESET_ARC"),
            ("固定寸法: 正多角形", "PRESET_POLYGON"),
            ("同心: 円", "CONCENTRIC_CIRCLE"),
            ("同心: 四角", "CONCENTRIC_RECT"),
            ("同心: 多角形", "CONCENTRIC_POLYGON"),
        ]

        for row, (name, mode) in enumerate(tools):
            self.tool_combo.addItem(name, mode)
            if mode is None:
                item = self.tool_combo.model().item(row)
                if item: item.setEnabled(False); font = item.font(); font.setBold(True); item.setFont(font)

        self.tool_combo.setCurrentIndex(1)
        self.tool_combo.currentIndexChanged.connect(self.on_tool_combo_changed)
        toolbar1.addWidget(self.tool_combo)
        toolbar1.addSeparator()

        toolbar1.addWidget(QLabel(" レイヤー: "))
        self.layer_combo = QComboBox()
        self.refresh_layer_combo()
        self.layer_combo.currentIndexChanged.connect(self.on_layer_changed)
        toolbar1.addWidget(self.layer_combo)

        layer_mgr_btn = QPushButton("☰ レイヤー設定")
        layer_mgr_btn.clicked.connect(self.open_layer_manager)
        toolbar1.addWidget(layer_mgr_btn)
        toolbar1.addSeparator()

        blk_insert_btn = QPushButton("📚 2Dブロックライブラリ")
        blk_insert_btn.clicked.connect(self.open_block_library_dialog)
        toolbar1.addWidget(blk_insert_btn)

        blk_create_btn = QPushButton("📦 作成")
        blk_create_btn.clicked.connect(self.open_create_block_dialog)
        toolbar1.addWidget(blk_create_btn)

        explode_btn = QPushButton("💥 分解")
        explode_btn.clicked.connect(self.canvas.explode_selected_block)
        toolbar1.addWidget(explode_btn)

        toolbar1.addWidget(QLabel(" | "))
        grp_btn = QPushButton("🔗 グループ化")
        grp_btn.clicked.connect(self.canvas.group_selected_items)
        toolbar1.addWidget(grp_btn)

        cnt_btn = QPushButton("📊 要素集計")
        cnt_btn.clicked.connect(self.open_layer_count_dialog)
        toolbar1.addWidget(cnt_btn)

        # --- ツールバー 第2段 ---
        self.addToolBarBreak()
        toolbar2 = QToolBar("プロパティ・スナップツールバー")
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar2)

        self.grid_chk = QCheckBox("グリッド吸着 ")
        self.grid_chk.toggled.connect(lambda chk: self.canvas.set_grid_snap(chk, self.grid_size_spin.value()))
        toolbar2.addWidget(self.grid_chk)

        self.grid_size_spin = QSpinBox()
        self.grid_size_spin.setRange(1, 5000); self.grid_size_spin.setValue(50); self.grid_size_spin.setSuffix(" mm")
        self.grid_size_spin.valueChanged.connect(lambda val: self.canvas.set_grid_snap(self.grid_chk.isChecked(), val))
        toolbar2.addWidget(self.grid_size_spin)

        self.angle_chk = QCheckBox(" 15°固定 ")
        self.angle_chk.setChecked(True); self.angle_chk.toggled.connect(self.canvas.set_angle_snap)
        toolbar2.addWidget(self.angle_chk)

        self.otrack_chk = QCheckBox(" OTRACK (F11) ")
        self.otrack_chk.setChecked(True)
        self.otrack_chk.toggled.connect(self.canvas.set_otrack_enabled)
        toolbar2.addWidget(self.otrack_chk)

        otrack_act = QAction("トラッキング切替", self)
        otrack_act.setShortcut(QKeySequence("F11"))
        otrack_act.triggered.connect(lambda: self.otrack_chk.setChecked(not self.otrack_chk.isChecked()))
        self.addAction(otrack_act)

        toolbar2.addSeparator()

        toolbar2.addWidget(QLabel(" 色: "))
        self.color_btn = QPushButton(); self.color_btn.setFixedSize(20, 20)
        self.update_color_button_style(self.canvas.current_color)
        self.color_btn.clicked.connect(self.choose_color); toolbar2.addWidget(self.color_btn)

        toolbar2.addWidget(QLabel(" 太さ: "))
        self.thickness_combo = QComboBox()
        for t in [1, 2, 3, 5, 8, 10]: self.thickness_combo.addItem(f"{t} mm", t)
        self.thickness_combo.setCurrentIndex(1)
        self.thickness_combo.currentIndexChanged.connect(lambda idx: self.canvas.set_thickness(self.thickness_combo.itemData(idx)))
        toolbar2.addWidget(self.thickness_combo)
        
        toolbar2.addWidget(QLabel(" 線種: "))
        self.style_combo = QComboBox()
        self.style_combo.addItem("実線", Qt.PenStyle.SolidLine)
        self.style_combo.addItem("破線", Qt.PenStyle.DashLine)
        self.style_combo.addItem("点線", Qt.PenStyle.DotLine)
        self.style_combo.currentIndexChanged.connect(lambda idx: self.canvas.set_style(self.style_combo.itemData(idx)))
        toolbar2.addWidget(self.style_combo)
        toolbar2.addSeparator()

        toolbar2.addWidget(QLabel(" 背景: "))
        self.bg_combo = QComboBox()
        self.bg_combo.addItem("白 (紙)", "WHITE")
        self.bg_combo.addItem("黒 (CAD)", "DARK")
        self.bg_combo.addItem("グレー", "GRAY")
        self.bg_combo.currentIndexChanged.connect(lambda idx: self.canvas.set_canvas_bg_color(self.bg_combo.itemData(idx)))
        toolbar2.addWidget(self.bg_combo)

        toolbar2.addSeparator()
        zi = QPushButton("🔍＋"); zi.clicked.connect(self.canvas.zoom_in); toolbar2.addWidget(zi)
        zo = QPushButton("🔍ー"); zo.clicked.connect(self.canvas.zoom_out); toolbar2.addWidget(zo)
        zf = QPushButton("🔲 全体表示"); zf.clicked.connect(self.canvas.zoom_fit); toolbar2.addWidget(zf)

        self.sync_toolbar_with_active_layer()

    # --- 各種オープン・ハンドラーメソッド ---
    def on_tool_combo_changed(self, idx):
        mode = self.tool_combo.itemData(idx)
        if mode is not None: self.canvas.set_mode(mode)

    def sync_toolbar_with_active_layer(self):
        props = self.canvas.layers.get(self.canvas.active_layer, self.canvas.layers["0"])
        self.update_color_button_style(props["color"])
        idx_t = self.thickness_combo.findData(props["thickness"])
        if idx_t >= 0: self.thickness_combo.blockSignals(True); self.thickness_combo.setCurrentIndex(idx_t); self.thickness_combo.blockSignals(False)
        idx_s = self.style_combo.findData(props["style"])
        if idx_s >= 0: self.style_combo.blockSignals(True); self.style_combo.setCurrentIndex(idx_s); self.style_combo.blockSignals(False)

    def open_jww_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Jw_cadファイルを開く", "", "Jw_cad Files (*.jww *.jws)")
        if path: self.canvas.import_jww_file(path)

    def open_background(self):
        path, _ = QFileDialog.getOpenFileName(self, "PDF/画像を開く", "", "図面・画像 (*.pdf *.png *.jpg *.jpeg)")
        if path: self.canvas.set_background_file(path)

    def open_insert_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "挿入するPDF/画像を選択", "", "図面・画像 (*.pdf *.png *.jpg *.jpeg *.bmp)")
        if path: self.canvas.insert_image_or_pdf(path)

    def choose_color(self):
        c = QColorDialog.getColor(self.canvas.current_color, self, "色選択")
        if c.isValid(): self.canvas.set_color(c); self.update_color_button_style(c)

    def update_color_button_style(self, color):
        self.color_btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #555;")

    def on_canvas_mode_changed(self, mode):
        idx = self.tool_combo.findData(mode)
        if idx >= 0 and self.tool_combo.currentIndex() != idx:
            self.tool_combo.blockSignals(True); self.tool_combo.setCurrentIndex(idx); self.tool_combo.blockSignals(False)

    def on_selection_changed(self):
        selected = self.canvas.scene.selectedItems()
        if len(selected) == 1:
            item = selected[0]
            if hasattr(item, "pen"):
                pen = item.pen()
                self.canvas.current_color = pen.color(); self.update_color_button_style(pen.color())
                idx_t = self.thickness_combo.findData(pen.width())
                if idx_t >= 0: self.thickness_combo.blockSignals(True); self.thickness_combo.setCurrentIndex(idx_t); self.thickness_combo.blockSignals(False); self.canvas.current_thickness = pen.width()
                idx_s = self.style_combo.findData(pen.style())
                if idx_s >= 0: self.style_combo.blockSignals(True); self.style_combo.setCurrentIndex(idx_s); self.style_combo.blockSignals(False); self.canvas.current_style = pen.style()
            elif isinstance(item, QGraphicsTextItem):
                self.canvas.current_color = item.defaultTextColor(); self.update_color_button_style(item.defaultTextColor())

    def refresh_layer_combo(self):
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        for l_name in self.canvas.layers.keys(): self.layer_combo.addItem(l_name, l_name)
        idx = self.layer_combo.findData(self.canvas.active_layer)
        if idx >= 0: self.layer_combo.setCurrentIndex(idx)
        self.layer_combo.blockSignals(False)

    def on_layer_changed(self, idx):
        l_name = self.layer_combo.itemData(idx)
        if l_name:
            if self.canvas.scene.selectedItems(): self.canvas.change_selected_layer(l_name)
            self.canvas.set_active_layer(l_name)
            self.sync_toolbar_with_active_layer()

    def open_layer_manager(self):
        if LayerManagerDialog(self.canvas, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh_layer_combo(); self.sync_toolbar_with_active_layer()

    def open_block_library_dialog(self):
        d = BlockLibraryDialog(self.canvas, self)
        d.exec()

    def open_create_block_dialog(self):
        if not self.canvas.scene.selectedItems():
            QMessageBox.warning(self, "警告", "ブロック化する要素を図面上で選択してください。")
            return
        existing_cats = sorted(list(set(b.get("category", "カスタム") for b in self.canvas.blocks.values())))
        d = CreateBlockDialog(existing_cats, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            b_name, b_cat = d.get_data()
            if b_name:
                if self.canvas.create_block_from_selected(b_name, category=b_cat):
                    QMessageBox.information(self, "完了", f"カテゴリ『{b_cat}』にブロック '{b_name}' を作成しました。")

    def open_coordinate_input(self):
        dialog = CoordinateInputDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            x, y, is_relative = dialog.get_values()
            self.canvas.process_coordinate_input(x, y, is_relative)

    def open_pitch_dialog(self):
        dialog = PitchArrayDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            count, dx, dy = dialog.get_values()
            self.canvas.generate_pitch_points(count, dx, dy)

    def open_concentric_dialog(self):
        dialog = ConcentricShapesDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            stype, base, step, is_mult, count, sides = dialog.get_values()
            self.canvas.generate_concentric_shapes(stype, base, step, is_mult, count, sides)

    def open_cloud_settings(self):
        d = CloudSettingsDialog(self.canvas.cloud_pitch, self.canvas.cloud_arc_height, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.canvas.set_cloud_pitch(d.pitch_spin.value()); self.canvas.set_cloud_arc_height(d.height_spin.value())

    def open_preset_settings(self): ShapePresetDialog(self.canvas, self).exec()
    def open_transform_settings(self): CADTransformDialog(self.canvas, self).exec()
    def open_hatch_settings(self): HatchSettingsDialog(self.canvas, self).exec()
    
    def open_table_dialog(self):
        d = TableSettingsDialog(self)
        if d.exec() == QDialog.DialogCode.Accepted:
            center_pos = self.canvas.mapToScene(self.canvas.viewport().rect().center())
            self.canvas.add_table_data(d.get_table_data(), d.cw_spin.value(), d.ch_spin.value(), center_pos)

    def open_paper_guide_settings(self):
        d = PaperGuideDialog(self.canvas, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.canvas.update_paper_guide(
                size_id=d.size_combo.currentData(), orientation=d.ori_combo.currentData(),
                scale=d.scale_combo.currentData(), show=d.show_chk.isChecked()
            )

    def execute_pdf_export(self):
        d = PdfExportDialog(self)
        if d.exec() == QDialog.DialogCode.Accepted:
            sz, ori = d.get_settings(); self.canvas.export_to_pdf(sz, ori)

    def open_layer_count_dialog(self):
        LayerCountDialog(self.canvas.count_objects_by_layer(), self).exec()

    def open_fillet_dialog(self):
        d = FilletChamferDialog(self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.canvas.fillet_radius = d.radius_spin.value()
            self.canvas.chamfer_dist = d.chamfer_spin.value()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())