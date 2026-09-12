import sys
print("1. main.py 起動開始")

try:
    from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QToolBar, QComboBox
    print("2. PyQt6 インポート成功")
    from canvas import CADCanvas
    print("3. canvas.py インポート成功")
except Exception as e:
    print(f"❌ インポートエラーが発生しました: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("朱書きCAD Pro (動作確認版)")
        self.setGeometry(100, 100, 1200, 700)
        
        self.canvas = CADCanvas()
        self.setCentralWidget(self.canvas)
        print("4. メインウィンドウ初期化完了")

if __name__ == "__main__":
    print("5. アプリケーション起動中...")
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    print("6. 画面表示処理実行")
    sys.exit(app.exec())