"""Shared appearance for the compact desktop workspace."""

STYLE = """
QMainWindow, QDialog { background: #f5f7f6; color: #172f39; }
QWidget { font-family: 'Microsoft JhengHei', 'Noto Sans CJK TC', 'Segoe UI'; font-size: 13px; color: #172f39; }
QLabel#brand { font-size: 19px; font-weight: 700; color: #172f39; }
QLabel#muted { color: #75858a; }
QLabel#section { font-size: 16px; font-weight: 700; }
QLabel#notice { background: #fff5db; color: #745619; padding: 10px; border-radius: 6px; }
QPushButton, QToolButton { background: #ffffff; border: 1px solid #dbe3e0; border-radius: 5px; padding: 5px 8px; }
QPushButton:hover, QToolButton:hover { background: #edf5f1; border-color: #78b3a4; }
QPushButton:checked { background: #e1f3ed; border-color: #197e69; }
QPushButton:pressed { background: #d4eae2; }
QPushButton:disabled, QToolButton:disabled { color: #a1adaa; background: #edf0ee; }
QPushButton#primary { background: #197e69; color: #ffffff; border: 1px solid #197e69; font-weight: 700; }
QPushButton#primary:hover { background: #126651; }
QPushButton#pitch { min-width: 24px; min-height: 24px; font-family: 'Segoe UI'; font-size: 21px; font-weight: 600; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { border: 1px solid #d6dfdc; border-radius: 5px; padding: 4px; background: white; selection-background-color: #197e69; }
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #197e69; }
QAbstractItemView#notes { background: #edf1f3; border: 1px solid #d5dde2; border-radius: 6px; }
QTabWidget::pane { border: 1px solid #dbe3e0; background: #ffffff; border-radius: 6px; }
QTabBar::tab { padding: 7px 10px; background: #edf1ef; border: 0; }
QTabBar::tab:selected { background: #ffffff; color: #197e69; border-bottom: 2px solid #197e69; }
QGroupBox { border: 1px solid #dbe3e0; border-radius: 6px; margin-top: 10px; padding-top: 14px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QGroupBox#score_settings, QGroupBox#note_tools, QGroupBox#measure_tools { background: #ffffff; margin-top: 9px; padding-top: 9px; }
QGroupBox#score_settings::title, QGroupBox#note_tools::title, QGroupBox#measure_tools::title { color: #17664f; font-weight: 600; }
QWidget#tool_panel QPushButton, QWidget#tool_panel QToolButton { padding: 2px 5px; }
QWidget#tool_panel QComboBox, QWidget#tool_panel QSpinBox { padding: 2px 4px; }
QWidget#tool_panel QPushButton#pitch { padding: 3px; min-width: 24px; min-height: 24px; font-size: 19px; }
QStatusBar { background: #eaf0ed; }
QSplitter::handle { background: #e1e7e4; width: 2px; }
QProgressBar { border: none; background: #e1e7e4; height: 4px; }
QProgressBar::chunk { background: #197e69; }
QMenuBar, QMenu { background: #ffffff; }
QMenu::item:selected { background: #e1f3ed; }
"""
