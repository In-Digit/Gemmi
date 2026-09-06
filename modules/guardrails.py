import os
import re
import shutil
from datetime import datetime

LOG_DIR = os.path.expanduser("~/gemini_companion/logs")
AUDIT_LOG_FILE = os.path.join(LOG_DIR, "assistant_audit.log")


class GuardrailEngine:
    """
    Контур безопасности (Guardrail Execution Engine):
    1. Перехват rm и замена на перемещение в системную корзину (gio trash).
    2. Жесткий табу-запрет на любую очистку корзины.
    3. Автоматическое создание .bak_timestamp резервных копий перед перезаписью.
    4. Аудит всех вызовов в assistant_audit.log.
    """

    def __init__(self, log_filepath: str = AUDIT_LOG_FILE):
        self.log_filepath = os.path.abspath(log_filepath)
        os.makedirs(os.path.dirname(self.log_filepath), exist_ok=True)

    def log_audit(self, action_type: str, details: str, status: str = "SUCCESS"):
        """Запись всех системных действий и команд в журнал аудита."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] [{status}] [{action_type}] {details}\n"
        try:
            with open(self.log_filepath, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            print(f"[GuardrailEngine] Ошибка записи в журнал аудита: {e}")

    def make_backup(self, filepath: str) -> str:
        """Создание резервной копии файла вида filename.ext.bak_YYYYMMDD_HHMMSS перед модификацией."""
        abs_path = os.path.abspath(filepath)
        if not os.path.exists(abs_path):
            return ""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{abs_path}.bak_{timestamp}"

        try:
            shutil.copy2(abs_path, backup_path)
            self.log_audit("BACKUP_CREATED", f"Оригинал: {abs_path} -> Копия: {backup_path}")
            return backup_path
        except Exception as e:
            self.log_audit("BACKUP_FAILED", f"Файл: {abs_path}, Ошибка: {e}", status="ERROR")
            raise RuntimeError(f"Не удалось создать резервную копию: {e}")

    def sanitize_command(self, cmd: str) -> str:
        """
        Проверка и безопасная трансформация Bash-команд:
        - Блокирует попытки очистки корзины.
        - Перехватывает rm и заменяет на gio trash (перемещение в системную корзину).
        """
        cmd_strip = cmd.strip()

        # Табу на очистку корзины
        forbidden_patterns = [
            r"trash-empty",
            r"rm\s+.*\.local/share/Trash",
            r"rm\s+.*\.trash",
            r"gio\s+trash\s+--empty"
        ]

        for pattern in forbidden_patterns:
            if re.search(pattern, cmd_strip, re.IGNORECASE):
                self.log_audit("BLOCKED_COMMAND", f"Попытка очистки корзины: {cmd}", status="BLOCKED")
                raise PermissionError("Очистка корзины категорически запрещена правилами безопасности.")

        # Перехват rm и подмена на gio trash
        if re.search(r"\brm\b", cmd_strip):
            sanitized = re.sub(r"\brm(\s+-[rRfFiI]+)*\s+", "gio trash ", cmd_strip)
            self.log_audit("COMMAND_SANITIZED", f"Исходная: '{cmd}' -> Заменена на: '{sanitized}'")
            return sanitized

        return cmd_strip
