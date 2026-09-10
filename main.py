import sys
from PyQt6.QtWidgets import (QApplication, QMainWindow, QToolBar, QFileDialog, 
                             QPushButton, QComboBox, QColorDialog, QLabel)
from PyQt6.QtGui import QAction
from canvas import CADCanvas

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("建築・施工用 朱書きCADシステム")
        self.setGeometry(100, 100, 1000, 700)
        
        self.canvas = CADCanvas()
        self.setCentralWidget(self.canvas)
        
        # ツールバーの追加
        toolbar = QToolBar()
        self.addToolBar(toolbar)
        
        # 背景読み込みボタン
        bg_action = QAction("背景画像を開く", self)
        bg_action.triggered.connect(self.open_background)
        toolbar.addAction(bg_action)
        
        toolbar.addSeparator()
        
        # 1. カラーピッカーボタン
        toolbar.addWidget(QLabel(" 色: "))
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(24, 24)
        self.update_color_button_style(self.canvas.current_color)
        self.color_btn.clicked.connect(self.choose_color)
        toolbar.addWidget(self.color_btn)
        
        toolbar.addSeparator()
        
        # 2. 線の太さドロップダウン
        toolbar.addWidget(QLabel(" 太さ: "))
        self.thickness_combo = QComboBox()
        thickness_options = [1, 2, 3, 5, 8, 10, 15, 20]
        for t in thickness_options:
            self.thickness_combo.addItem(f"{t} px", t)
        
        # 初期選択を2px (インデックス 1) に設定
        self.thickness_combo.setCurrentIndex(1)
        self.thickness_combo.currentIndexChanged.connect(self.on_thickness_changed)
        toolbar.addWidget(self.thickness_combo)

    def open_background(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "背景画像を選択", "", "Image Files (*.png *.jpg *.jpeg)"
        )
        if file_path:
            self.canvas.set_background_image(file_path)

    def choose_color(self):
        """QColorDialogを開き、選択した色をキャンバスとボタンに反映"""
        color = QColorDialog.getColor(self.canvas.current_color, self, "描画色の選択")
        if color.isValid():
            self.canvas.set_color(color)
            self.update_color_button_style(color)

    def update_color_button_style(self, color):
        """カラーピッカーボタンの見た目を現在選択中の色に変更"""
        self.color_btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #555;")

    def on_thickness_changed(self, index):
        """ドロップダウン変更時にキャンバスの線の太さを更新"""
        thickness = self.thickness_combo.itemData(index)
        self.canvas.set_thickness(thickness)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())