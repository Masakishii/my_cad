import copy
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, 
                             QPushButton, QTextEdit, QTableWidget, QTableWidgetItem, 
                             QComboBox, QDoubleSpinBox, QColorDialog, QFontComboBox, QCheckBox)
from PyQt6.QtGui import QColor, QFont

class TextEditDialog(QDialog):
    """文字編集ダイアログ"""
    def __init__(self, parent=None, text="", font_size=12, color=None, font_family="Meiryo", bold=False, italic=False):
        super().__init__(parent)
        self.setWindowTitle("文章の入力・高度な書式編集")
        self.resize(480, 380)
        self.selected_color = QColor(color) if color else QColor(255, 0, 0)

        layout = QVBoxLayout(self)
        
        cfg_layout1 = QHBoxLayout()
        cfg_layout1.addWidget(QLabel("フォント:"))
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(font_family))
        cfg_layout1.addWidget(self.font_combo)

        cfg_layout1.addWidget(QLabel("サイズ(pt):"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 500)
        self.size_spin.setValue(int(font_size))
        cfg_layout1.addWidget(self.size_spin)
        layout.addLayout(cfg_layout1)

        cfg_layout2 = QHBoxLayout()
        self.bold_check = QCheckBox("太字 (Bold)")
        self.bold_check.setChecked(bold)
        cfg_layout2.addWidget(self.bold_check)

        self.italic_check = QCheckBox("斜体 (Italic)")
        self.italic_check.setChecked(italic)
        cfg_layout2.addWidget(self.italic_check)

        self.color_btn = QPushButton(" 色を選択 ")
        self.update_color_button_style()
        self.color_btn.clicked.connect(self.choose_color)
        cfg_layout2.addWidget(self.color_btn)
        cfg_layout2.addStretch()
        layout.addLayout(cfg_layout2)

        layout.addWidget(QLabel("テキスト:"))
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(text)
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def choose_color(self):
        col = QColorDialog.getColor(self.selected_color, self, "文字色の選択")
        if col.isValid():
            self.selected_color = col
            self.update_color_button_style()

    def update_color_button_style(self):
        txt_col = "#000000" if (self.selected_color.red()*0.299 + self.selected_color.green()*0.587 + self.selected_color.blue()*0.114) > 180 else "#FFFFFF"
        self.color_btn.setStyleSheet(f"background-color: {self.selected_color.name()}; color: {txt_col}; font-weight: bold; border: 1px solid #888;")

    def get_result(self):
        return (self.text_edit.toPlainText(), self.size_spin.value(), self.selected_color, 
                self.font_combo.currentFont().family(), self.bold_check.isChecked(), self.italic_check.isChecked())


class TableEditDialog(QDialog):
    """表データ編集ダイアログ"""
    def __init__(self, parent=None, grid_data=None, cell_w=100, cell_h=30, align="CENTER", font_size=12, color=None, font_family="Meiryo", bold=False, italic=False):
        super().__init__(parent)
        self.setWindowTitle("表データの詳細再編集")
        self.resize(720, 520)
        self.grid_data = copy.deepcopy(grid_data) if grid_data else [[""]]
        self.selected_color = QColor(color) if color else QColor(0, 0, 0)

        main_layout = QVBoxLayout(self)
        
        cfg_layout1 = QHBoxLayout()
        self.w_spin = QDoubleSpinBox()
        self.w_spin.setRange(10.0, 2000.0); self.w_spin.setValue(float(cell_w))
        cfg_layout1.addWidget(QLabel("セル幅:"))
        cfg_layout1.addWidget(self.w_spin)

        self.h_spin = QDoubleSpinBox()
        self.h_spin.setRange(5.0, 1000.0); self.h_spin.setValue(float(cell_h))
        cfg_layout1.addWidget(QLabel("セル高:"))
        cfg_layout1.addWidget(self.h_spin)

        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(font_family))
        cfg_layout1.addWidget(QLabel("フォント:"))
        cfg_layout1.addWidget(self.font_combo)

        self.font_spin = QSpinBox()
        self.font_spin.setRange(6, 200); self.font_spin.setValue(int(font_size))
        cfg_layout1.addWidget(QLabel("サイズ:"))
        cfg_layout1.addWidget(self.font_spin)
        main_layout.addLayout(cfg_layout1)

        cfg_layout2 = QHBoxLayout()
        self.bold_check = QCheckBox("太字"); self.bold_check.setChecked(bold)
        self.italic_check = QCheckBox("斜体"); self.italic_check.setChecked(italic)
        cfg_layout2.addWidget(self.bold_check)
        cfg_layout2.addWidget(self.italic_check)

        self.color_btn = QPushButton(" 色を選択 ")
        self.update_color_button_style()
        self.color_btn.clicked.connect(self.choose_color)
        cfg_layout2.addWidget(self.color_btn)

        self.align_combo = QComboBox()
        self.align_combo.addItems(["中央 (CENTER)", "左寄せ (LEFT)", "右寄せ (RIGHT)"])
        align_map = {"CENTER": 0, "LEFT": 1, "RIGHT": 2}
        self.align_combo.setCurrentIndex(align_map.get(str(align).upper(), 0))
        cfg_layout2.addWidget(QLabel("揃え:"))
        cfg_layout2.addWidget(self.align_combo)
        cfg_layout2.addStretch()
        main_layout.addLayout(cfg_layout2)

        btn_layout = QHBoxLayout()
        add_r = QPushButton("＋ 行追加"); add_r.clicked.connect(lambda: self.table_widget.insertRow(self.table_widget.rowCount()))
        del_r = QPushButton("－ 行削除"); del_r.clicked.connect(lambda: self.table_widget.removeRow(max(0, self.table_widget.currentRow())))
        add_c = QPushButton("＋ 列追加"); add_c.clicked.connect(lambda: self.table_widget.insertColumn(self.table_widget.columnCount()))
        del_c = QPushButton("－ 列削除"); del_c.clicked.connect(lambda: self.table_widget.removeColumn(max(0, self.table_widget.currentColumn())))
        btn_layout.addWidget(add_r); btn_layout.addWidget(del_r); btn_layout.addWidget(add_c); btn_layout.addWidget(del_c)
        main_layout.addLayout(btn_layout)

        self.table_widget = QTableWidget()
        self.populate_table()
        main_layout.addWidget(self.table_widget)

        dlg_btns = QHBoxLayout()
        ok_btn = QPushButton("OK"); ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("キャンセル"); cancel_btn.clicked.connect(self.reject)
        dlg_btns.addStretch(); dlg_btns.addWidget(ok_btn); dlg_btns.addWidget(cancel_btn)
        main_layout.addLayout(dlg_btns)

    def choose_color(self):
        col = QColorDialog.getColor(self.selected_color, self, "文字色の選択")
        if col.isValid(): self.selected_color = col; self.update_color_button_style()

    def update_color_button_style(self):
        txt_col = "#000000" if (self.selected_color.red()*0.299 + self.selected_color.green()*0.587 + self.selected_color.blue()*0.114) > 180 else "#FFFFFF"
        self.color_btn.setStyleSheet(f"background-color: {self.selected_color.name()}; color: {txt_col}; font-weight: bold; border: 1px solid #888;")

    def populate_table(self):
        rows, cols = len(self.grid_data), max(len(r) for r in self.grid_data) if self.grid_data else 1
        self.table_widget.setRowCount(rows); self.table_widget.setColumnCount(cols)
        for r in range(rows):
            for c in range(cols):
                val = self.grid_data[r][c] if c < len(self.grid_data[r]) else ""
                self.table_widget.setItem(r, c, QTableWidgetItem(str(val)))

    def get_result(self):
        rows, cols = self.table_widget.rowCount(), self.table_widget.columnCount()
        res_grid = [[self.table_widget.item(r, c).text() if self.table_widget.item(r, c) else "" for c in range(cols)] for r in range(rows)]
        align_list = ["CENTER", "LEFT", "RIGHT"]
        return (res_grid, self.w_spin.value(), self.h_spin.value(), align_list[self.align_combo.currentIndex()], 
                self.font_spin.value(), self.selected_color, self.font_combo.currentFont().family(), 
                self.bold_check.isChecked(), self.italic_check.isChecked())