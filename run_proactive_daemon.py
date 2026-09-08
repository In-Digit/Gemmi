# /home/mrak/gemini_companion/run_proactive_daemon.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Демон для запуска Proactive Engine и User Facts Manager.
Запускает HTTP-сервер на 0.0.0.0:8765 для отслеживания статуса (проактивные сообщения
и синхронизация истории для мобильных клиентов в локальной сети).
"""

import os
import sys
import time
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Добавляем корневую директорию проекта в sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.proactive_engine import ProactiveEngine
from modules.user_facts_manager import UserFactsManager

# Пути к файлам данных
DATA_DIR = os.path.expanduser("~/gemini_companion/data")
QUEUE_FILE = os.path.join(DATA_DIR, "proactive_queue.json")
HISTORY_FILE = os.path.expanduser("~/gemini_companion/history.json")
HTTP_PORT = 8765


class ProactiveStatusHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP-запросов: возвращает JSON со статусом новых сообщений и изменения истории."""

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/proactive_status":
            has_new = False
            history_mtime = 0.0

            # Проверка очереди проактивности
            if os.path.exists(QUEUE_FILE):
                try:
                    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                        if f.read().strip():
                            has_new = True
                except Exception:
                    pass

            # Проверка времени модификации history.json для синхронизации устройств
            if os.path.exists(HISTORY_FILE):
                try:
                    history_mtime = os.path.getmtime(HISTORY_FILE)
                except Exception:
                    pass

            response_data = {
                "has_new": has_new,
                "history_mtime": history_mtime
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(response_data).encode("utf-8"))
        else:
            self.send_response(404)
            self._send_cors_headers()
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_http_server():
    """Запускает HTTP-сервер на всех интерфейсах (0.0.0.0) в отдельном потоке."""
    server = HTTPServer(("0.0.0.0", HTTP_PORT), ProactiveStatusHandler)
    print(f"HTTP-сервер статуса запущен на http://0.0.0.0:{HTTP_PORT}")
    server.serve_forever()


def main():
    print("Запуск Proactive Engine и User Facts Manager...")
    engine = ProactiveEngine()
    facts_manager = UserFactsManager()

    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    print("Демон активен. Нажмите Ctrl+C для остановки.")
    try:
        while True:
            engine.tick()
            facts_manager.run_analysis_if_needed()
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nОстановка демона...")
        engine._save_state()
        print("Состояние сохранено. Выход.")


if __name__ == "__main__":
    main()
