import os
from modules.notebook_manager import NotebookManager
from modules.active_state_manager import ActiveStateManager
from modules.guardrails import GuardrailEngine
from modules.bash_executor import BashExecutor


def main():
    print("=== Тестирование подсистем ===")

    # 1. Проверка Fast-Path и Guardrails
    executor = BashExecutor()
    print("\n[1] Тест Fast-Path (запрос 'статус сети'):")
    success, out = executor.execute("статус сети")
    print(f"Статус: {success}\nВывод:\n{out}")

    print("\n[2] Тест перехвата rm (замена на gio trash):")
    test_file = os.path.expanduser("~/gemini_companion/data/test_rm.txt")
    with open(test_file, "w", encoding="utf-8") as f:
        f.write("Тестовый файл")

    success, out = executor.execute(f"rm {test_file}")
    print(f"Статус: {success}\nВывод:\n{out}")

    # 2. Проверка NotebookManager
    nb = NotebookManager()
    print("\n[3] Тест записи и чтения блокнота:")
    nb.add_note("Тестовая процедура проверки модулей", source_prompt="запомни на будущее тестовую процедуру")
    print(nb.get_all_notes_formatted())

    # 3. Проверка ActiveStateManager
    asm = ActiveStateManager()
    print("\n[4] Тест суточного контекста:")
    asm.update_summary("Успешно развёрнуты и проверены модули безопасности и памяти.")
    print(asm.get_context_formatted())


if __name__ == "__main__":
    main()
