import sys
import os
import time
import subprocess
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtWebEngineWidgets import QWebEngineView

APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.join(APP_DIR, "app.py")
ICON_PATH = os.path.join(APP_DIR, "icon.png")
PROACTIVE_DAEMON_PATH = os.path.join(APP_DIR, "run_proactive_daemon.py")

class JemiWindow(QMainWindow):
    def __init__(self, server_process, daemon_process):
        super().__init__()
        self.server_process = server_process
        self.daemon_process = daemon_process
        self.setWindowTitle("✨ Джеми")
        self.resize(500, 750)

        # Веб-движок для Streamlit
        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl("http://localhost:8501"))
        self.setCentralWidget(self.browser)

        # Нативный трей Qt
        self.tray_icon = QSystemTrayIcon(QIcon(ICON_PATH), self)
        tray_menu = QMenu()

        show_action = QAction("Показать Джеми", self)
        show_action.triggered.connect(self.show_and_activate)
        tray_menu.addAction(show_action)

        hide_action = QAction("Скрыть", self)
        hide_action.triggered.connect(self.hide)
        tray_menu.addAction(hide_action)

        tray_menu.addSeparator()

        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(self.full_quit)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_click)
        self.tray_icon.show()

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show_and_activate()

    def show_and_activate(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        # Перехватываем крестик: скрываем окно вместо завершения
        event.ignore()
        self.hide()

    def full_quit(self):
        # Корректно завершаем демон и сервер
        if self.daemon_process and self.daemon_process.poll() is None:
            self.daemon_process.terminate()
            try:
                self.daemon_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.daemon_process.kill()
        if self.server_process and self.server_process.poll() is None:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.server_process.kill()
        QApplication.quit()

def main():
    # Запускаем Streamlit сервер
    server_process = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", APP_PATH, "--server.headless", "true"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Запускаем Proactive Engine демон (если файл существует)
    daemon_process = None
    if os.path.exists(PROACTIVE_DAEMON_PATH):
        daemon_process = subprocess.Popen(
            [sys.executable, PROACTIVE_DAEMON_PATH],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print("Proactive Engine демон запущен.")
    else:
        print("Файл run_proactive_daemon.py не найден. Демон не запущен.")

    time.sleep(2.5)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = JemiWindow(server_process, daemon_process)
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
