# /home/mrak/gemini_companion/run_widget.py
import sys
import os
import time
import fcntl
import subprocess
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtWebEngineWidgets import QWebEngineView

APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.join(APP_DIR, "app.py")
ICON_PATH = os.path.join(APP_DIR, "icon.png")
PROACTIVE_DAEMON_PATH = os.path.join(APP_DIR, "run_proactive_daemon.py")
VOICE_ASSISTANT_PATH = os.path.join(APP_DIR, "voice_assistant.py")
VOICE_STOP_FLAG = os.path.expanduser("~/gemini_companion/data/voice_stop.flag")
LOCK_FILE_PATH = "/tmp/jemi_widget.lock"


class JemiWindow(QMainWindow):
    """
    Главное окно виджета на PyQt6 с интеграцией Streamlit через QWebEngineView
    и управлением нативным треем системы.
    """
    def __init__(self, server_process, daemon_process, voice_process, lock_fd):
        super().__init__()
        self.server_process = server_process
        self.daemon_process = daemon_process
        self.voice_process = voice_process
        self.lock_fd = lock_fd  # Дескриптор блокировки единственного экземпляра
        self.setWindowTitle("✨ Джеми")
        self.resize(500, 750)

        # Веб-движок для отображения локального Streamlit
        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl("http://localhost:8501"))
        self.setCentralWidget(self.browser)

        # Нативный системный трей Qt
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
        """Обработка клика по иконке в трее."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show_and_activate()

    def show_and_activate(self):
        """Разворачивает окно на передний план."""
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        """Перехватываем закрытие окна крестиком: сворачиваем в трей вместо завершения."""
        event.ignore()
        self.hide()

    def full_quit(self):
        """Полное корректное завершение приложения и всех дочерних служб."""
        # Создаем стоп-флаг для голосового помощника
        try:
            os.makedirs(os.path.dirname(VOICE_STOP_FLAG), exist_ok=True)
            with open(VOICE_STOP_FLAG, "w") as f:
                f.write("stop")
        except Exception:
            pass

        # Завершаем голосовой процесс
        if self.voice_process and self.voice_process.poll() is None:
            self.voice_process.terminate()
            try:
                self.voice_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.voice_process.kill()

        # Завершаем демон проактивности
        if self.daemon_process and self.daemon_process.poll() is None:
            self.daemon_process.terminate()
            try:
                self.daemon_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.daemon_process.kill()

        # Завершаем сервер Streamlit
        if self.server_process and self.server_process.poll() is None:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.server_process.kill()

        # Удаляем lock-файл
        if self.lock_fd:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                os.close(self.lock_fd)
                if os.path.exists(LOCK_FILE_PATH):
                    os.remove(LOCK_FILE_PATH)
            except Exception:
                pass

        QApplication.quit()


def acquire_single_instance_lock():
    """Проверяет, запущена ли уже копия приложения через эксклюзивный flock."""
    try:
        lock_fd = os.open(LOCK_FILE_PATH, os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(lock_fd, 0)
        os.write(lock_fd, f"{os.getpid()}\n".encode("utf-8"))
        return lock_fd
    except (BlockingIOError, OSError):
        print("[run_widget] Копия Джеми уже запущена. Завершение дублирующего процесса.")
        sys.exit(0)


def main():
    lock_fd = acquire_single_instance_lock()

    # Удаляем старый стоп-флаг голоса перед запуском
    if os.path.exists(VOICE_STOP_FLAG):
        try:
            os.remove(VOICE_STOP_FLAG)
        except Exception:
            pass

    # Запуск сервера Streamlit для Джемми (порт 8501)
    server_process = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", APP_PATH,
            "--server.headless", "true",
            "--server.address", "0.0.0.0",
            "--server.port", "8501",
            "--server.enableCORS", "false",
            "--server.enableXsrfProtection", "false"
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Запуск фонового демона проактивности (порт 8765)
    daemon_process = None
    if os.path.exists(PROACTIVE_DAEMON_PATH):
        daemon_process = subprocess.Popen(
            [sys.executable, PROACTIVE_DAEMON_PATH],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print("[run_widget] Демон Proactive Engine запущен.")

    # Запуск голосового ассистента (voice_assistant.py)
    voice_process = None
    if os.path.exists(VOICE_ASSISTANT_PATH):
        voice_process = subprocess.Popen(
            [sys.executable, VOICE_ASSISTANT_PATH],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print("[run_widget] Голосовой ассистент запущен.")

    time.sleep(2.0)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = JemiWindow(server_process, daemon_process, voice_process, lock_fd)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
