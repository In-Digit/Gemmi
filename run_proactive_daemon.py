#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Демон для запуска Proactive Engine и User Facts Manager.
Также запускает простой HTTP-сервер, который отдаёт статус наличия новых
проактивных сообщений (для JavaScript-поллинга в Streamlit).
Сервер поддерживает CORS, чтобы браузер мог обращаться к нему из приложения Streamlit.
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

# Путь к очереди проактивных сообщений
QUEUE_FILE = os.path.expanduser("~/gemini_companion/data/proactive_queue.json")
HTTP_PORT = 8765  # порт для опроса статуса


class ProactiveStatusHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP-запросов: возвращает JSON со статусом наличия новых сообщений."""

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
            if os.path.exists(QUEUE_FILE):
                try:
                    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                    if content:
                        has_new = True
                except Exception:
                    pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"has_new": has_new}).encode("utf-8"))
        else:
            self.send_response(404)
            self._send_cors_headers()
            self.end_headers()

    def log_message(self, format, *args):
        # Подавляем стандартный вывод логов HTTP-сервера
        pass


def start_http_server():
    """Запускает HTTP-сервер в отдельном потоке."""
    server = HTTPServer(("localhost", HTTP_PORT), ProactiveStatusHandler)
    print(f"HTTP-сервер статуса запущен на http://localhost:{HTTP_PORT}")
    server.serve_forever()


def main():
    print("Запуск Proactive Engine и User Facts Manager...")
    engine = ProactiveEngine()
    facts_manager = UserFactsManager()

    # Запускаем HTTP-сервер в фоновом потоке
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
