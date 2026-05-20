vk_api
requests
flask
import os
import json
import time
import threading
import requests
from datetime import datetime
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.upload import VkUpload
from flask import Flask

# ========== СОЗДАЁМ ВЕБ-СЕРВЕР ДЛЯ HEALTHCHECK ==========
app = Flask(__name__)

@app.route('/health')
def health():
    return "OK", 200

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# ========== ВАШИ ДАННЫЕ (из переменных окружения) ==========
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
OWNER_SCREEN_NAME = "denchik93"

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

YANDEXGPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# ========== НАСТРОЙКИ ==========
DATA_FILE = os.path.join("data", "bot_data.json")
GROUP_ID = None
OWNER_ID = None

# ========== РАБОТА С ФАЙЛОМ ДАННЫХ ==========
def load_data():
    # Создаём папку data, если её нет
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(DATA_FILE):
        return {
            "schedule": {},
            "publish_time": "10:00",
            "pending_post": None
        }
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(data):
    os.makedirs("data", exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ========== ПОЛУЧЕНИЕ ID ВЛАДЕЛЬЦА ==========
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

# ========== ГЕНЕРАЦИЯ ТЕКСТА ПОСТА (YandexGPT) ==========
def generate_post_text(topic: str) -> str:
    system_prompt = f"""Ты — креативный копирайтер для семейного сообщества ВКонтакте о развитии детей (0–17 лет). Напиши пост на тему: "{topic}".
Пост должен быть:
- живым, тёплым и полезным для родителей;
- содержать конкретные советы или описание игры/занятия;
- длиной 300–500 символов;
- с эмодзи;
- в конце обязательно вопрос к аудитории для вовлечения.
Только текст поста, без лишних комментариев и пояснений."""

    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.7,
            "maxTokens": "600"
        },
        "messages": [
            {
                "role": "system",
                "text": system_prompt
            }
        ]
    }

    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(YANDEXGPT_URL, json=body, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        generated_text = data["result"]["alternatives"][0]["message"]["text"]
        return generated_text.strip()
    except Exception as e:
        print(f"Ошибка генерации текста: {e}")
        if 'response' in locals() and response.status_code == 403:
            return "⚠️ Ошибка авторизации YandexGPT. Проверьте API-ключ и folder ID."
        return f"⚠️ Не удалось сгенерировать текст. Ошибка: {e}"

# ========== ГЕНЕРАЦИЯ КАРТИНКИ (БЕСПЛАТНО) ==========
def generate_image(prompt_topic):
    image_prompt = f"Create an illustration for a children's educational game or parenting tip: {prompt_topic}. Bright, friendly, cartoon style, family friendly."
    url = f"https://pollinations.ai/p/{image_prompt}?width=1024&height=768"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            filename = "temp_img.jpg"
            with open(filename, 'wb') as f:
                f.write(resp.content)
            return filename
        else:
            print(f"Ошибка генерации картинки: {resp.status_code}")
            return None
    except Exception as e:
        print(f"Ошибка: {e}")
        return None

# ========== ЗАГРУЗКА ФОТО В VK ==========
def upload_photo_to_vk(vk, image_path, for_wall=True):
    upload = VkUpload(vk)
    if for_wall:
        photo = upload.photo_wall(image_path)
        return f"photo{photo[0]['owner_id']}_{photo[0]['id']}"
    else:
        photo = upload.photo_messages(image_path)[0]
        return f"photo{photo['owner_id']}_{photo['id']}"

# ========== ОТПРАВКА НА МОДЕРАЦИЮ ==========
def send_for_moderation(post_text, image_path, vk_group_session):
    attachment = upload_photo_to_vk(vk_group_session, image_path, for_wall=False)
    vk_group_session.method("messages.send", {
        "user_id": OWNER_ID,
        "message": f"✍️ *Новый пост на модерацию*\n\n{post_text}\n\nОтветьте «опубликовать» или «отклонить»:",
        "attachment": attachment,
        "random_id": 0
    })

# ========== ПУБЛИКАЦИЯ ПОСТА ==========
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

# ========== ОБРАБОТЧИК СООБЩЕНИЙ (КОМАНДЫ) ==========
def handle_messages(vk_group_session):
    longpoll = VkLongPoll(vk_group_session)
    data = load_data()
    
    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me and event.user_id == OWNER_ID:
            msg = event.text.strip()
            print(f"Получено сообщение: {msg}")
            
            if msg.startswith("/set_time"):
                parts = msg.split()
                if len(parts) >= 2:
                    new_time = parts[1]
                    try:
                        datetime.strptime(new_time, "%H:%M")
                        data["publish_time"] = new_time
                        save_data(data)
                        reply = f"🕒 Время публикации установлено на {new_time}"
                    except:
                        reply = "❌ Неверный формат. Используйте /set_time 09:00"
                else:
                    reply = "❌ Укажите время, например /set_time 15:30"
                vk_group_session.method("messages.send", {
                    "user_id": OWNER_ID,
                    "message": reply,
                    "random_id": 0
                })
            
            elif msg.startswith("/add"):
                try:
                    parts = msg[5:].split(":", 1)
                    date_str = parts[0].strip()
                    topic = parts[1].strip()
                    datetime.strptime(date_str, "%Y-%m-%d")
                    data["schedule"][date_str] = topic
                    save_data(data)
                    reply = f"✅ Добавлена тема на {date_str}:\n{topic}"
                except Exception:
                    reply = "❌ Ошибка. Используйте формат:\n/add 2025-06-20: описание темы"
                vk_group_session.method("messages.send", {
                    "user_id": OWNER_ID,
                    "message": reply,
                    "random_id": 0
                })
            
            elif msg.startswith("/remove"):
                parts = msg.split()
                if len(parts) >= 2:
                    date_str = parts[1]
                    if date_str in data["schedule"]:
                        del data["schedule"][date_str]
                        save_data(data)
                        reply = f"🗑 Тема на {date_str} удалена"
                    else:
                        reply = f"❌ На {date_str} нет запланированной темы"
                else:
                    reply = "❌ Укажите дату: /remove 2025-06-20"
                vk_group_session.method("messages.send", {
                    "user_id": OWNER_ID,
                    "message": reply,
                    "random_id": 0
                })
            
            elif msg == "/list":
                if not data["schedule"]:
                    reply = "📭 Нет запланированных тем. Добавьте командой /add"
                else:
                    lines = ["📅 *Ваш контент-план:*"]
                    for date, topic in sorted(data["schedule"].items()):
                        lines.append(f"{date}: {topic}")
                    reply = "\n".join(lines)
                vk_group_session.method("messages.send", {
                    "user_id": OWNER_ID,
                    "message": reply,
                    "random_id": 0
                })
            
            elif msg.lower() == "опубликовать" and data.get("pending_post"):
                pending = data["pending_post"]
                publish_post(pending["text"], pending["image_path"], vk_group_session)
                today_str = datetime.now().strftime("%Y-%m-%d")
                if today_str in data["schedule"]:
                    del data["schedule"][today_str]
                data["pending_post"] = None
                save_data(data)
                vk_group_session.method("messages.send", {
                    "user_id": OWNER_ID,
                    "message": "✅ Пост опубликован!",
                    "random_id": 0
                })
            
            elif msg.lower() == "отклонить" and data.get("pending_post"):
                data["pending_post"] = None
                save_data(data)
                vk_group_session.method("messages.send", {
                    "user_id": OWNER_ID,
                    "message": "❌ Пост отклонён.",
                    "random_id": 0
                })
            
            elif msg == "/help":
                help_text = """Доступные команды:
/set_time HH:MM – задать время ежедневной публикации
/add YYYY-MM-DD: тема поста – запланировать пост
/remove YYYY-MM-DD – удалить запланированный пост
/list – показать все запланированные темы
/help – эта справка

Когда наступает заданное время, бот берёт тему на сегодня, генерирует пост + картинку, присылает вам на проверку. Вы отвечаете «опубликовать» или «отклонить»."""
                vk_group_session.method("messages.send", {
                    "user_id": OWNER_ID,
                    "message": help_text,
                    "random_id": 0
                })
            
            else:
                vk_group_session.method("messages.send", {
                    "user_id": OWNER_ID,
                    "message": "❓ Неизвестная команда. Напишите /help",
                    "random_id": 0
                })

# ========== ПРОВЕРКА РАСПИСАНИЯ И ПУБЛИКАЦИЯ ==========
def check_and_publish_scheduled(vk_group_session):
    data = load_data()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    
    if (today_str in data["schedule"] and 
        current_time == data["publish_time"] and 
        data.get("pending_post") is None):
        
        topic = data["schedule"][today_str]
        print(f"Генерация поста на тему: {topic}")
        
        post_text = generate_post_text(topic)
        if post_text.startswith("⚠️"):
            vk_group_session.method("messages.send", {
                "user_id": OWNER_ID,
                "message": f"⚠️ Ошибка генерации текста для темы «{topic}».\n{post_text}",
                "random_id": 0
            })
            return
        
        img_file = generate_image(topic)
        if img_file:
            send_for_moderation(post_text, img_file, vk_group_session)
            data["pending_post"] = {
                "text": post_text,
                "image_path": img_file,
                "topic": topic
            }
            save_data(data)
            print("Пост отправлен на модерацию")
        else:
            vk_group_session.method("messages.send", {
                "user_id": OWNER_ID,
                "message": f"⚠️ Не удалось сгенерировать картинку для темы «{topic}». Пост не создан.",
                "random_id": 0
            })

# ========== ЗАПУСК ОСНОВНОГО БОТА ==========
def main():
    global OWNER_ID, GROUP_ID
    
    if not VK_GROUP_TOKEN:
        print("❌ Ошибка: не задан VK_GROUP_TOKEN в переменных окружения")
        return
    
    vk_session = vk_api.VkApi(token=VK_GROUP_TOKEN)
    vk = vk_session.get_api()
    
    group_info = vk.groups.getById()
    GROUP_ID = group_info[0]['id']
    
    OWNER_ID = get_owner_id(vk_session)
    if not OWNER_ID:
        print("❌ Не удалось определить ID владельца. Проверьте короткое имя.")
        return
    
    print(f"✅ Бот запущен для сообщества {GROUP_ID}")
    print(f"📨 Владелец: {OWNER_SCREEN_NAME} (id {OWNER_ID})")
    print("🤖 Используется YandexGPT (бесплатно)")
    
    # Запускаем обработчик сообщений в основном потоке (он бесконечный)
    handle_messages(vk_session)

# ========== ТОЧКА ВХОДА ==========
if __name__ == "__main__":
    # Запускаем веб-сервер в отдельном потоке
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    # Запускаем бота (он работает в основном потоке)
    main()
__pycache__/
*.pyc
temp_img.jpg
data/
.env
