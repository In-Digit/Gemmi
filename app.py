#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Графический интерфейс ассистента «Джеми» на Streamlit.
Обеспечивает:
- отображение диалога (с возможностью сворачивания старых сообщений);
- отправку запросов модели с индикацией прогресса;
- обработку проактивных сообщений и реакций пользователя;
- автоматическую повторную отправку неотвеченного последнего сообщения;
- выполнение команд exec_bash и web_search;
- ручной запуск анализа фактов о пользователе;
- фоновую проверку новых проактивных сообщений через JavaScript-поллинг;
- приватный режим «Мы не одни»;
- flash-режим (промпт начинается с **) — запрос без контекста, ответ не сохраняется.
"""

import os
import re
import sys
import json
import time
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
        .proactive-message {
            background-color: #2a2f3a;
            border-left: 4px solid #6c5ce7;
            padding: 8px 12px;
            border-radius: 6px;
            margin: 4px 0;
        }
        .proactive-label {
            color: #a29bfe;
            font-size: 0.8em;
            font-weight: bold;
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
if "show_full_history" not in st.session_state:
    st.session_state.show_full_history = False
if "auto_resend_attempted" not in st.session_state:
    st.session_state.auto_resend_attempted = False
if "private_mode" not in st.session_state:
    st.session_state.private_mode = False

ctx = st.session_state.context_manager
executor = st.session_state.bash_executor
facts_manager = st.session_state.user_facts_manager

# --- Файлы для обмена с Proactive Engine ---
PROACTIVE_QUEUE_FILE = os.path.expanduser("~/gemini_companion/data/proactive_queue.json")
PROACTIVE_FLAG_FILE = os.path.expanduser("~/gemini_companion/data/proactive_pending_flag.json")
PROACTIVE_REACTION_FILE = os.path.expanduser("~/gemini_companion/data/proactive_reaction.json")


# --- Вспомогательные функции ---

def generate_summary_from_history(history):
    """Создает краткую выжимку из истории диалога (для сжатия при простое)."""
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
    """Читает proactive_queue.json, добавляет сообщения в историю и очищает файл.
    Устанавливает флаг ожидания реакции."""
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
    """Классифицирует ответ пользователя на проактивное сообщение и записывает реакцию."""
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
    """Извлекает и выполняет блоки exec_bash, возвращает дополнительные результаты."""
    bash_blocks = re.findall(r"```exec_bash\s*\n(.*?)\n```", response_text, re.DOTALL)
    exec_results = []
    for cmd in bash_blocks:
        success, result = executor.execute(cmd)
        status_icon = "✅" if success else "❌"
        exec_results.append(f"\n\n{status_icon} **[Выполнение команды]** `{cmd}`:\n```\n{result}\n```")
    return exec_results


def execute_web_blocks(response_text):
    """Извлекает и выполняет блоки web_search, возвращает дополнительные результаты."""
    web_blocks = re.findall(r"```web_search\s*\n(.*?)\n```", response_text, re.DOTALL)
    exec_results = []
    for query in web_blocks:
        query = query.strip()
        if query.startswith("http://") or query.startswith("https://"):
            page_text = fetch_page_content(query)
            exec_results.append(f"\n\n🌐 **[Чтение URL]** `{query}`:\n```text\n{page_text}\n```")
        else:
            search_results = search_web(query)
            if isinstance(search_results, list) and len(search_results) > 0 and "error" not in search_results[0]:
                formatted_search = "\n".join([f"- **{r.get('title', 'Без названия')}**: {r.get('body', '')} ({r.get('href', '')})" for r in search_results])
                exec_results.append(f"\n\n🔍 **[Поиск в сети]** `{query}`:\n{formatted_search}")
            else:
                err_msg = search_results[0].get("error", "Ничего не найдено") if search_results else "Ничего не найдено"
                exec_results.append(f"\n\n⚠️ **[Ошибка поиска]** `{query}`:\n{err_msg}")
    return exec_results


# --- Проверяем очередь и таймаут перед отображением ---
process_proactive_queue()
ctx.check_timeout(summarize_func=generate_summary_from_history)

# --- Автоматическая повторная отправка последнего сообщения, если нет ответа ---
if not st.session_state.auto_resend_attempted and ctx.history and ctx.history[-1]["role"] == "user":
    st.session_state.auto_resend_attempted = True
    last_user_text = ctx.history[-1]["parts"][0].get("text", "")
    if last_user_text:
        payload = ctx.get_payload(private_mode=st.session_state.private_mode)
        with st.chat_message("assistant"):
            status_placeholder = st.empty()
            message_placeholder = st.empty()
            def on_progress(model, status_text):
                status_placeholder.markdown(f"🔄 **Модель:** `{model}` — **Статус:** {status_text}")
            response_text = ask_gemini(payload, progress_callback=on_progress)
            status_placeholder.empty()
            if response_text.startswith("Ошибка:"):
                message_placeholder.error(response_text)
            else:
                exec_results = execute_bash_blocks(response_text) + execute_web_blocks(response_text)
                if exec_results:
                    response_text += "".join(exec_results)
                message_placeholder.markdown(response_text)
                ctx.add_model_message(response_text)
        st.rerun()

# --- Боковая панель ---
st.sidebar.title("✨ Джеми")
st.sidebar.caption("Персональный ассистент")
st.sidebar.markdown("---")

health = ctx.get_context_health()
st.sidebar.caption(f"Статус: **{health['status']}**")
st.sidebar.progress(health['percentage'] / 100)
st.sidebar.text(f"Токены: ~{health['tokens']:,} / {health['max_tokens']:,}")
st.sidebar.markdown("---")

# Кнопка приватного режима
private_button_label = "👥 Мы не одни" if not st.session_state.private_mode else "👤 Мы одни"
if st.sidebar.button(private_button_label, use_container_width=True):
    st.session_state.private_mode = not st.session_state.private_mode
    st.rerun()

# Кнопка переключения отображения истории
history_button_label = "📜 Показать всю историю" if not st.session_state.show_full_history else "📜 Скрыть историю"
if st.sidebar.button(history_button_label, use_container_width=True):
    st.session_state.show_full_history = not st.session_state.show_full_history
    st.rerun()

st.sidebar.markdown("---")

if st.sidebar.button("👩‍💻 Старшая сестра", use_container_width=True):
    st.sidebar.info("Модуль кодера готовится к запуску...")

if st.sidebar.button("🧠 Анализировать факты о пользователе", use_container_width=True):
    with st.spinner("Анализирую историю и извлекаю факты..."):
        facts_manager.run_analysis_if_needed(force=True)
    st.success("Анализ завершён. Факты обновлены в профиле.")
    st.rerun()

if st.sidebar.button("🧹 Очистить диалог", use_container_width=True):
    ctx.clear_all()
    st.rerun()

if st.sidebar.button("🛑 Завершить работу", use_container_width=True):
    st.sidebar.write("Завершаю работу...")
    os.system("pkill -f run_widget.py")

# --- Отображение истории сообщений (с учётом приватного режима и сворачивания) ---
if st.session_state.private_mode:
    # Показываем только два последних сообщения: вопрос пользователя и ответ ассистента
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

# --- Ввод пользователя ---
user_input = st.chat_input("Напиши мне...")
if user_input:
    handle_proactive_reaction(user_input)

# Обработка механизма переотправки при сбое
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
    # --- Flash-режим: если промпт начинается с ** ---
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
            # Не сохраняем в историю, завершаем обработку
            st.stop()
        else:
            st.warning("Пустой flash-запрос. Введите текст после **.")

    # --- Обычная обработка ---
    with st.chat_message("user"):
        st.markdown(user_input)

    # Fast-Path
    cmd_key = user_input.lower().strip()
    if cmd_key in executor.presets:
        success, result = executor.execute(cmd_key)
        response_text = f"⚙️ **[Fast-Path]**\n```\n{result}\n```"
        ctx.add_user_message(user_input)
        ctx.add_model_message(response_text)
        with st.chat_message("assistant"):
            st.markdown(response_text)
        st.rerun()

    # Обычная обработка через LLM
    ctx.add_user_message(user_input)
    payload = ctx.get_payload(private_mode=st.session_state.private_mode)

    with st.chat_message("assistant"):
        status_placeholder = st.empty()
        message_placeholder = st.empty()
        def on_progress(model, status_text):
            status_placeholder.markdown(f"🔄 **Модель:** `{model}` — **Статус:** {status_text}")
        response_text = ask_gemini(payload, progress_callback=on_progress)
        status_placeholder.empty()

        if response_text.startswith("Ошибка:"):
            message_placeholder.error(response_text)
            # Откатываем последнее user-сообщение, чтобы не было двух подряд
            if ctx.history and ctx.history[-1]["role"] == "user":
                ctx.history.pop()
                ctx.save_history()
            st.session_state.failed_prompt = user_input
            st.rerun()
        else:
            exec_results = execute_bash_blocks(response_text) + execute_web_blocks(response_text)
            if exec_results:
                response_text += "".join(exec_results)
            message_placeholder.markdown(response_text)
            ctx.add_model_message(response_text)

# --- JavaScript-поллинг: проверка новых проактивных сообщений ---
js_polling = """
<script>
function checkProactiveStatus() {
    fetch('http://localhost:8765/proactive_status')
        .then(response => response.json())
        .then(data => {
            if (data.has_new) {
                window.location.reload();
            }
        })
        .catch(error => console.log('Ошибка проверки статуса:', error));
}
setInterval(checkProactiveStatus, 60000);
checkProactiveStatus();
</script>
"""
components.html(js_polling, height=0, width=0)
