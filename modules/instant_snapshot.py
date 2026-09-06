#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль мгновенного сбора системного слепка (instant_snapshot.py).

Собирает актуальную информацию о системе в реальном времени:
- активное окно (класс и заголовок);
- загрузка CPU (средняя за 1 минуту);
- температура ядра (из thermal_zone или sensors);
- состояние батареи (наличие, процент, зарядка);
- местоположение (по SSID Wi-Fi);
- статус медиа (воспроизводится ли звук);
- статус mute аудио.

Данные возвращаются в виде словаря и сохраняются в JSON-файл
для возможного использования другими модулями (например, Proactive Engine).
"""

import json
import os
import subprocess
import re
from datetime import datetime

# Путь к файлу для сохранения слепка (если нужно)
SNAPSHOT_FILE = os.path.expanduser("~/gemini_companion/data/instant_snapshot.json")


def run_cmd(cmd, timeout=2):
    """
    Выполняет shell-команду с таймаутом и возвращает stdout без пробельных символов по краям.
    При ошибке или таймауте возвращает пустую строку.
    """
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return res.stdout.strip()
    except Exception:
        return ""


def get_active_window_info():
    """
    Возвращает кортеж (wm_class, window_title) для активного окна.
    Использует xdotool (если доступен), в противном случае — wmctrl.
    Если определить не удалось, возвращает пустые строки.
    """
    # Способ 1: xdotool
    active_id = run_cmd("xdotool getactivewindow")
    if active_id:
        wm_class_raw = run_cmd(f"xprop -id {active_id} WM_CLASS")
        title = run_cmd(f"xdotool getwindowname {active_id}")

        # Извлекаем класс из строки вида: WM_CLASS(STRING) = "code", "Code"
        match = re.search(r'WM_CLASS\(STRING\)\s*=\s*"([^"]+)",\s*"([^"]+)"', wm_class_raw)
        if match:
            class1, class2 = match.group(1).lower(), match.group(2).lower()
            # Обычно второй элемент более специфичен (например, "Code")
            wm_class = class2 if class2 else class1
            return wm_class, title
        else:
            # Если не удалось распарсить, возвращаем пустой класс
            return "", title

    # Способ 2: wmctrl (запасной)
    # wmctrl не показывает активное окно напрямую, поэтому используем xprop с _NET_ACTIVE_WINDOW
    active_id_net = run_cmd("xprop -root _NET_ACTIVE_WINDOW")
    if active_id_net:
        # Формат: _NET_ACTIVE_WINDOW(WINDOW): window id # 0x...
        match = re.search(r'0x[0-9a-fA-F]+', active_id_net)
        if match:
            win_id = match.group(0)
            wm_class_raw = run_cmd(f"xprop -id {win_id} WM_CLASS")
            title = run_cmd(f"xprop -id {win_id} _NET_WM_NAME")
            # Извлекаем заголовок
            title_match = re.search(r'_NET_WM_NAME\(UTF8_STRING\)\s*=\s*"(.*)"', title)
            if title_match:
                title = title_match.group(1)
            else:
                title = ""

            match_class = re.search(r'WM_CLASS\(STRING\)\s*=\s*"([^"]+)",\s*"([^"]+)"', wm_class_raw)
            if match_class:
                class1, class2 = match_class.group(1).lower(), match_class.group(2).lower()
                wm_class = class2 if class2 else class1
                return wm_class, title
    return "", ""


def get_cpu_load():
    """
    Возвращает загрузку CPU в процентах (средняя за 1 минуту).
    Нормализует значение load average к количеству ядер.
    """
    try:
        with open("/proc/loadavg", "r") as f:
            lavg_1min = float(f.readline().split()[0])
        ncores = os.cpu_count() or 1
        load_percent = min(100.0, (lavg_1min / ncores) * 100.0)
        return round(load_percent, 1)
    except Exception:
        return None


def get_cpu_temperature():
    """
    Возвращает температуру ядра в градусах Цельсия.
    Пробует thermal_zone, затем lm-sensors (sensors -j).
    Если датчик недоступен, возвращает None.
    """
    # Способ 1: thermal_zone
    try:
        thermal_dir = "/sys/class/thermal"
        for zone in sorted(os.listdir(thermal_dir)):
            if zone.startswith("thermal_zone"):
                temp_file = os.path.join(thermal_dir, zone, "temp")
                if os.path.exists(temp_file):
                    with open(temp_file, "r") as f:
                        temp_raw = f.read().strip()
                    if temp_raw.isdigit():
                        return float(temp_raw) / 1000.0
                    break
    except Exception:
        pass

    # Способ 2: lm-sensors (sensors -j)
    sensors_out = run_cmd("sensors -j", timeout=5)
    if sensors_out:
        try:
            data = json.loads(sensors_out)
            # Ищем чипы с температурой (coretemp, k10temp, zenpower и т.п.)
            for chip_name, chip_data in data.items():
                if any(keyword in chip_name.lower() for keyword in ["coretemp", "k10temp", "zenpower", "cpu"]):
                    for key, val in chip_data.items():
                        if isinstance(val, dict) and "temp1_input" in val:
                            return float(val["temp1_input"])
        except Exception:
            pass
    return None


def get_battery_status():
    """
    Возвращает словарь с информацией о батарее:
    {
        "present": bool,       # есть ли батарея
        "percent": int|None,   # процент заряда
        "charging": bool       # подключено ли зарядное устройство
    }
    Если батареи нет, возвращает {"present": False, "percent": None, "charging": False}.
    """
    battery_dirs = ["/sys/class/power_supply/BAT0", "/sys/class/power_supply/BAT1"]
    for bat_dir in battery_dirs:
        if os.path.exists(bat_dir):
            try:
                with open(os.path.join(bat_dir, "capacity"), "r") as f:
                    percent = int(f.read().strip())
                with open(os.path.join(bat_dir, "status"), "r") as f:
                    status = f.read().strip().lower()
                is_charging = status in ["charging", "full"]
                return {
                    "present": True,
                    "percent": percent,
                    "charging": is_charging
                }
            except Exception:
                break
    return {"present": False, "percent": None, "charging": False}


def get_wifi_ssid():
    """
    Возвращает SSID текущей Wi-Fi сети (или пустую строку, если не подключены).
    """
    out = run_cmd("nmcli -t -f ACTIVE,SSID dev wifi 2>/dev/null | grep '^yes:'")
    if out:
        parts = out.split(":", 1)
        if len(parts) > 1:
            return parts[1]
    # Запасной вариант: проверка подключения
    active_wifi = run_cmd("nmcli -t -f TYPE,STATE,CONNECTION dev 2>/dev/null | grep '^wifi:connected:'")
    if active_wifi:
        conn_name = active_wifi.split(':')[-1]
        return conn_name
    return ""


def get_location(ssid=None, config=None):
    """
    Определяет местоположение на основе SSID.
    Если передан config (из proactive_config.json), использует его списки SSID для дома/работы.
    Возвращает строку: 'дом', 'работа', 'на ходу', 'неизвестно'.
    """
    if ssid is None:
        ssid = get_wifi_ssid().lower()
    if not ssid:
        return "неизвестно"

    home_ssids = []
    work_ssids = []
    if config:
        home_ssids = [s.lower() for s in config.get("sensors", {}).get("geolocation", {}).get("home_ssids", [])]
        work_ssids = [s.lower() for s in config.get("sensors", {}).get("geolocation", {}).get("work_ssids", [])]
    else:
        # Дефолтные значения, если конфиг не передан
        home_ssids = ["tri-al", "tri-al-5g"]
        work_ssids = ["kbkb"]

    if ssid in home_ssids:
        return "дом"
    elif ssid in work_ssids:
        return "работа"
    else:
        return "на ходу"


def get_media_playing():
    """
    Проверяет, воспроизводится ли аудио в данный момент.
    Использует playerctl и pactl (аналогично предыдущей версии).
    """
    # Проверка через playerctl
    media_status = run_cmd("playerctl -a status 2>/dev/null")
    if "playing" in media_status.lower():
        return True

    # Проверка через pulseaudio/pipiwire corked flag
    sink_inputs = run_cmd("pactl list sink-inputs 2>/dev/null").lower()
    if 'pulse.corked = "false"' in sink_inputs or 'corked: no' in sink_inputs:
        return True
    return False


def get_audio_muted():
    """
    Проверяет, выключен ли звук (mute) на стандартном выходе.
    Возвращает True, если звук выключен, иначе False.
    """
    sink_mute = run_cmd("pactl get-sink-mute @DEFAULT_SINK@ 2>/dev/null")
    return "yes" in sink_mute.lower()


def capture_snapshot(proactive_config=None):
    """
    Собирает полный слепок системы.
    Если передан proactive_config (словарь конфигурации Proactive Engine),
    то использует его для определения местоположения.
    Возвращает словарь со всеми собранными данными.
    """
    # Активное окно
    wm_class, window_title = get_active_window_info()

    # CPU
    cpu_load = get_cpu_load()
    cpu_temp = get_cpu_temperature()

    # Батарея
    battery = get_battery_status()

    # Wi-Fi и геолокация
    ssid = get_wifi_ssid()
    location = get_location(ssid, proactive_config)

    # Медиа и mute
    media_playing = get_media_playing()
    audio_muted = get_audio_muted()

    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "active_window": {
            "class": wm_class,
            "title": window_title
        },
        "cpu_load_percent": cpu_load,
        "cpu_temp_celsius": cpu_temp,
        "battery": battery,
        "wifi_ssid": ssid,
        "location": location,
        "media_playing": media_playing,
        "audio_muted": audio_muted
    }

    # Сохраняем в файл (если нужно)
    os.makedirs(os.path.dirname(SNAPSHOT_FILE), exist_ok=True)
    try:
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[instant_snapshot] Ошибка сохранения файла: {e}")

    return snapshot


if __name__ == "__main__":
    data = capture_snapshot()
    print("Мгновенный слепок системы:")
    print(json.dumps(data, ensure_ascii=False, indent=2))
