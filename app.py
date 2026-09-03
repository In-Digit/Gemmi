import os
import sys
import streamlit as st

# Добавляем корневую директорию проекта в sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.context_manager import ContextManager
from modules.llm_client import ask_gemini

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

# --- Инициализация менеджера контекста в сессии ---
if "context_manager" not in st.session_state:
    st.session_state.context_manager = ContextManager()

ctx = st.session_state.context_manager

# --- Боковая панель ---
st.sidebar.title("✨ Джеми")
st.sidebar.caption("Персональный ассистент")
st.sidebar.markdown("---")

if st.sidebar.button("🧹 Очистить диалог", use_container_width=True):
    ctx.clear_all()
    st.rerun()

st.sidebar.markdown("---")

if st.sidebar.button("🛑 Завершить работу", use_container_width=True):
    st.sidebar.write("Завершаю работу...")
    os.system("pkill -f run_widget.py")

# --- Отображение истории сообщений ---
for message in ctx.history:
    role = "user" if message.get("role") == "user" else "assistant"
    parts = message.get("parts", [])
    text = ""
    if parts and isinstance(parts[0], dict):
        text = parts[0].get("text", "")

    if text:
        with st.chat_message(role):
            st.markdown(text)

# --- Ввод пользователя и генерация ответа ---
if user_input := st.chat_input("Напиши мне..."):
    # Отображаем сообщение пользователя в чате
    with st.chat_message("user"):
        st.markdown(user_input)

    # Фиксируем в ContextManager и формируем payload
    ctx.add_user_message(user_input)
    payload = ctx.get_payload()

    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        with st.spinner("Думаю..."):
            response_text = ask_gemini(payload)

        if response_text.startswith("Ошибка:"):
            message_placeholder.error(response_text)
        else:
            message_placeholder.markdown(response_text)
            ctx.add_model_message(response_text)
