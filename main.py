import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.context_manager import ContextManager
from modules.llm_client import ask_gemini

def generate_summary(history: list) -> str:
    """Создает краткую выжимку тезисов из диалога."""
    if not history:
        return ""

    formatted_dialog = []
    for msg in history:
        role = "Пользователь" if msg["role"] == "user" else "Ассистент"
        text = msg.get("parts", [{}])[0].get("text", "")
        formatted_dialog.append(f"{role}: {text}")

    prompt = (
        "Сделай очень краткую выжимку (1-2 предложения, только ключевая суть, факты и решения) "
        "из этого диалога:\n\n" + "\n".join(formatted_dialog)
    )

    summary = ask_gemini(prompt, timeout=15)
    if summary.startswith("Ошибка:"):
        return ""
    return summary.strip()

def main():
    # ContextManager сам подтянет config.json и соберет правильную инструкцию
    ctx = ContextManager()

    print("Запуск Джеми...")
    print("Команды: /new или 'новая тема', /pause или 'продолжим позже', /summary, exit\n")

    while True:
        try:
            user_input = input("Алексей: ").strip()
            if not user_input:
                continue

            cmd = user_input.strip().rstrip(".!?").lower()

            if cmd in ["exit", "quit"]:
                print("До связи!")
                break

            if cmd in ["/new", "новая тема", "новый разговор"] or "новая тема" in cmd or "новый разговор" in cmd:
                ctx.clear_all()
                print("\n[Контекст и выжимка полностью очищены. Начинаем с чистого листа.]\n")
                continue

            if cmd in ["/pause", "продолжим позже"] or "продолжим позже" in cmd or "пауз" in cmd:
                ctx.set_paused(True)
                print("\n[Диалог поставлен на паузу. 10-минутный таймер отключен до следующего сообщения.]\n")
                continue

            if cmd in ["/summary", "резюме"] or "резюме" in cmd:
                if ctx.summary:
                    print(f"\n[Накопленная выжимка за день]:\n{ctx.summary}\n")
                else:
                    print("\n[Выжимка за день пока пуста.]\n")
                continue

            ctx.check_timeout(summarize_func=generate_summary)
            ctx.add_user_message(user_input)
            payload = ctx.get_payload()

            response_text = ask_gemini(payload)
            print(f"\nДжеми: {response_text}\n")

            if not response_text.startswith("Ошибка:"):
                ctx.add_model_message(response_text)

        except (KeyboardInterrupt, EOFError):
            print("\nДо связи!")
            break

if __name__ == "__main__":
    main()
