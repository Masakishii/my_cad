import sys
from PyQt6.QtWidgets import (QApplication, QMainWindow, QToolBar, QFileDialog, 
                             QPushButton, QComboBox, QColorDialog, QLabel, QCheckBox)
from PyQt6.QtGui import QAction
from PyQt6.QtCore import Qt
from canvas import CADCanvas

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("建築・施工用 朱書きCADシステム")
        self.setGeometry(100, 100, 1400, 750)
        
        self.canvas = CADCanvas()
        self.setCentralWidget(self.canvas)
        
        toolbar = QToolBar()
        self.addToolBar(toolbar)
        
        # ファイル機能
        bg_action = QAction("背景(PDF/画像)を開く", self)
        bg_action.triggered.connect(self.open_background)
        toolbar.addAction(bg_action)
        
        dxf_action = QAction("DXF保存", self)
        dxf_action.triggered.connect(self.canvas.save_to_dxf)
        toolbar.addAction(dxf_action)
        
        toolbar.addSeparator()
        
        # ツール切替
        toolbar.addWidget(QLabel(" ツール: "))
        self.tool_combo = QComboBox()
        tools = [
            ("選択・移動・削除", "SELECT"),
            ("選択図形を雲マーク化", "CLOUD_OBJECT"),
            ("直線", "LINE"),
            ("水平線", "H_LINE"),
            ("垂直線", "V_LINE"),
            ("四角形", "RECT"),
            ("2点指定円", "CIRCLE_2P"),
            ("3点指定円", "CIRCLE_3P"),
            ("3点指定円弧", "ARC_3P"),
            ("楕円", "ELLIPSE"),
            ("連続線", "POLYLINE"),
            ("多角形", "POLYGON"),
            ("フリーハンド雲マーク", "CLOUD"),
            ("矢印注釈", "ARROW"),
            ("寸法線 (自動計測)", "DIMENSION"),
            ("引き出し線 (注釈)", "LEADER"),
            ("テキスト入力", "TEXT")
        ]
        for name, mode in tools:
            self.tool_combo.addItem(name, mode)
        self.tool_combo.currentIndexChanged.connect(self.on_tool_changed)
        toolbar.addWidget(self.tool_combo)
        
        toolbar.addSeparator()
        
        # 15度角度スナップチェックボックス
        self.angle_chk = QCheckBox("15°固定")
        self.angle_chk.setChecked(True)
        self.angle_chk.toggled.connect(self.canvas.set_angle_snap)
        toolbar.addWidget(self.angle_chk)

        toolbar.addSeparator()

        # 色選択
        toolbar.addWidget(QLabel(" 色: "))
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(24, 24)
        self.update_color_button_style(self.canvas.current_color)
        self.color_btn.clicked.connect(self.choose_color)
        toolbar.addWidget(self.color_btn)
        
        toolbar.addSeparator()
        
        # 線の太さ
        toolbar.addWidget(QLabel(" 太さ: "))
        self.thickness_combo = QComboBox()
        for t in [1, 2, 3, 5, 8, 10, 15, 20]:
            self.thickness_combo.addItem(f"{t} px", t)
        self.thickness_combo.setCurrentIndex(1)
        self.thickness_combo.currentIndexChanged.connect(self.on_thickness_changed)
        toolbar.addWidget(self.thickness_combo)

        toolbar.addSeparator()

        # 線種
        toolbar.addWidget(QLabel(" 線種: "))
        self.style_combo = QComboBox()
        self.style_combo.addItem("実線 (───)", Qt.PenStyle.SolidLine)
        self.style_combo.addItem("破線 (---)", Qt.PenStyle.DashLine)
        self.style_combo.addItem("点線 (･･･)", Qt.PenStyle.DotLine)
        self.style_combo.addItem("一点鎖線 (-・-)", Qt.PenStyle.DashDotLine)
        self.style_combo.currentIndexChanged.connect(self.on_style_changed)
        toolbar.addWidget(self.style_combo)

    def open_background(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "背景ファイルを選択", "", "図面・画像 (*.pdf *.png *.jpg *.jpeg)"
        )
        if file_path:
            self.canvas.set_background_file(file_path)

    def choose_color(self):
        color = QColorDialog.getColor(self.canvas.current_color, self, "描画色の選択")
        if color.isValid():
            self.canvas.set_color(color)
            self.update_color_button_style(color)

    def update_color_button_style(self, color):
        self.color_btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #555;")

    def on_thickness_changed(self, index):
        self.canvas.set_thickness(self.thickness_combo.itemData(index))

    def on_style_changed(self, index):
        self.canvas.set_style(self.style_combo.itemData(index))

    def on_tool_changed(self, index):
        self.canvas.set_mode(self.tool_combo.itemData(index))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())