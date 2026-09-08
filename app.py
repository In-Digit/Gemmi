# /home/mrak/gemini_companion/app.py
import os
import re
import sys
import json
import time
import socket
import subprocess
from datetime import datetime
import streamlit as st
import streamlit.components.v1 as components

# Добавляем корневую директорию проекта в sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.context_manager import ContextManager
from modules.llm_client import ask_gemini
from modules.bash_executor import BashExecutor
from modules.web_search import search_web, fetch_page_content
from modules.user_facts_manager import UserFactsManager
from modules.media_player import MediaPlayer

# --- Конфигурация страницы и стили ---
st.set_page_config(page_title="Джеми", page_icon="✨", layout="wide")

st.markdown("""
    <style>
        .stApp {
            background-color: #0e1117;
            color: #e0e0e0;
        }
        .stChatMessage {
            background-color: #1a1f29;
            border-radius: 10px;
            padding: 12px;
            margin-bottom: 10px;
            border: 1px solid #2d3748;
        }
        header, footer { visibility: hidden; }
        .stButton>button {
            border-radius: 8px;
            background-color: #2d3748;
            color: #ffffff;
            border: 1px solid #4a5568;
        }
        .stButton>button:hover {
            background-color: #4a5568;
            border-color: #718096;
        }
    </style>
""", unsafe_allow_html=True)

# --- Инициализация менеджеров в сессии ---
if "context_manager" not in st.session_state:
    st.session_state.context_manager = ContextManager()
if "bash_executor" not in st.session_state:
    st.session_state.bash_executor = BashExecutor()
if "user_facts_manager" not in st.session_state:
    st.session_state.user_facts_manager = UserFactsManager()
if "media_player" not in st.session_state:
    st.session_state.media_player = MediaPlayer()
if "show_full_history" not in st.session_state:
    st.session_state.show_full_history = False
if "auto_resend_attempted" not in st.session_state:
    st.session_state.auto_resend_attempted = False
if "private_mode" not in st.session_state:
    st.session_state.private_mode = False
if "show_debug_logs" not in st.session_state:
    st.session_state.show_debug_logs = False

ctx = st.session_state.context_manager
executor = st.session_state.bash_executor
facts_manager = st.session_state.user_facts_manager
media = st.session_state.media_player

# --- Пути к файлам обмена ---
PROACTIVE_QUEUE_FILE = os.path.expanduser("~/gemini_companion/data/proactive_queue.json")
PROACTIVE_FLAG_FILE = os.path.expanduser("~/gemini_companion/data/proactive_pending_flag.json")
PROACTIVE_REACTION_FILE = os.path.expanduser("~/gemini_companion/data/proactive_reaction.json")
INTERACTION_FILE = os.path.expanduser("~/gemini_companion/data/interaction_state.json")
HISTORY_FILE_PATH = os.path.expanduser("~/gemini_companion/history.json")

VOICE_CONFIG_FILE = os.path.expanduser("~/gemini_companion/config/voice_config.json")
VOICE_INPUT_FILE = os.path.expanduser("~/gemini_companion/data/voice_input.json")
VOICE_RESPONSE_FILE = os.path.expanduser("~/gemini_companion/data/voice_response.json")
VOICE_ERROR_FILE = os.path.expanduser("~/gemini_companion/data/voice_error.json")


# --- Вспомогательные функции ---

def get_local_ip() -> str:
    """Определяет актуальный локальный IP-адрес машины в сети."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def is_port_open(port: int) -> bool:
    """Проверяет, слушается ли указанный TCP-порт на localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('localhost', port)) == 0


def touch_user_interaction():
    """Обновляет время взаимодействия пользователя для предотвращения ложных проактивных срабатываний."""
    state = {
        "last_interaction": datetime.now().isoformat(),
        "prompt_status": "idle",
        "last_prompt_time": None
    }
    try:
        os.makedirs(os.path.dirname(INTERACTION_FILE), exist_ok=True)
        with open(INTERACTION_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения interaction_state: {e}")


def load_voice_config_ui() -> dict:
    if os.path.exists(VOICE_CONFIG_FILE):
        try:
            with open(VOICE_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"mode": "text_text"}


def save_voice_config_ui(cfg: dict):
    os.makedirs(os.path.dirname(VOICE_CONFIG_FILE), exist_ok=True)
    try:
        with open(VOICE_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения voice_config: {e}")


def check_voice_input_queue() -> str:
    """Проверяет, появился ли распознанный текст от голосового ассистента."""
    if os.path.exists(VOICE_INPUT_FILE):
        try:
            with open(VOICE_INPUT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            os.remove(VOICE_INPUT_FILE)
            return data.get("text", "").strip()
        except Exception:
            pass
    return ""


def send_to_tts_queue(text: str):
    """Отправляет текст ответа Джемми в очередь озвучивания."""
    try:
        os.makedirs(os.path.dirname(VOICE_RESPONSE_FILE), exist_ok=True)
        with open(VOICE_RESPONSE_FILE, "w", encoding="utf-8") as f:
            json.dump({"text": text, "timestamp": datetime.now().isoformat()}, f, ensure_ascii=False)
    except Exception as e:
        print(f"Ошибка записи в voice_response: {e}")


def generate_summary_from_history(history):
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


def process_proactive_queue():
    if not os.path.exists(PROACTIVE_QUEUE_FILE):
        return
    try:
        with open(PROACTIVE_QUEUE_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if not lines:
            return
        last_proactive_text = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                text = data.get("text", "")
                if text:
                    ctx.add_model_message(text)
                    last_proactive_text = text
            except json.JSONDecodeError:
                continue
        with open(PROACTIVE_QUEUE_FILE, "w", encoding="utf-8") as f:
            f.write("")
        if last_proactive_text:
            flag_data = {"timestamp": datetime.now().isoformat(), "text": last_proactive_text}
            with open(PROACTIVE_FLAG_FILE, "w", encoding="utf-8") as f:
                json.dump(flag_data, f, ensure_ascii=False)
    except Exception as e:
        print(f"Ошибка обработки proactive_queue: {e}")


def handle_proactive_reaction(user_input):
    if not os.path.exists(PROACTIVE_FLAG_FILE):
        return
    try:
        with open(PROACTIVE_FLAG_FILE, "r", encoding="utf-8") as f:
            flag = json.load(f)
        proactive_time = datetime.fromisoformat(flag["timestamp"])
        if (datetime.now() - proactive_time).total_seconds() > 300:
            os.remove(PROACTIVE_FLAG_FILE)
            return
        response = user_input.strip().lower()
        refusal_words = ["отстань", "не мешай", "занят", "потом", "не сейчас", "отвали"]
        if any(word in response for word in refusal_words):
            reaction = "negative_explicit"
        elif len(user_input.split()) > 10:
            reaction = "positive"
        else:
            reaction = "neutral"
        reaction_data = {"reaction": reaction, "timestamp": datetime.now().isoformat()}
        with open(PROACTIVE_REACTION_FILE, "w", encoding="utf-8") as f:
            json.dump(reaction_data, f, ensure_ascii=False)
        os.remove(PROACTIVE_FLAG_FILE)
    except Exception as e:
        print(f"Ошибка обработки проактивной реакции: {e}")


def execute_bash_blocks(response_text):
    bash_blocks = re.findall(r"```exec_bash\s*\n(.*?)\n```", response_text, re.DOTALL)
    results = []
    for cmd in bash_blocks:
        cmd_clean = cmd.strip()
        success, output = executor.execute(cmd_clean)
        status_icon = "✅" if success else "❌"
        results.append({
            "type": "bash",
            "command": cmd_clean,
            "success": success,
            "output": output,
            "log": f"{status_icon} **[Команда]** `{cmd_clean}`\n```\n{output}\n```"
        })
    return results


def execute_web_blocks(response_text):
    web_blocks = re.findall(r"```web_search\s*\n(.*?)\n```", response_text, re.DOTALL)
    results = []
    for query in web_blocks:
        query_clean = query.strip()
        if query_clean.startswith("http://") or query_clean.startswith("https://"):
            page_text = fetch_page_content(query_clean)
            results.append({
                "type": "web_fetch",
                "query": query_clean,
                "output": page_text,
                "log": f"🌐 **[Чтение URL]** `{query_clean}`\n```text\n{page_text[:1000]}...\n```"
            })
        else:
            search_data = search_web(query_clean)
            if isinstance(search_data, list) and len(search_data) > 0 and "error" not in search_data[0]:
                formatted_search = "\n".join([
                    f"- {r.get('title', 'Без названия')}: {r.get('body', '')}"
                    for r in search_data
                ])
                results.append({
                    "type": "web_search",
                    "query": query_clean,
                    "output": formatted_search,
                    "log": f"🔍 **[Поиск]** `{query_clean}` ({len(search_data)} рез.)"
                })
            else:
                err_msg = search_data[0].get("error", "Ничего не найдено") if search_data else "Ничего не найдено"
                results.append({
                    "type": "web_search",
                    "query": query_clean,
                    "output": err_msg,
                    "log": f"⚠️ **[Ошибка поиска]** `{query_clean}`: {err_msg}"
                })
    return results


def run_tool_loop(initial_payload, status_placeholder):
    def on_progress(model, status_text):
        status_placeholder.markdown(f"🔄 **Модель:** `{model}` — **Статус:** {status_text}")

    first_response = ask_gemini(initial_payload, progress_callback=on_progress)
    if first_response.startswith("Ошибка:"):
        status_placeholder.empty()
        return False, first_response, []

    tool_logs = []
    bash_results = execute_bash_blocks(first_response)
    web_results = execute_web_blocks(first_response)
    all_tools = bash_results + web_results

    if not all_tools:
        status_placeholder.empty()
        return True, first_response, []

    for item in all_tools:
        tool_logs.append(item["log"])

    tool_feedback = ["--- ДАННЫЕ ИЗ СЕТИ / СИСТЕМЫ (ПОЛУЧЕНЫ ТОЛЬКО ЧТО) ---"]
    for item in all_tools:
        if item["type"] == "bash":
            tool_feedback.append(f"Команда `{item['command']}` дала результат:\n{item['output']}")
        elif item["type"] == "web_search":
            tool_feedback.append(f"Поиск по теме `{item['query']}` нашел следующее:\n{item['output']}")
        elif item["type"] == "web_fetch":
            tool_feedback.append(f"Текст со страницы `{item['query']}`:\n{item['output']}")

    tool_feedback.append(
        "----------------------------------------------------\n"
        "ВАЖНЕЙШАЯ ИНСТРУКЦИЯ ПО ОТВЕТУ:\n"
        "Ты — Джемми, общаешься с Алексеем лично, тепло, по-свойски и с легким юмором.\n"
        "На основе данных выше ответь ему человеческим языком, словно ты сама это знаешь или только что выглянула в окно.\n"
        "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:\n"
        "- Отвечать сухой ссылкой на сайт или фразой 'вот ссылка'.\n"
        "- Выводить блоки кода exec_bash или web_search.\n"
        "- Писать техническим канцелярским языком.\n"
        "Если в данных есть градусы/ветер/осадки — назови их и прокомментируй по-живому."
    )

    second_payload = json.loads(json.dumps(initial_payload))
    second_payload["contents"].append({"role": "model", "parts": [{"text": first_response}]})
    second_payload["contents"].append({"role": "user", "parts": [{"text": "\n\n".join(tool_feedback)}]})

    status_placeholder.markdown("✨ **Джеми:** заглянула, сейчас скажу...")
    final_response = ask_gemini(second_payload, progress_callback=on_progress)
    status_placeholder.empty()

    if final_response.startswith("Ошибка:"):
        fallback_msg = "У меня почему-то сорвалась мысль, пока я смотрела... Спроси меня ещё разок, пожалуйста!"
        return False, fallback_msg, tool_logs

    return True, final_response, tool_logs


# --- Синхронизация и таймаут ---
process_proactive_queue()
ctx.check_timeout(summarize_func=generate_summary_from_history)

# Проверяем не пришел ли текст с голоса
voice_text = check_voice_input_queue()
if voice_text and not st.session_state.get("active_voice_input_processed"):
    st.session_state.active_voice_input_processed = True
    user_input = voice_text
else:
    user_input = None


# --- Автоматическая повторная отправка последнего сообщения ---
if not st.session_state.auto_resend_attempted and ctx.history and ctx.history[-1]["role"] == "user":
    st.session_state.auto_resend_attempted = True
    last_user_text = ctx.history[-1]["parts"][0].get("text", "")
    if last_user_text:
        payload = ctx.get_payload(private_mode=st.session_state.private_mode)
        with st.chat_message("assistant"):
            status_placeholder = st.empty()
            message_placeholder = st.empty()
            success, final_text, tool_logs = run_tool_loop(payload, status_placeholder)
            message_placeholder.markdown(final_text)
            if tool_logs and st.session_state.show_debug_logs:
                with st.expander("🛠️ Под капотом (инструменты)", expanded=False):
                    for log in tool_logs:
                        st.markdown(log)
            if success:
                ctx.add_model_message(final_text)
                cfg = load_voice_config_ui()
                if cfg.get("mode") in ["text_voice", "voice_voice"]:
                    send_to_tts_queue(final_text)
        st.rerun()


# --- Боковая панель ---
st.sidebar.title("✨ Джеми")
current_ip = get_local_ip()
st.sidebar.code(f"http://{current_ip}:8501", language="text")

if st.sidebar.button("🔄 Обновить чат", use_container_width=True):
    ctx.sync_if_modified()
    st.rerun()

st.sidebar.markdown("---")

# --- Блок гибридного медиаплеера ---
st.sidebar.subheader("🎵 Плеер")
player_status = media.get_status()
state_icon = "⏸️" if player_status["paused"] else ("▶️" if player_status["active"] else "⏹️")
st.sidebar.caption(f"{state_icon} **{player_status['source']}**\n\n_{player_status['track']}_")

col_prev, col_play, col_next, col_stop = st.sidebar.columns(4)
with col_prev:
    if st.button("⏮️", use_container_width=True, help="Предыдущий трек"):
        media.prev_track()
        st.rerun()
with col_play:
    if st.button("⏯️", use_container_width=True, help="Воспроизведение / Пауза"):
        media.play_pause()
        st.rerun()
with col_next:
    if st.button("⏭️", use_container_width=True, help="Следующий трек"):
        media.next_track()
        st.rerun()
with col_stop:
    if st.button("⏹️", use_container_width=True, help="Стоп"):
        media.stop_all()
        st.rerun()

# Быстрый поиск и запуск YouTube Music
yt_query = st.sidebar.text_input("YouTube Music поиск", placeholder="Название трека...")
if st.sidebar.button("▶️ Включить трек", use_container_width=True):
    if yt_query.strip():
        with st.spinner("Ищу и запускаю..."):
            res = media.search_and_play_ytmusic(yt_query)
            if res.get("success"):
                st.sidebar.success(f"Играет: {res['artist']} — {res['title']}")
                time.sleep(1.0)
                st.rerun()
            else:
                st.sidebar.error(res.get("error", "Ошибка поиска"))

st.sidebar.markdown("---")

# --- Выпадающий список режимов голосового общения ---
st.sidebar.subheader("🎙️ Голосовой контур")
voice_cfg = load_voice_config_ui()
current_mode = voice_cfg.get("mode", "text_text")

mode_options = {
    "💬 Текст ⇄ 💬 Текст": "text_text",
    "💬 Текст ⇄ 🔊 Голос": "text_voice",
    "🎙️ Голос ⇄ 🔊 Голос": "voice_voice",
    "🎙️ Голос ⇄ 💬 Текст": "voice_text"
}

reverse_mode_options = {v: k for k, v in mode_options.items()}
selected_label = st.sidebar.selectbox(
    "Режим общения",
    options=list(mode_options.keys()),
    index=list(mode_options.values()).index(current_mode) if current_mode in mode_options.values() else 0
)

new_mode = mode_options[selected_label]
if new_mode != current_mode:
    voice_cfg["mode"] = new_mode
    save_voice_config_ui(voice_cfg)
    st.rerun()

st.sidebar.markdown("---")

health = ctx.get_context_health()
st.sidebar.caption(f"Статус: **{health['status']}**")
st.sidebar.progress(health['percentage'] / 100)
st.sidebar.text(f"Токены: ~{health['tokens']:,} / {health['max_tokens']:,}")
st.sidebar.markdown("---")

private_button_label = "👥 Мы не одни" if not st.session_state.private_mode else "👤 Мы одни"
if st.sidebar.button(private_button_label, use_container_width=True):
    st.session_state.private_mode = not st.session_state.private_mode
    st.rerun()

history_button_label = "📜 Показать всю историю" if not st.session_state.show_full_history else "📜 Скрыть историю"
if st.sidebar.button(history_button_label, use_container_width=True):
    st.session_state.show_full_history = not st.session_state.show_full_history
    st.rerun()

debug_button_label = "🔧 Скрыть тех-логи" if st.session_state.show_debug_logs else "🔧 Показать тех-логи"
if st.sidebar.button(debug_button_label, use_container_width=True):
    st.session_state.show_debug_logs = not st.session_state.show_debug_logs
    st.rerun()

st.sidebar.markdown("---")

if st.sidebar.button("👩‍💻 Старшая сестра", use_container_width=True):
    sister_url = "http://localhost:8502"
    if is_port_open(8502):
        st.sidebar.info("Сестра уже запущена. Открываю браузер...")
        subprocess.Popen(["xdg-open", sister_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        sister_app_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sister", "app.py")
        subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", sister_app_path, "--server.port", "8502", "--server.headless", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(1.5)
        subprocess.Popen(["xdg-open", sister_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        st.sidebar.success("Сестра запущена на http://localhost:8502")

if st.sidebar.button("🧠 Анализировать факты", use_container_width=True):
    with st.spinner("Анализирую историю..."):
        facts_manager.run_analysis_if_needed(force=True)
    st.success("Анализ завершён.")
    st.rerun()

if st.sidebar.button("🧹 Очистить диалог", use_container_width=True):
    ctx.clear_all()
    st.rerun()

if st.sidebar.button("🛑 Завершить работу", use_container_width=True):
    st.sidebar.write("Завершаю работу...")
    os.system("pkill -f run_widget.py")

# --- Отображение истории сообщений ---
if st.session_state.private_mode:
    messages_to_show = ctx.history[-2:]
else:
    messages_to_show = ctx.history if st.session_state.show_full_history else ctx.history[-10:]

for message in messages_to_show:
    role = "user" if message.get("role") == "user" else "assistant"
    parts = message.get("parts", [])
    text = ""
    if parts and isinstance(parts[0], dict):
        text = parts[0].get("text", "")
    if text:
        with st.chat_message(role):
            st.markdown(text)

# --- Уведомление об ошибке распознавания голоса (STT Error) ---
if os.path.exists(VOICE_ERROR_FILE):
    st.warning("⚠️ Голосовой ассистент не смог распознать последнюю реплику (шум или сбой сети).")
    if st.button("🗑️ Очистить ошибку"):
        try:
            os.remove(VOICE_ERROR_FILE)
            st.rerun()
        except Exception:
            pass

# --- Ввод пользователя (чат или голос) ---
chat_input = st.chat_input("Напиши мне...")
if chat_input:
    user_input = chat_input
    st.session_state.active_voice_input_processed = False
elif user_input:
    pass # сработало от voice_text выше

if user_input:
    touch_user_interaction()
    handle_proactive_reaction(user_input)

if "failed_prompt" in st.session_state:
    st.warning(f"⚠️ Ошибка сети. Не отправлено: {st.session_state.failed_prompt}")
    col1, col2, _ = st.columns([1, 1, 4])
    with col1:
        if st.button("🔄 Повторить", use_container_width=True):
            user_input = st.session_state.failed_prompt
            del st.session_state.failed_prompt
    with col2:
        if st.button("❌ Отменить", use_container_width=True):
            del st.session_state.failed_prompt
            st.rerun()

if user_input:
    touch_user_interaction()

    if user_input.startswith("**"):
        flash_prompt = user_input[2:].strip()
        if flash_prompt:
            with st.chat_message("user"):
                st.markdown(flash_prompt)
            payload = {
                "contents": [{"role": "user", "parts": [{"text": flash_prompt}]}]
            }
            with st.chat_message("assistant"):
                status_placeholder = st.empty()
                message_placeholder = st.empty()
                def on_progress(model, status_text):
                    status_placeholder.markdown(f"🔄 **Модель:** `{model}` — **Статус:** {status_text}")
                response = ask_gemini(payload, progress_callback=on_progress)
                status_placeholder.empty()
                if response.startswith("Ошибка:"):
                    message_placeholder.error(response)
                else:
                    message_placeholder.markdown(response)
            st.stop()

    with st.chat_message("user"):
        st.markdown(user_input)

    cmd_key = user_input.lower().strip()
    if cmd_key in executor.presets:
        success, result = executor.execute(cmd_key)
        response_text = f"⚙️ **[Fast-Path]**\n```\n{result}\n```"
        ctx.add_user_message(user_input)
        ctx.add_model_message(response_text)
        with st.chat_message("assistant"):
            st.markdown(response_text)
        st.rerun()

    ctx.add_user_message(user_input)
    payload = ctx.get_payload(private_mode=st.session_state.private_mode)

    with st.chat_message("assistant"):
        status_placeholder = st.empty()
        message_placeholder = st.empty()

        success, final_text, tool_logs = run_tool_loop(payload, status_placeholder)

        message_placeholder.markdown(final_text)

        if tool_logs and st.session_state.show_debug_logs:
            with st.expander("🛠️ Под капотом (инструменты)", expanded=False):
                for log in tool_logs:
                    st.markdown(log)

        if not success and final_text.startswith("Ошибка:"):
            if ctx.history and ctx.history[-1]["role"] == "user":
                ctx.history.pop()
                ctx.save_history()
            st.session_state.failed_prompt = user_input
            st.rerun()
        else:
            ctx.add_model_message(final_text)
            # Отправляем на озвучку, если разрешено настройками
            cfg = load_voice_config_ui()
            if cfg.get("mode") in ["text_voice", "voice_voice"]:
                send_to_tts_queue(final_text)

# --- Автообновление (синхронизация ноут/телефон/голос) ---
initial_mtime = os.path.getmtime(HISTORY_FILE_PATH) if os.path.exists(HISTORY_FILE_PATH) else 0.0

js_polling = f"""
<script>
(function() {{
    const topWin = window.parent || window;
    if (typeof topWin._jemi_mtime === 'undefined') {{
        topWin._jemi_mtime = {initial_mtime};
    }}

    function checkSyncStatus() {{
        const host = topWin.location.hostname || window.location.hostname;
        fetch('http://' + host + ':8765/proactive_status')
            .then(response => response.json())
            .then(data => {{{{
                if (data.has_new || (data.history_mtime && data.history_mtime > topWin._jemic_mtime)) {{{{
                    if (data.history_mtime) {{{{
                        topWin._jemi_mtime = data.history_mtime;
                    }}}}
                    topWin.location.reload();
                }}}}
            }}}})
            .catch(error => console.log('Sync polling error:', error));
    }}

    if (!topWin._jemi_poll_interval) {{
        topWin._jemi_poll_interval = setInterval(checkSyncStatus, 3000);
    }}
}})();
</script>
"""
components.html(js_polling, height=0, width=0)
