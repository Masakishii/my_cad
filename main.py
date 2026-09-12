import sys, csv, os, json, traceback, urllib.request
from PyQt6.QtWidgets import (QApplication, QMainWindow, QToolBar, QFileDialog, 
                             QPushButton, QComboBox, QColorDialog, QLabel, QCheckBox, 
                             QDialog, QVBoxLayout, QHBoxLayout, QDoubleSpinBox, QSpinBox, 
                             QFormLayout, QTabWidget, QDialogButtonBox, QTableWidget, 
                             QTableWidgetItem, QMessageBox, QWidget, QLineEdit, QGraphicsTextItem,
                             QInputDialog, QMenuBar)
from PyQt6.QtGui import QAction, QPageSize, QPageLayout, QKeySequence, QColor, QFont
from PyQt6.QtCore import Qt
from old_canvas import CADCanvas

# secret.py からURLを読み込み（未作成時は安全にスルー）
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
        payload = {"username": "朱書きCAD エラー通知", "content": f"🚨 **エラーが発生しました**\n```python\n{full_error_msg[-1800:]}\n```"}
        try:
            req = urllib.request.Request(DISCORD_WEBHOOK_URL, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            urllib.request.urlopen(req, timeout=3)
        except Exception: pass
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

sys.excepthook = global_exception_handler

# --- ダイアログ群 ---
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

# --- メインウィンドウ ---
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
        dxf_act = QAction("DXF形式で保存", self); dxf_act.setShortcut(QKeySequence("Ctrl+Shift+S")); dxf_act.triggered.connect(self.canvas.save_to_dxf); file_menu.addAction(dxf_act)
        
        edit_menu = menubar.addMenu("編集(&E)")
        undo_act = QAction("↶ 元に戻す", self); undo_act.setShortcut(QKeySequence.StandardKey.Undo); undo_act.triggered.connect(self.canvas.undo); edit_menu.addAction(undo_act)
        redo_act = QAction("↷ やり直し", self); redo_act.setShortcut(QKeySequence.StandardKey.Redo); redo_act.triggered.connect(self.canvas.redo); edit_menu.addAction(redo_act)
        edit_menu.addSeparator()
        
        group_act = QAction("🔗 グループ化", self); group_act.setShortcut(QKeySequence("Ctrl+G")); group_act.triggered.connect(self.canvas.group_selected_items); edit_menu.addAction(group_act)
        ungroup_act = QAction("💥 グループ分割", self); ungroup_act.setShortcut(QKeySequence("Ctrl+Shift+G")); ungroup_act.triggered.connect(self.canvas.ungroup_selected_items); edit_menu.addAction(ungroup_act)
        edit_menu.addSeparator()
        count_act = QAction("📊 レイヤー別オブジェクト集計", self); count_act.triggered.connect(self.open_layer_count_dialog); edit_menu.addAction(count_act)

        setting_menu = menubar.addMenu("設定(&T)")
        f_act = QAction("✂️ フィレット・面取り設定", self); f_act.triggered.connect(self.open_fillet_dialog); setting_menu.addAction(f_act)

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
            ("回転", "ROTATE"),
            ("拡大縮小", "SCALE"),
            ("ミラー", "MIRROR"),
            ("平行線", "OFFSET"),
            
            ("【 基本作図ツール 】", None),
            ("直線", "LINE"), 
            ("連続線", "POLYLINE"), 
            ("スプライン (自由曲線)", "SPLINE"), 
            ("四角形", "RECT"), 
            ("円 (中心・半径)", "CIRCLE"), 
            ("円弧 (中心・始・終)", "ARC"), 
            ("3点指定円弧", "ARC_3P"), 
            ("点", "POINT"),

            ("【 注釈・寸法・自動計測 】", None),
            ("引き出し線 (長/面積自動計測)", "LEADER"),
            ("角度寸法 (ANGULAR)", "DIM_ANGLE"),
            ("半径・直径寸法 (R/φ)", "DIM_RADIUS"),
            ("寸法線", "DIMENSION"), 
            ("文章入力", "TEXT"),
            ("雲マーク", "CLOUD"),
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

        self.sync_toolbar_with_active_layer()

    # --- ヘルパーメソッド ---
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