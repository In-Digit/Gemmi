#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль безопасного исполнения Bash-команд с поддержкой Fast-Path (action_presets.json).
Прогоняет все вызовы через GuardrailEngine (замена rm на gio trash, защита корзины, аудит).
Дополнительно корректно обрабатывает фоновые команды (завершающиеся на '&'), чтобы не
блокировать интерфейс и не ждать их завершения.
"""

import os
import json
import subprocess
import shlex
from typing import Dict, Any, Tuple

from modules.guardrails import GuardrailEngine

PRESETS_FILE = os.path.expanduser("~/gemini_companion/config/action_presets.json")


class BashExecutor:
    """
    Модуль безопасного исполнения Bash-команд с поддержкой Fast-Path (action_presets.json).
    Прогоняет все вызовы через GuardrailEngine (замена rm на gio trash, защита корзины, аудит).
    """

    def __init__(self, presets_filepath: str = PRESETS_FILE):
        self.guardrail = GuardrailEngine()
        self.presets_filepath = os.path.abspath(presets_filepath)
        self.presets: Dict[str, str] = self._load_presets()

    def _load_presets(self) -> Dict[str, str]:
        """Загрузка быстрых команд-пресетов из action_presets.json"""
        if os.path.exists(self.presets_filepath):
            try:
                with open(self.presets_filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[BashExecutor] Ошибка чтения {self.presets_filepath}: {e}")
                return {}
        return {}

    def execute(self, command: str) -> Tuple[bool, str]:
        """
        Выполняет команду в системе после проверки безопасности.
        Возвращает кортеж (успех: bool, вывод_или_ошибка: str).
        """
        cmd_strip = command.strip()

        # Проверка на наличие пресета (Fast-Path)
        cmd_to_run = self.presets.get(cmd_strip.lower(), cmd_strip)

        # Валидация и трансформация через контур безопасности
        try:
            safe_cmd = self.guardrail.sanitize_command(cmd_to_run)
        except PermissionError as e:
            return False, str(e)

        # Определяем, является ли команда фоновой (заканчивается на '&')
        # Простейшая проверка: если последний символ '&', то фоновый запуск
        is_background = safe_cmd.rstrip().endswith('&')

        try:
            if is_background:
                # Запускаем в фоне, не дожидаясь завершения
                # Используем Popen с DEVNULL для stdout/stderr, чтобы не блокироваться
                subprocess.Popen(
                    safe_cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True  # отвязываем от текущего процесса
                )
                self.guardrail.log_audit("EXEC_BACKGROUND", f"Cmd: '{safe_cmd}'")
                return True, "Команда запущена в фоне."
            else:
                # Обычное выполнение с таймаутом
                result = subprocess.run(
                    safe_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                stdout = result.stdout.strip()
                stderr = result.stderr.strip()

                if result.returncode == 0:
                    output = stdout if stdout else "Команда успешно выполнена."
                    self.guardrail.log_audit("EXEC_SUCCESS", f"Cmd: '{safe_cmd}'")
                    return True, output
                else:
                    error_msg = stderr if stderr else f"Команда завершилась с кодом {result.returncode}."
                    self.guardrail.log_audit("EXEC_ERROR", f"Cmd: '{safe_cmd}' | Err: {error_msg}", status="ERROR")
                    return False, error_msg

        except subprocess.TimeoutExpired:
            err = "Превышен таймаут выполнения (30 секунд)."
            self.guardrail.log_audit("EXEC_TIMEOUT", f"Cmd: '{safe_cmd}'", status="TIMEOUT")
            return False, err
        except Exception as e:
            err = f"Ошибка запуска: {e}"
            self.guardrail.log_audit("EXEC_EXCEPTION", f"Cmd: '{safe_cmd}' | Err: {err}", status="ERROR")
            return False, err
