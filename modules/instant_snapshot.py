import json
import os
import subprocess
from datetime import datetime

def run_cmd(cmd):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=2)
        return res.stdout.strip()
    except Exception:
        return ""

def capture_snapshot():
    # Wi-Fi connection check
    wifi_raw = run_cmd("nmcli -t -f ACTIVE,SSID,BSSID dev wifi 2>/dev/null | grep -i '^yes:'")
    
    if not wifi_raw:
        active_wifi = run_cmd("nmcli -t -f TYPE,STATE,CONNECTION dev 2>/dev/null | grep '^wifi:connected:'")
        if active_wifi:
            conn_name = active_wifi.split(':')[-1]
            wifi_raw = f"yes:{conn_name}:unknown_bssid"

    # Media status check: 1. Playerctl
    media_statuses = run_cmd("playerctl -a status 2>/dev/null")
    is_playing = "playing" in media_statuses.lower()

    # Media status check: 2. PulseAudio / PipeWire Corked flag
    if not is_playing:
        sink_inputs = run_cmd("pactl list sink-inputs 2>/dev/null").lower()
        if 'pulse.corked = "false"' in sink_inputs or 'corked: no' in sink_inputs:
            is_playing = True

    # Audio mute status
    sink_mute = run_cmd("pactl get-sink-mute @DEFAULT_SINK@ 2>/dev/null")

    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "wifi_connection": wifi_raw if wifi_raw else "disconnected",
        "media_playing": is_playing,
        "audio_muted": "yes" in sink_mute.lower()
    }

    data_dir = os.path.expanduser("~/gemini_companion/data")
    os.makedirs(data_dir, exist_ok=True)
    
    output_file = os.path.join(data_dir, "instant_snapshot.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    return snapshot

if __name__ == "__main__":
    data = capture_snapshot()
    print("Обновленный слепок системы:")
    print(json.dumps(data, ensure_ascii=False, indent=2))
