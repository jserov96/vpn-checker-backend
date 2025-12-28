import os
import re
import html
import socket
import ssl
import time
import json
import requests
import base64
import websocket
from datetime import datetime, timedelta
from urllib.parse import quote, unquote

# ------------------ Настройки ------------------
NEW_KEYS_FOLDER = "checked"
os.makedirs(NEW_KEYS_FOLDER, exist_ok=True)

TIMEOUT = 2
RETRIES = 1
CACHE_DURATION_HOURS = 6  # Если ключ проверяли меньше 6 часов назад - не проверяем снова

LIVE_KEYS_FILE = os.path.join(NEW_KEYS_FOLDER, "live_keys.txt")
HISTORY_FILE = os.path.join(NEW_KEYS_FOLDER, "history.json") # Тут храним память
MY_CHANNEL = "@vlesstrojan" 

# Ваши источники
URLS = [
    "https://raw.githubusercontent.com/kort0881/vpn-vless-configs-russia/main/githubmirror/new/all_new.txt",
    "https://raw.githubusercontent.com/zieng2/wl/main/vless.txt",
    "https://raw.githubusercontent.com/LowiKLive/BypassWhitelistRu/refs/heads/main/WhiteList-Bypass_Ru.txt",
    "https://raw.githubusercontent.com/zieng2/wl/main/vless_universal.txt",
    "https://raw.githubusercontent.com/vsevjik/OBSpiskov/refs/heads/main/wwh",
    "https://jsnegsukavsos.hb.ru-msk.vkcloud-storage.ru/love",
    "https://etoneya.a9fm.site/1",
    "https://s3c3.001.gpucloud.ru/vahe4xkwi/cjdr"
]

# ------------------ Функции ------------------

def load_history():
    """Загружаем базу проверенных ключей"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}

def save_history(history):
    """Сохраняем базу"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except: pass

def decode_base64_safe(data):
    try:
        data = data.replace('-', '+').replace('_', '/')
        padding = len(data) % 4
        if padding: data += '=' * (4 - padding)
        return base64.b64decode(data).decode('utf-8', errors='ignore')
    except: return None

def fetch_and_load_keys(urls):
    all_keys = set()
    print(f"Загрузка с {len(urls)} источников...")
    for url in urls:
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200: continue
            
            content = resp.text.strip()
            if "vmess://" not in content and "vless://" not in content:
                decoded = decode_base64_safe(content)
                lines = decoded.splitlines() if decoded else content.splitlines()
            else:
                lines = content.splitlines()

            for line in lines:
                line = line.strip()
                if line.startswith(("vless://", "vmess://", "trojan://", "ss://")):
                    all_keys.add(line)
        except Exception: pass
    return list(all_keys)

def extract_host_port(key):
    try:
        if "@" in key and ":" in key:
            after_at = key.split("@")[1]
            main_part = re.split(r'[?#]', after_at)[0]
            if ":" in main_part:
                return main_part.split(":")[0], int(main_part.split(":")[1])
    except: return None, None
    return None, None

def measure_latency(key, host, port, timeout=TIMEOUT):
    is_tls = 'security=tls' in key or 'security=reality' in key or 'trojan://' in key or 'vmess://' in key
    is_ws = 'type=ws' in key or 'net=ws' in key
    
    path = "/"
    path_match = re.search(r'path=([^&]+)', key)
    if path_match: path = unquote(path_match.group(1))
    protocol = "wss" if is_tls else "ws"

    if is_ws:
        try:
            start = time.time()
            ws_url = f"{protocol}://{host}:{port}{path}"
            ws = websocket.create_connection(ws_url, timeout=timeout, sslopt={"cert_reqs": ssl.CERT_NONE})
            ws.close()
            return int((time.time() - start) * 1000)
        except: return None

    if not is_ws and is_tls:
        try:
            start = time.time()
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            with socket.create_connection((host, port), timeout=timeout) as sock:
                with context.wrap_socket(sock, server_hostname=host):
                    pass
            return int((time.time() - start) * 1000)
        except: return None

    try:
        start = time.time()
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return int((time.time() - start) * 1000)
    except: return None

def add_comment(key, latency, quality):
    if "#" in key: base, _ = key.split("#", 1)
    else: base = key
    tag = f"{quality}_{latency}ms_{MY_CHANNEL}".replace(" ", "_")
    return f"{base}#{tag}"

# ------------------ Main ------------------
if __name__ == "__main__":
    print("=== START SMART CHECKER ===")
    
    # 1. Загружаем историю проверок
    history = load_history()
    print(f"В истории записей: {len(history)}")
    
    # 2. Качаем свежие ключи
    current_keys = fetch_and_load_keys(URLS)
    print(f"Скачано уникальных ключей: {len(current_keys)}")

    valid_keys_list = []
    current_time = time.time()
    checks_made = 0
    skipped_count = 0

    # 3. Умная проверка
    for i, key in enumerate(current_keys):
        key = html.unescape(key).strip()
        
        # Используем сам ключ (без хештега) как ID для базы
        key_id = key.split("#")[0]
        
        # ПРОВЕРКА КЭША
        cached = history.get(key_id)
        latency = None
        need_check = True

        if cached:
            last_check_time = cached.get('time', 0)
            # Если проверяли недавно (меньше 6 часов назад)
            if current_time - last_check_time < (CACHE_DURATION_HOURS * 3600):
                if cached.get('alive', False):
                    # Ключ был жив, верим истории
                    latency = cached.get('latency', 100)
                    need_check = False
                    skipped_count += 1
                else:
                    # Ключ был мертв, проверяем реже (например, раз в 24 часа) или не проверяем
                    # Для простоты: мертвые перепроверяем всегда, вдруг ожили? 
                    # Или можно пропускать: need_check = False
                    pass

        if need_check:
            host, port = extract_host_port(key)
            if host:
                checks_made += 1
                latency = measure_latency(key, host, port)
                
                # Обновляем историю
                history[key_id] = {
                    'alive': latency is not None,
                    'latency': latency,
                    'time': current_time
                }
        
        # Если живой (из кэша или после проверки)
        if latency is not None:
            qual = "fast" if latency < 500 else "normal" if latency < 1500 else "slow"
            final_key = add_comment(key, latency, qual)
            valid_keys_list.append(final_key)
            
        if i % 100 == 0: 
            print(f"Processed {i}/{len(current_keys)} (Checks: {checks_made}, Skipped: {skipped_count})")

    # 4. Сохраняем результаты
    
    # Чистим старую историю (удаляем записи старше 3 дней, чтобы файл не пух)
    clean_history = {}
    for k, v in history.items():
        if current_time - v['time'] < (3 * 24 * 3600):
            clean_history[k] = v
    save_history(clean_history)

    with open(LIVE_KEYS_FILE, "w", encoding="utf-8") as f:
        for k in valid_keys_list:
            f.write(k + "\n")

    print(f"=== DONE ===")
    print(f"Valid: {len(valid_keys_list)}")
    print(f"Real Checks: {checks_made}")
    print(f"Skipped (Cached): {skipped_count}")






