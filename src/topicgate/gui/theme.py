from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


LIGHT_THEME = """
QTabBar#workspaceDestinations::tab { padding: 7px 10px; color: #4b5563; border-bottom: 2px solid transparent; }
QTabBar#workspaceDestinations::tab:selected { color: #202124; border-bottom: 2px solid #405f78; font-weight: bold; }
QTabBar#workspaceDestinations::tab:disabled { color: #89939f; }
QWidget { color: #202124; font-size: 13px; }
QMainWindow, QDialog, QMessageBox, QWidget#applicationRoot { background: #f3f4f6; color: #202124; }
QMessageBox QLabel { background: transparent; color: #202124; }
QFrame[workspacePane="true"] { background: #ffffff; border: 1px solid #c8ced6; border-radius: 8px; }
QWidget#snapshotPanel, QWidget#snapshotAdvancedContent, QGroupBox#snapshotControls, QGroupBox#snapshotHealthPanel { background: #ffffff; }
QScrollArea#snapshotPanelScrollArea { background: #ffffff; }
QToolButton#snapshotAdvancedButton { border: 0; background: transparent; font-weight: 650; padding: 3px 5px; }
QToolButton#snapshotAdvancedButton:hover { background: #eef2f6; }
QGroupBox#snapshotControls, QGroupBox#snapshotHealthPanel { border: 1px solid #c8ced6; border-radius: 5px; margin-top: 8px; padding-top: 8px; }
QGroupBox#snapshotControls::title, QGroupBox#snapshotHealthPanel::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; color: #4b5563; font-weight: 650; }
QLabel#sectionTitle, QLabel#workspaceHeading { color: #4b5563; font-weight: 650; }
QLabel#sectionTitle { font-size: 11px; }
QLabel#workspaceHeading { font-size: 15px; }
QLabel#topicContextKind { color: #405d7a; background: #e7eff7; border: 1px solid #b8c9db; border-radius: 4px; padding: 2px 5px; font-size: 10px; font-weight: 650; }
QLabel#publishTopicHint { color: #4b5563; background: #f3f5f7; border: 1px solid #c8ced6; border-radius: 5px; padding: 7px; }
QLineEdit, QPlainTextEdit, QComboBox, QTreeView { background: #ffffff; border: 1px solid #b8c0ca; border-radius: 5px; padding: 5px; selection-background-color: #dce9f7; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QTreeView:focus { border: 1px solid #4f6f91; }
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled { color: #737b85; background: #f1f3f5; border-color: #c8ced6; }
QPlainTextEdit#decodedPayload, QPlainTextEdit#rawPayload { background: #fbfcfd; }
QPlainTextEdit#decodedPayload:focus, QPlainTextEdit#rawPayload:focus { background: #ffffff; }
QTabBar#topicDetailsMode, QTabBar#topicSettingsTabs, QTabBar#healthTabs, QTabBar#storedObservationsPages { background: transparent; }
QTabBar#topicDetailsMode::tab, QTabBar#topicSettingsTabs::tab, QTabBar#healthTabs::tab, QTabBar#storedObservationsPages::tab { color: #4b5563; background: #f3f5f7; border: 1px solid #b8c0ca; height: 22px; padding: 6px 16px; }
QTabBar#topicDetailsMode::tab:first, QTabBar#topicSettingsTabs::tab:first, QTabBar#healthTabs::tab:first, QTabBar#storedObservationsPages::tab:first { border-top-left-radius: 5px; border-bottom-left-radius: 5px; }
QTabBar#topicDetailsMode::tab:last, QTabBar#topicSettingsTabs::tab:last, QTabBar#healthTabs::tab:last, QTabBar#storedObservationsPages::tab:last { border-top-right-radius: 5px; border-bottom-right-radius: 5px; }
QTabBar#topicDetailsMode::tab:!first, QTabBar#topicSettingsTabs::tab:!first, QTabBar#healthTabs::tab:!first, QTabBar#storedObservationsPages::tab:!first { border-left: 0; }
QTabBar#topicDetailsMode::tab:hover:!selected, QTabBar#topicSettingsTabs::tab:hover:!selected, QTabBar#healthTabs::tab:hover:!selected, QTabBar#storedObservationsPages::tab:hover:!selected { color: #202124; background: #eef2f6; }
QTabBar#topicDetailsMode::tab:selected, QTabBar#topicSettingsTabs::tab:selected, QTabBar#healthTabs::tab:selected, QTabBar#storedObservationsPages::tab:selected { color: #ffffff; background: #405d7a; border-color: #405d7a; font-weight: 650; }
QLabel#brokerConnectionStatus { border-radius: 9px; padding: 3px 9px; font-weight: 650; }
QLabel#brokerConnectionStatus[connectionState="connected"] { color: #11653f; background: #dcf7e9; }
QLabel#brokerConnectionStatus[connectionState="connecting"], QLabel#brokerConnectionStatus[connectionState="reconnecting"] { color: #8a4b00; background: #fff0d5; }
QLabel#brokerConnectionStatus[connectionState="disconnected"] { color: #4b5563; background: #e9edf1; }
QPushButton#brokerHealthSummary, QPushButton#topicHealthBadge { text-align: left; font-weight: 650; padding: 3px 8px; }
QPushButton#topicHealthBadge { border-radius: 9px; font-size: 11px; }
QPushButton#brokerHealthSummary { color: #4b5563; background: #f8fafc; border: 1px solid #d8dde3; border-left: 3px solid #89939f; border-radius: 5px; }
QPushButton#brokerHealthSummary:hover { background: #f1f4f7; border-color: #b8c0ca; border-left-color: #6f7b87; }
QPushButton#brokerHealthSummary[healthTone="success"] { color: #11653f; background: #f5fbf8; border-color: #cce7d9; border-left-color: #4ca878; }
QPushButton#brokerHealthSummary[healthTone="success"]:hover { background: #ebf7f1; border-color: #a8dec2; border-left-color: #3f9166; }
QPushButton#brokerHealthSummary[healthTone="problem"] { color: #8f2525; background: #fff8f8; border-color: #ead0d0; border-left-color: #c65353; }
QPushButton#brokerHealthSummary[healthTone="problem"]:hover { background: #fff0f0; border-color: #d7a4a4; border-left-color: #ad4141; }
QPushButton#brokerHealthSummary[healthTone="warning"] { color: #8a4b00; background: #fffaf0; border-color: #eadbbd; border-left-color: #c58a29; }
QPushButton#brokerHealthSummary[healthTone="warning"]:hover { background: #fff3dc; border-color: #e3c68f; border-left-color: #ac741e; }
QPushButton#brokerHealthSummary[healthTone="neutral"] { color: #4b5563; }
QPushButton#topicHealthBadge[healthTone="success"] { color: #11653f; background: #dcf7e9; border-color: #a8dec2; }
QPushButton#topicHealthBadge[healthTone="problem"] { color: #8f2525; background: #fff0f0; border-color: #d7a4a4; }
QPushButton#topicHealthBadge[healthTone="warning"] { color: #8a4b00; background: #fff0d5; border-color: #e3c68f; }
QPushButton#topicHealthBadge[healthTone="neutral"] { color: #4b5563; background: #e9edf1; border-color: #c8ced6; }
QLabel[healthTone="problem"] { color: #8f2525; }
QLabel[healthTone="warning"] { color: #8a4b00; }
QLabel[healthTone="success"] { color: #11653f; }
QWidget#brokerProfilePopupRow { background: #ffffff; }
QToolButton#selectBrokerProfileButton { border: 0; text-align: left; padding: 6px 8px; }
QToolButton#selectBrokerProfileButton:hover { background: #eef2f6; }
QToolButton#editBrokerProfileButton, QToolButton#deleteBrokerProfileButton { padding: 5px 8px; }
QToolButton#deleteBrokerProfileButton { color: #a53030; border-color: #d7a4a4; }
QToolButton#deleteBrokerProfileButton:hover { color: #8f2525; background: #fff5f5; border-color: #c77d7d; }
QPushButton, QToolButton { background: #ffffff; border: 1px solid #b8c0ca; border-radius: 5px; padding: 6px 11px; }
QPushButton:hover, QToolButton:hover { background: #f7f8fa; border-color: #89939f; }
QPushButton:pressed, QToolButton:pressed { background: #eceff3; }
QPushButton:disabled, QToolButton:disabled { color: #737b85; background: #f1f3f5; border-color: #c8ced6; }
QPushButton[primary="true"] { color: #ffffff; background: #405d7a; border-color: #405d7a; }
QPushButton[primary="true"]:hover { background: #334e68; border-color: #334e68; }
QPushButton[primary="true"]:pressed { background: #2b4259; border-color: #2b4259; }
QPushButton[primary="true"]:disabled { color: #737b85; background: #e5e7eb; border-color: #c8ced6; }
QPushButton[danger="true"] { color: #a53030; border-color: #d7a4a4; }
QPushButton[danger="true"]:hover { color: #8f2525; background: #fff5f5; border-color: #c77d7d; }
QPushButton[danger="true"]:disabled { color: #a66a6a; background: #fffafa; border-color: #e0b4b4; }
QMenuBar, QMenu, QDockWidget { background: #ffffff; }
QMenuBar { border-bottom: 1px solid #c8ced6; }
QToolButton#brokerConnectionButton { background: transparent; border: 0; border-radius: 3px; padding: 0 6px; }
QToolButton#brokerConnectionButton:hover, QToolButton#brokerConnectionButton:focus { background: #eef2f6; }
QToolButton#brokerConnectionButton:pressed { background: #e4e7eb; }
QHeaderView::section { color: #4b5563; background: #f3f5f7; border: 0; border-bottom: 1px solid #c8ced6; padding: 6px; font-weight: 600; }
QTreeView { alternate-background-color: #fafbfc; }
QTreeView::item { border-left: 3px solid transparent; padding: 3px 2px; }
QTreeView::item:hover { color: #202124; background: #eef2f6; border-left-color: #9aa8b6; }
QTreeView::item:selected { color: #202124; background: #dce9f7; border-left-color: #405d7a; }
QTreeView#observerTree::item { border-left: 0; }
QSplitter::handle:horizontal { background: #e4e7eb; border-left: 1px solid #c8ced6; border-right: 1px solid #c8ced6; width: 8px; }
QSplitter::handle:vertical { background: #e4e7eb; border-top: 1px solid #c8ced6; border-bottom: 1px solid #c8ced6; height: 8px; }
QDockWidget { border-top: 1px solid #c8ced6; }
"""


def apply_light_theme(application: QApplication) -> None:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f3f4f6"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#fafbfc"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#dce9f7"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#737b85"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#202124"))
    application.setPalette(palette)
    application.setStyleSheet(LIGHT_THEME)
