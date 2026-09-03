import json
import os
from datetime import datetime, timedelta

DATA_DIR = os.path.expanduser("~/gemini_companion/data")
INTERACTION_FILE = os.path.join(DATA_DIR, "interaction_state.json")
SNAPSHOT_FILE = os.path.join(DATA_DIR, "instant_snapshot.json")

# Порог молчания в секундах (2.5 часа = 9000 сек)
IDLE_THRESHOLD_SECONDS = 9000

def load_json(filepath, default):
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(filepath, data):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def record_user_interaction():
    """Фиксация любого твоего обращения к системе."""
    state = {
        "last_interaction": datetime.now().isoformat(),
        "prompt_status": "idle",
        "last_prompt_time": None
    }
    save_json(INTERACTION_FILE, state)
    return state

def check_proactive_trigger():
    snapshot = load_json(SNAPSHOT_FILE, {})
    default_state = {
        "last_interaction": datetime.now().isoformat(),
        "prompt_status": "idle",
        "last_prompt_time": None
    }
    state = load_json(INTERACTION_FILE, default_state)

    try:
        last_inter = datetime.fromisoformat(state["last_interaction"])
    except (ValueError, KeyError):
        last_inter = datetime.now()

    now = datetime.now()
    idle_duration = (now - last_inter).total_seconds()

    # Если вопрос был задан, но ответа не последовало в течение 15 минут — уходим в "молчаливый отказ"
    if state.get("prompt_status") == "awaiting_response":
        try:
            last_prompt = datetime.fromisoformat(state["last_prompt_time"])
            if (now - last_prompt).total_seconds() > 900:
                state["prompt_status"] = "ignored"
                save_json(INTERACTION_FILE, state)
        except (ValueError, TypeError):
            pass

    # Условия для оклика:
    # 1. Прошло 2.5+ часа
    # 2. Статус idle (еще не спрашивали и не было отказа)
    if idle_duration >= IDLE_THRESHOLD_SECONDS and state.get("prompt_status") == "idle":
        state["prompt_status"] = "awaiting_response"
        state["last_prompt_time"] = now.isoformat()
        save_json(INTERACTION_FILE, state)
        return {
            "trigger": True,
            "action": "speak",
            "message": "Поговорим?",
            "reason": f"Тишина {round(idle_duration / 3600, 1)} ч."
        }

    return {
        "trigger": False,
        "idle_hours": round(idle_duration / 3600, 2),
        "status": state.get("prompt_status", "idle")
    }

if __name__ == "__main__":
    if not os.path.exists(INTERACTION_FILE):
        record_user_interaction()
        print("Создано начальное состояние взаимодействия.")

    res = check_proactive_trigger()
    print("Результат проверки триггера:")
    print(json.dumps(res, ensure_ascii=False, indent=2))
