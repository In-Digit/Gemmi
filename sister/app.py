# /home/mrak/gemini_companion/sister/app.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GUI «Старшей сестры» на Streamlit.
Одноэкранный широкий чат с поддержкой выбора файлов проекта,
умной фильтрацией мусора в дереве, автоподхватом BOARD.md,
детальной статистикой токенов и менеджером именованных воркспейсов.
"""

import os
import re
import sys
import json
import socket
import subprocess
from datetime import datetime
import streamlit as st

# Пути
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_STATE_FILE = os.path.expanduser("~/gemini_companion/data/sister_project_state.json")
WORKSPACES_DIR = os.path.expanduser("~/gemini_companion/data/workspaces/")

# Добавляем пути для импорта
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, ".."))  # для импорта modules

from context_manager_sister import SisterContextManager
from llm_client_sister import ask_gemini_sister
from modules.web_search import search_web, fetch_page_content

# --- Инициализация страницы ---
st.set_page_config(page_title="Старшая сестра", page_icon="👩‍💻", layout="wide")

if "sister_ctx" not in st.session_state:
    st.session_state.sister_ctx = SisterContextManager()
if "use_pro" not in st.session_state:
    st.session_state.use_pro = False
if "trigger_send" not in st.session_state:
    st.session_state.trigger_send = None

ctx = st.session_state.sister_ctx


def is_jemi_online(port: int = 8501) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex(("localhost", port)) == 0
    except Exception:
        return False


def load_project_state():
    if os.path.exists(PROJECT_STATE_FILE):
        try:
            with open(PROJECT_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "project_folders": ["", "", "", "", ""],
        "tree_loaded": False,
        "selected_paths": []
    }


def save_project_state():
    os.makedirs(os.path.dirname(PROJECT_STATE_FILE), exist_ok=True)
    state = {
        "project_folders": st.session_state.project_folders,
        "tree_loaded": st.session_state.tree_loaded,
        "selected_paths": [f["path"] for f in st.session_state.selected_files]
    }
    try:
        with open(PROJECT_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Sister] Ошибка сохранения состояния проекта: {e}")


# --- Функции управления воркспейсами ---
def get_saved_workspaces() -> list:
    os.makedirs(WORKSPACES_DIR, exist_ok=True)
    return [f[:-5] for f in os.listdir(WORKSPACES_DIR) if f.endswith(".json")]


def save_named_workspace(name: str):
    if not name.strip():
        return
    os.makedirs(WORKSPACES_DIR, exist_ok=True)
    ws_path = os.path.join(WORKSPACES_DIR, f"{name.strip()}.json")
    state = {
        "project_folders": st.session_state.project_folders,
        "tree_loaded": st.session_state.tree_loaded,
        "selected_paths": [f["path"] for f in st.session_state.selected_files]
    }
    try:
        with open(ws_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Sister] Ошибка сохранения воркспейса {name}: {e}")


def load_named_workspace(name: str):
    ws_path = os.path.join(WORKSPACES_DIR, f"{name}.json")
    if os.path.exists(ws_path):
        try:
            with open(ws_path, "r", encoding="utf-8") as f:
                state = json.load(f)

            # 1. Полный сброс текущего контекста (чистый лист перед загрузкой)
            ctx.clear_all()
            st.session_state.selected_files = []

            # 2. Накатываем новый пресет
            st.session_state.project_folders = state.get("project_folders", ["", "", "", "", ""])
            st.session_state.tree_loaded = state.get("tree_loaded", False)

            saved_paths = state.get("selected_paths", [])
            for p in saved_paths:
                if os.path.exists(p):
                    try:
                        with open(p, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        st.session_state.selected_files.append({"path": p, "content": content})
                    except Exception:
                        pass

            save_project_state()
            return True
        except Exception as e:
            print(f"[Sister] Ошибка загрузки воркспейса {name}: {e}")
    return False


saved_state = load_project_state()
if "project_folders" not in st.session_state:
    st.session_state.project_folders = saved_state.get("project_folders", ["", "", "", "", ""])
if "tree_loaded" not in st.session_state:
    st.session_state.tree_loaded = saved_state.get("tree_loaded", False)

if "selected_files" not in st.session_state:
    st.session_state.selected_files = []
    saved_paths = saved_state.get("selected_paths", [])
    for p in saved_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                st.session_state.selected_files.append({"path": p, "content": content})
            except Exception:
                pass

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
</style>
""", unsafe_allow_html=True)


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


def run_sister_tool_loop(initial_payload):
    first_response, first_pro_fallback = ask_gemini_sister(initial_payload, use_pro_only=st.session_state.use_pro)

    pro_state_changed = first_pro_fallback
    if first_pro_fallback and st.session_state.use_pro:
        st.session_state.use_pro = False

    if first_response.startswith("Ошибка:"):
        return False, first_response, [], pro_state_changed

    web_results = execute_web_blocks(first_response)
    if not web_results:
        return True, first_response, [], pro_state_changed

    tool_logs = [item["log"] for item in web_results]
    tool_feedback = ["--- ДАННЫЕ ИЗ СЕТИ (ПОЛУЧЕНЫ ТОЛЬКО ЧТО) ---"]
    for item in web_results:
        if item["type"] == "web_search":
            tool_feedback.append(f"Поиск по теме `{item['query']}`:\n{item['output']}")
        elif item["type"] == "web_fetch":
            tool_feedback.append(f"Текст со страницы `{item['query']}`:\n{item['output']}")

    tool_feedback.append(
        "----------------------------------------------------\n"
        "Инструкция: На основе найденных данных дай точный, экспертный и развернутый ответ Алексею.\n"
        "Не выводи блоки web_search и не упоминай техническую кухню поиска."
    )

    second_payload = json.loads(json.dumps(initial_payload))
    second_payload["contents"].append({"role": "model", "parts": [{"text": first_response}]})
    second_payload["contents"].append({"role": "user", "parts": [{"text": "\n\n".join(tool_feedback)}]})

    final_response, second_pro_fallback = ask_gemini_sister(second_payload, use_pro_only=st.session_state.use_pro)
    if second_pro_fallback:
        pro_state_changed = True

    if final_response.startswith("Ошибка:"):
        return True, first_response, tool_logs, pro_state_changed

    return True, final_response, tool_logs, pro_state_changed


# --- Левая панель ---
with st.sidebar:
    st.header("👩‍💻 Старшая сестра")
    st.caption("Среда разработки и анализа")

    if is_jemi_online(8501):
        st.success("✨ Джеми: онлайн 🟢")
    else:
        st.info("✨ Джеми: не запущена ⚪")

    # Индикатор здоровья контекста
    health = ctx.get_context_health(
        selected_files=st.session_state.selected_files,
        project_folders=st.session_state.project_folders
    )
    st.markdown("---")
    st.caption(f"Тонус контекста: **{health['status']}**")
    st.progress(health['percentage'] / 100)
    st.text(f"Токены: ~{health['tokens']:,} / {health['max_tokens']:,}")

    # Детализация токенов по компонентам
    breakdown = ctx.get_token_breakdown(
        selected_files=st.session_state.selected_files,
        project_folders=st.session_state.project_folders
    )
    with st.expander("📊 Детализация контекста", expanded=False):
        st.text(f"• Системный промпт: ~{breakdown['system']:,}")
        st.text(f"• Паспорт BOARD.md: ~{breakdown['board']:,}")
        st.text(f"• Выбранные файлы: ~{breakdown['files']:,}")
        st.text(f"• История чата:   ~{breakdown['history']:,}")

    st.markdown("---")
    st.session_state.use_pro = st.toggle("⚡ Использовать только PRO модели", value=st.session_state.use_pro)

    st.markdown("---")
    st.header("🗂️ Воркспейсы")
    saved_ws = get_saved_workspaces()
    if saved_ws:
        selected_ws = st.selectbox("Загрузить проект", options=["-- выберите --"] + saved_ws, index=0)
        if selected_ws != "-- выберите --":
            if st.button("📂 Открыть воркспейс", use_container_width=True):
                if load_named_workspace(selected_ws):
                    st.success(f"Воркспейс '{selected_ws}' загружен!")
                    st.rerun()

    ws_name_input = st.text_input("Имя воркспейса", placeholder="например: esp32_sensor")
    if st.button("💾 Сохранить воркспейс", use_container_width=True):
        if ws_name_input.strip():
            save_named_workspace(ws_name_input)
            st.success(f"Сохранено как '{ws_name_input.strip()}'")
            st.rerun()
        else:
            st.warning("Введите имя воркспейса.")

    st.markdown("---")
    st.header("📁 Текущий проект")
    for i in range(5):
        val = st.text_input(
            f"Папка {i+1}",
            value=st.session_state.project_folders[i],
            key=f"folder_input_{i}"
        )
        if val != st.session_state.project_folders[i]:
            st.session_state.project_folders[i] = val
            save_project_state()

    if st.button("Загрузить дерево", use_container_width=True):
        st.session_state.tree_loaded = True
        save_project_state()

    non_empty_folders = [f for f in st.session_state.project_folders if f]
    active_folder = st.selectbox("Выберите папку", options=non_empty_folders, index=0) if non_empty_folders else None

    if active_folder:
        boards_in_active = ctx.find_board_files(project_folders=[active_folder])
        if boards_in_active:
            st.caption(f"📋 **BOARD.md**: подключён 🟢 (`{boards_in_active[0]['filename']}`)")
        else:
            st.caption("📋 **BOARD.md**: не найден ⚪")

    if active_folder and st.session_state.get("tree_loaded", False):
        st.subheader("Дерево файлов")
        current_selected_paths = [f["path"] for f in st.session_state.selected_files]

        IGNORED_DIRS = {".git", "build", "managed_components", "__pycache__", ".vscode", ".idea", "venv", "env", "node_modules"}
        IGNORED_EXTS = {".o", ".bin", ".elf", ".map", ".a", ".pyc", ".zip", ".tar", ".gz"}

        def show_tree(folder):
            try:
                items = sorted(os.listdir(folder))
            except Exception:
                return
            for item in items:
                if item in IGNORED_DIRS or item.startswith('.'):
                    continue
                full_path = os.path.join(folder, item)
                if os.path.isdir(full_path):
                    with st.expander(item):
                        show_tree(full_path)
                else:
                    ext = os.path.splitext(item)[1].lower()
                    if ext in IGNORED_EXTS:
                        continue
                    is_checked = full_path in current_selected_paths
                    checked = st.checkbox(item, value=is_checked, key=full_path)
                    if checked and not is_checked:
                        try:
                            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                                content = f.read()
                            st.session_state.selected_files.append({"path": full_path, "content": content})
                            save_project_state()
                        except Exception as e:
                            st.error(f"Ошибка чтения {full_path}: {e}")
                    elif not checked and is_checked:
                        st.session_state.selected_files = [
                            f for f in st.session_state.selected_files if f["path"] != full_path
                        ]
                        save_project_state()

        show_tree(active_folder)

    st.markdown("---")
    st.write("Выбрано файлов:", len(st.session_state.selected_files))

    st.markdown("---")
    if st.button("🧹 Очистить историю", use_container_width=True):
        ctx.clear_all()
        st.success("История очищена")
        st.rerun()

    if st.button("📝 Новый чат (с резюме)", use_container_width=True):
        if ctx.history:
            with st.spinner("Формирую резюме текущего сеанса..."):
                history_text = "\n".join([
                    f"{'Пользователь' if msg['role']=='user' else 'Ассистент'}: {msg['parts'][0].get('text','')}"
                    for msg in ctx.history
                ])
                prompt = (
                    "Подробно резюмируй, что мы сделали на данный момент, какие решения приняты, "
                    "что запланировано дальше. Сохрани все важные технические детали, названия файлов, "
                    "функции и переменные, необходимые для продолжения работы.\n\n"
                    f"История диалога:\n{history_text}"
                )
                summary, _ = ask_gemini_sister(prompt, use_pro_only=st.session_state.use_pro)
                if summary.startswith("Ошибка:"):
                    st.error(summary)
                else:
                    ctx.clear_all()
                    ctx.add_user_message(f"Резюме предыдущего чата:\n{summary}")
                    st.success("Создан новый чат с переносом контекста")
                    st.rerun()
        else:
            st.info("История пуста")

    if st.button("🆕 Новый проект", use_container_width=True):
        ctx.clear_all()
        st.session_state.selected_files = []
        st.session_state.project_folders = ["", "", "", "", ""]
        st.session_state.tree_loaded = False
        if os.path.exists(PROJECT_STATE_FILE):
            os.remove(PROJECT_STATE_FILE)
        st.success("Проект сброшен")
        st.rerun()

    st.markdown("---")
    if st.button("🛑 Завершить сестру", use_container_width=True):
        os._exit(0)


# --- Правая часть: широкий чистый чат ---
st.header("👩‍💻 Старшая сестра — Разработка")

for idx, msg in enumerate(ctx.history):
    role = "user" if msg["role"] == "user" else "assistant"
    text = msg["parts"][0].get("text", "")
    with st.chat_message(role):
        st.markdown(text)

        if idx == len(ctx.history) - 1 and role == "user":
            st.markdown("---")
            col_retry, col_del, _ = st.columns([1, 1, 4])
            with col_retry:
                if st.button("🔄 Повторить", key=f"retry_{idx}"):
                    st.session_state.trigger_send = text
                    st.rerun()
            with col_del:
                if st.button("🗑️ Удалить", key=f"del_{idx}"):
                    ctx.history.pop()
                    ctx.save_history()
                    st.rerun()

        elif idx == len(ctx.history) - 1 and role == "assistant":
            if "давай код" in text.lower() or "если готов посмотреть на изменения" in text.lower():
                st.markdown("")
                if st.button("🚀 Давай код!", key=f"give_code_{idx}", type="primary"):
                    st.session_state.trigger_send = "давай код"
                    st.rerun()


chat_input = st.chat_input("Запрос к Старшей сестре...")
user_input = chat_input or st.session_state.trigger_send

if st.session_state.trigger_send:
    st.session_state.trigger_send = None

if user_input:
    is_retry_action = (chat_input is None and ctx.history and ctx.history[-1]["role"] == "user")

    if not is_retry_action:
        ctx.add_user_message(user_input)

    payload = ctx.get_payload(
        selected_files=st.session_state.selected_files,
        project_folders=st.session_state.project_folders
    )

    with st.chat_message("assistant"):
        status_placeholder = st.empty()
        status_placeholder.markdown("🔄 Думаю...")

        success, response, tool_logs, pro_changed = run_sister_tool_loop(payload)
        status_placeholder.empty()

        if pro_changed:
            st.warning("⚠️ PRO-модели не ответили. Режим «Только PRO» автоматически отключен, задействован стандартный пул моделей.")

        if tool_logs:
            with st.expander("🛠️ Использованные источники (поиск)", expanded=False):
                for log in tool_logs:
                    st.markdown(log)

        if not success and response.startswith("Ошибка:"):
            st.error(response)
        else:
            st.markdown(response)
            ctx.add_model_message(response)
            st.rerun()
