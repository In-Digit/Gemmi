#!/bin/bash
# Запуск сервера Streamlit в фоновом режиме
streamlit run ~/gemini_companion/app.py --server.headless true &
STREAMLIT_PID=$!

# Небольшая пауза для старта сервера
sleep 2

# Открытие отдельного окна
google-chrome --app=http://localhost:8501

# При закрытии окна завершаем и Streamlit
kill $STREAMLIT_PID
