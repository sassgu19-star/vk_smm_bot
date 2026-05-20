#!/usr/bin/env python3
import sys
import os

sys.stderr = sys.stdout
print("=== BOT STARTUP ===", flush=True)
print(f"Python {sys.version}", flush=True)
print(f"CWD: {os.getcwd()}", flush=True)
print(f"Files: {os.listdir('.')}", flush=True)

try:
    import json
    import time
    import threading
    from datetime import datetime
    import requests
    import vk_api
    from vk_api.longpoll import VkLongPoll, VkEventType
    from vk_api.upload import VkUpload
    from flask import Flask
    print("Imports OK", flush=True)
except Exception as e:
    print(f"IMPORT FAIL: {e}", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== FLASK WEB SERVER ==========
app = Flask(__name__)

@app.route('/health')
def health():
    return "OK", 200

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# ========== ENV VARIABLES ==========
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
OWNER_SCREEN_NAME = "denchik93"
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")
YANDEXGPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

print(f"VK_TOKEN set: {bool(VK_GROUP_TOKEN)}", flush=True)
print(f"YANDEX_API_KEY set: {bool(YANDEX_API_KEY)}", flush=True)
print(f"YANDEX_FOLDER_ID set: {bool(YANDEX_FOLDER_ID)}", flush=True)

if not VK_GROUP_TOKEN:
    print("ERROR: VK_GROUP_TOKEN not set", flush=True)
    sys.exit(1)

# ========== DATA FILE ==========
DATA_FILE = os.path.join("data", "bot_data.json")
GROUP_ID = None
OWNER_ID = None

def load_data():
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(DATA_FILE):
        return {"schedule": {}, "publish_time": "10:00", "pending_post": None}
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(data):
    os.makedirs("data", exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_owner_id(vk_session):
    try:
        user = vk_session.method("users.get", {"user_ids": OWNER_SCREEN_NAME})
        if user:
            return user[0]['id']
        else:
            raise Exception("Пользователь не найден")
    except Exception as e:
        print(f"Ошибка получения ID владельца: {e}")
        return None

def generate_post_text(topic: str) -> str:
    system_prompt = f"""Ты — креативный копирайтер для семейного сообщества ВКонтакте о развитии детей (0–17 лет). Напиши пост на тему: "{topic}".
Пост должен быть живым, тёплым, полезным, с эмодзи и вопросом в конце. Длина 300-500 символов. Только текст."""
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {"stream": False, "temperature": 0.7, "maxTokens": "600"},
        "messages": [{"role": "system", "text": system_prompt}]
    }
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    try:
        response = requests.post(YANDEXGPT_URL, json=body, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()["result"]["alternatives"][0]["message"]["text"].strip()
    except Exception as e:
        print(f"Ошибка генерации текста: {e}")
        return f"⚠️ Ошибка: {e}"

def generate_image(prompt_topic):
    url = f"https://pollinations.ai/p/{prompt_topic}?width=1024&height=768"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            filename = "temp_img.jpg"
            with open(filename, 'wb') as f:
                f.write(resp.content)
            return filename
        return None
    except Exception as e:
        print(f"Ошибка картинки: {e}")
        return None

def upload_photo_to_vk(vk, image_path, for_wall=True):
    upload = VkUpload(vk)
    if for_wall:
        photo = upload.photo_wall(image_path)
        return f"photo{photo[0]['owner_id']}_{photo[0]['id']}"
    else:
        photo = upload.photo_messages(image_path)[0]
        return f"photo{photo['owner_id']}_{photo['id']}"

def send_for_moderation(post_text, image_path, vk_group_session):
    attachment = upload_photo_to_vk(vk_group_session, image_path, for_wall=False)
    vk_group_session.method("messages.send", {
        "user_id": OWNER_ID,
        "message": f"✍️ Новый пост:\n\n{post_text}\n\nОтветьте «опубликовать» или «отклонить»:",
        "attachment": attachment,
        "random_id": 0
    })

def publish_post(post_text, image_path, vk_group_session):
    global GROUP_ID
    if GROUP_ID is None:
        group_info = vk_group_session.method("groups.getById")
        GROUP_ID = group_info[0]['id']
    attachment = upload_photo_to_vk(vk_group_session, image_path, for_wall=True)
    vk_group_session.method("wall.post", {
        "owner_id": -GROUP_ID,
        "message": post_text,
        "attachments": attachment,
        "from_group": 1
    })
    print(f"✅ Пост опубликован в {datetime.now()}")

def handle_messages(vk_group_session):
    longpoll = VkLongPoll(vk_group_session)
    data = load_data()
    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me and event.user_id == OWNER_ID:
            msg = event.text.strip()
            print(f"Получено: {msg}")
            if msg.startswith("/set_time"):
                parts = msg.split()
                if len(parts) >= 2:
                    try:
                        datetime.strptime(parts[1], "%H:%M")
                        data["publish_time"] = parts[1]
                        save_data(data)
                        reply = f"🕒 Время публикации {parts[1]}"
                    except:
                        reply = "❌ Формат HH:MM"
                else:
                    reply = "❌ /set_time 09:00"
                vk_group_session.method("messages.send", {"user_id": OWNER_ID, "message": reply, "random_id": 0})
            elif msg.startswith("/add"):
                try:
                    parts = msg[5:].split(":", 1)
                    date_str = parts[0].strip()
                    topic = parts[1].strip()
                    datetime.strptime(date_str, "%Y-%m-%d")
                    data["schedule"][date_str] = topic
                    save_data(data)
                    reply = f"✅ Добавлено на {date_str}"
                except:
                    reply = "❌ /add 2025-06-20: тема"
                vk_group_session.method("messages.send", {"user_id": OWNER_ID, "message": reply, "random_id": 0})
            elif msg.startswith("/remove"):
                parts = msg.split()
                if len(parts) >= 2 and parts[1] in data["schedule"]:
                    del data["schedule"][parts[1]]
                    save_data(data)
                    reply = f"🗑 Удалено {parts[1]}"
                else:
                    reply = "❌ Даты нет или /remove 2025-06-20"
                vk_group_session.method("messages.send", {"user_id": OWNER_ID, "message": reply, "random_id": 0})
            elif msg == "/list":
                if not data["schedule"]:
                    reply = "📭 Нет тем"
                else:
                    lines = ["📅 План:"]
                    for d, t in sorted(data["schedule"].items()):
                        lines.append(f"{d}: {t}")
                    reply = "\n".join(lines)
                vk_group_session.method("messages.send", {"user_id": OWNER_ID, "message": reply, "random_id": 0})
            elif msg.lower() == "опубликовать" and data.get("pending_post"):
                pending = data["pending_post"]
                publish_post(pending["text"], pending["image_path"], vk_group_session)
                today_str = datetime.now().strftime("%Y-%m-%d")
                if today_str in data["schedule"]:
                    del data["schedule"][today_str]
                data["pending_post"] = None
                save_data(data)
                vk_group_session.method("messages.send", {"user_id": OWNER_ID, "message": "✅ Опубликовано!", "random_id": 0})
            elif msg.lower() == "отклонить" and data.get("pending_post"):
                data["pending_post"] = None
                save_data(data)
                vk_group_session.method("messages.send", {"user_id": OWNER_ID, "message": "❌ Отклонено", "random_id": 0})
            elif msg == "/help":
                help_text = """Команды:
/set_time HH:MM
/add YYYY-MM-DD: тема
/remove YYYY-MM-DD
/list
/help"""
                vk_group_session.method("messages.send", {"user_id": OWNER_ID, "message": help_text, "random_id": 0})
            else:
                vk_group_session.method("messages.send", {"user_id": OWNER_ID, "message": "/help", "random_id": 0})

def check_and_publish_scheduled(vk_group_session):
    data = load_data()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    if (today_str in data["schedule"] and current_time == data["publish_time"] and data.get("pending_post") is None):
        topic = data["schedule"][today_str]
        print(f"Генерация: {topic}")
        post_text = generate_post_text(topic)
        if post_text.startswith("⚠️"):
            vk_group_session.method("messages.send", {"user_id": OWNER_ID, "message": f"Ошибка текста: {post_text}", "random_id": 0})
            return
        img = generate_image(topic)
        if img:
            send_for_moderation(post_text, img, vk_group_session)
            data["pending_post"] = {"text": post_text, "image_path": img, "topic": topic}
            save_data(data)
        else:
            vk_group_session.method("messages.send", {"user_id": OWNER_ID, "message": "⚠️ Нет картинки", "random_id": 0})

def main():
    global OWNER_ID, GROUP_ID
    vk_session = vk_api.VkApi(token=VK_GROUP_TOKEN)
    vk = vk_session.get_api()
    group_info = vk.groups.getById()
    GROUP_ID = group_info[0]['id']
    OWNER_ID = get_owner_id(vk_session)
    if not OWNER_ID:
        print("❌ OWNER_ID не найден")
        sys.exit(1)
    print(f"✅ Бот запущен, сообщество {GROUP_ID}, владелец {OWNER_ID}")
    # Запускаем поток для расписания
    def schedule_loop():
        while True:
            try:
                check_and_publish_scheduled(vk_session)
            except Exception as e:
                print(f"Ошибка в расписании: {e}")
            time.sleep(60)
    threading.Thread(target=schedule_loop, daemon=True).start()
    # Обработка сообщений (основной поток)
    handle_messages(vk_session)

if __name__ == "__main__":
    print("Запуск веб-сервера...", flush=True)
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    print("Запуск бота...", flush=True)
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
