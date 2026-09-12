import sys
from PyQt6.QtWidgets import QApplication, QLabel

app = QApplication(sys.argv)
label = QLabel("テスト表示：PyQt6は正常に動いています！")
label.resize(400, 200)
label.show()
sys.exit(app.exec())