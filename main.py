import os
import time
import uuid
import json
import logging
import html
import re
from datetime import datetime, timedelta
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import threading
import random
import sys
import hashlib
import asyncio
import requests
from types import SimpleNamespace

# =====================================================
#           🔐 USERBOT & AI INTEGRATION
# =====================================================
try:
    import userbot as ub_module
    USERBOT_AVAILABLE = True
except ImportError:
    USERBOT_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.error("userbot.py not found or dependencies missing!")

USERBOT_LOOP = asyncio.new_event_loop()

def _run_userbot_loop():
    asyncio.set_event_loop(USERBOT_LOOP)
    USERBOT_LOOP.run_forever()

threading.Thread(target=_run_userbot_loop, daemon=True).start()

def run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, USERBOT_LOOP)
    return future.result(timeout=60)

# =====================================================
#           🔐 ADMIN AUTH
# =====================================================

# Set ADMIN_PASSWORD and BOT_TOKEN as environment variables before starting the bot.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()  # Set a strong value in Render -> Environment
ADMIN_PASSWORD_HASH = hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest()
SESSION_FILE = "admin_session.json"

def load_session():
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_session(data):
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except:
        return False

def verify_admin_password(password):
    return hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD_HASH

def is_admin_logged_in(user_id):
    # Admin login is stored in the main database and remains active until Logout.
    session = user_sessions.get(user_id, {})
    if session.get("logged_in", False):
        return True

    saved = db.get("admin_sessions", {}).get(str(user_id), {})
    if saved.get("logged_in", False):
        try:
            login_time = datetime.fromisoformat(saved.get("login_time", datetime.now().isoformat()))
        except (TypeError, ValueError):
            login_time = datetime.now()
        user_sessions[user_id] = {
            "logged_in": True, "login_time": login_time,
            "remember": True, "auto_login": True
        }
        return True

    legacy = load_session()
    old = legacy.get(str(user_id), {})
    if old.get("logged_in", False):
        user_sessions[user_id] = {
            "logged_in": True, "login_time": datetime.now(),
            "remember": True, "auto_login": True
        }
        db.setdefault("admin_sessions", {})[str(user_id)] = {
            "logged_in": True,
            "login_time": old.get("login_time", datetime.now().isoformat())
        }
        sync_db()
        return True
    return False


def login_admin(user_id, password, remember=True):
    if not verify_admin_password(password):
        return False
    login_time = datetime.now()
    user_sessions[user_id] = {
        "logged_in": True, "login_time": login_time,
        "remember": True, "auto_login": True
    }
    db.setdefault("admin_sessions", {})[str(user_id)] = {
        "logged_in": True, "login_time": login_time.isoformat()
    }
    sync_db()
    return True


def logout_admin(user_id):
    user_sessions[user_id] = {
        "logged_in": False, "login_time": None,
        "remember": False, "auto_login": False
    }
    db.setdefault("admin_sessions", {}).pop(str(user_id), None)
    sync_db()
    saved = load_session()
    saved.pop(str(user_id), None)
    save_session(saved)
    return True

user_sessions = {}
live_schedule_messages = {}  
live_deletion_messages = {} 
BOT_NAME = "SHAKIB"

try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except ImportError:
    GTTS_AVAILABLE = False

# =====================================================
#           ⚙️ CONFIG
# =====================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()  # Set in Render -> Environment
ADMIN_ID = 7058712225
DB_FILE = "bot_database.json"
LOG_FILE = "bot_activity.log"
BACKUP_INTERVAL = 300

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def print_banner():
    os.system('clear' if os.name == 'posix' else 'cls')
    print("\033[96m" + "═" * 55)
    print(f"\033[93m   ✨ {BOT_NAME} PREMIUM BOT ✨   \033[96m")
    print("═" * 55)
    print("\033[92m✅ Bot Ready!\033[0m")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Add BOT_TOKEN in Render -> Environment, then redeploy.")
if not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD is missing. Add a strong ADMIN_PASSWORD in Render -> Environment, then redeploy.")

bot = telebot.TeleBot(BOT_TOKEN)

# =====================================================
#   ✅ HELPER FUNCTIONS (Auto Clean)
# =====================================================

def clean_msg(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

def send_clean_prompt(chat_id, text, reply_markup=None, parse_mode=None):
    st = user_states.get(chat_id)
    if isinstance(st, dict) and "last_bot_msg" in st:
        clean_msg(chat_id, st["last_bot_msg"])
    
    msg = bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    
    if isinstance(user_states.get(chat_id), dict):
        user_states[chat_id]["last_bot_msg"] = msg.message_id
    else:
        user_states[chat_id] = {"last_bot_msg": msg.message_id}
    return msg

# =====================================================
#   ✅ SMART PARSERS
# =====================================================

def extract_public_username(text):
    if not text:
        return None
    raw = str(text).strip()
    if raw.startswith("@") and raw[1:].isdigit():
        return None
    if "t.me/" in raw:
        p = raw.split("t.me/")[-1].strip("/").split("/")[0].split("?")[0]
        if p.startswith("+") or p.startswith("joinchat"):
            return None
        if p:
            return "@" + p
    if raw.startswith("@") and len(raw) > 1:
        u = raw[1:].split("/")[0].split("?")[0]
        if u.isdigit():
            return None
        return "@" + u
    if not raw.startswith("-") and not raw.startswith("http") and " " not in raw and "/" not in raw:
        return "@" + raw
    return None

def extract_join_url(text):
    if not text:
        return None
    raw = str(text).strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("@"):
        return f"https://t.me/{raw[1:]}"
    if not raw.startswith("-") and not raw.startswith("+") and " " not in raw:
        return f"https://t.me/{raw}"
    return None

def extract_chat_id(text):
    if not text:
        return None
    raw = str(text).strip()
    if "|" in raw:
        raw = raw.split("|")[0].strip()
    if raw.startswith("@") and raw[1:].isdigit():
        d = raw[1:]
        return "-" + d if d.startswith("100") else "-100" + d
    if raw.startswith("@-"):
        f = raw[1:]
        if f.startswith("-100") and f[4:].isdigit():
            return f
    if raw.isdigit() and len(raw) >= 10:
        return "-" + raw if raw.startswith("100") else "-100" + raw
    if raw.startswith("-100") and raw[4:].isdigit():
        return raw
    if raw.startswith("-") and raw[1:].isdigit():
        return raw
    return None

def validate_channel(identifier):
    if not identifier:
        return None
    try:
        chat = bot.get_chat(identifier)
        return {
            "id": chat.id,
            "title": chat.title or chat.first_name or "Unknown",
            "username": chat.username,
            "type": chat.type
        }
    except Exception as e:
        logger.error(f"Validate error ({identifier}): {e}")
        return None

def check_bot_admin(chat_id):
    try:
        bi = bot.get_me()
        m = bot.get_chat_member(chat_id, bi.id)
        return m.status in ['administrator', 'creator']
    except Exception as e:
        logger.error(f"Bot admin check error: {e}")
        return False

def check_bot_delete_permission(chat_id):
    try:
        bi = bot.get_me()
        m = bot.get_chat_member(chat_id, bi.id)
        if m.status in ['administrator', 'creator']:
            if getattr(m, 'can_delete_messages', False):
                return True
        return False
    except Exception as e:
            logger.error(f"Permission check error: {e}")
            return False

# =====================================================
#   ✅ CHANNEL SYSTEM
# =====================================================

def get_all_channels():
    channels = []
    try:
        if isinstance(bot_settings.get("channels"), list):
            for ch in bot_settings["channels"]:
                if ch:
                    # Prefer the verified Telegram title everywhere in the panel;
                    # retain the custom name as a fallback for legacy records.
                    real_title = str(ch.get("real_title") or "").strip()
                    channels.append({
                        "id": ch.get("id", "ch_" + str(uuid.uuid4().hex[:6])),
                        "name": real_title or ch.get("name", "Channel"),
                        "custom_name": ch.get("name", "Channel"),
                        "real_title": real_title,
                        "type": ch.get("type", "unknown"),
                        "join_url": ch.get("join_url", ""),
                        "chat_id": str(ch.get("chat_id", ""))
                    })
    except Exception as e:
        logger.error(f"get_all_channels error: {e}")
    return channels

def add_channel(name, ch_type, join_url, chat_id, join_btn_name="", real_title=""):
    if "channels" not in bot_settings or not isinstance(bot_settings["channels"], list):
        bot_settings["channels"] = []
    new_ch = {
        "id": "ch_" + str(uuid.uuid4().hex[:8]),
        "name": name,
        "real_title": real_title or "",
        "type": ch_type,
        "join_url": join_url or "",
        "chat_id": str(chat_id) if chat_id else "",
        "join_btn_name": str(join_btn_name or "").strip(),
        "added_at": datetime.now().isoformat()
    }
    bot_settings["channels"].append(new_ch)
    sync_db()
    return new_ch

def remove_channel_by_id(ch_id):
    if isinstance(bot_settings.get("channels"), list):
        bot_settings["channels"] = [c for c in bot_settings["channels"] if c.get("id") != ch_id]
    sync_db()

def update_channel(ch_id, **fields):
    """Updates whichever of name/join_btn_name/real_title/chat_id/join_url are passed."""
    for c in bot_settings.get("channels", []):
        if c.get("id") == ch_id:
            for k, v in fields.items():
                if v is not None:
                    c[k] = v
            sync_db()
            return c
    return None

def get_unjoined_channels(user_id):
    unjoined = []
    if not bot_settings.get("force_join", True):
        return unjoined
    for ch in get_all_channels():
        try:
            chat_id = ch.get("chat_id", "").strip()
            join_url = ch.get("join_url", "").strip()
            identifier = None
            if chat_id:
                if chat_id.startswith("@"):
                    identifier = chat_id
                elif chat_id.startswith("-"):
                    identifier = chat_id
            elif ch.get("type") == "public" and join_url:
                identifier = extract_public_username(join_url)
            if not identifier:
                continue
            member = bot.get_chat_member(identifier, user_id)
            if member.status in ['left', 'kicked']:
                unjoined.append(ch)
        except Exception as e:
            logger.error(f"Membership check error ({ch.get('name')}): {e}")
            continue
    return unjoined

def get_join_message(unjoined):
    title = bot_settings.get("force_join_title", "🔒 ACCESS RESTRICTED")
    msg_body = bot_settings.get("force_join_message", "📌 Join {count} channel(s) to continue")
    try:
        body = msg_body.replace("{count}", str(len(unjoined)))
    except:
        body = msg_body
    return f"{title}\n\n{body}".strip()

def get_my_admin_channels():
    result = []
    try:
        bi = bot.get_me()
        for ch in get_all_channels():
            try:
                identifier = ch.get("chat_id") or ch.get("join_url", "")
                if not identifier:
                    continue
                if not str(identifier).startswith("-") and not str(identifier).startswith("@"):
                    identifier = extract_public_username(identifier)
                if not identifier:
                    continue
                try:
                    member = bot.get_chat_member(identifier, bi.id)
                    if member.status in ['administrator', 'creator']:
                        info = validate_channel(identifier)
                        result.append({
                            "id": ch.get("id"),
                            "name": ch.get("name", "Channel"),
                            "chat_id": identifier,
                            "title": info["title"] if info else ch.get("name", "Channel"),
                            "username": info["username"] if info else None
                        })
                except:
                    continue
            except:
                continue
    except:
        pass
    return result

# =====================================================
#           💾 DATABASE
# =====================================================

def get_default_db():
    return {
        "file_database": {},
        "project_files": [],
        "admin_sessions": {},
        "registered_users": [],
        "banned_users": [],
        "user_activity": {},
        "user_ranks": {},
        "bot_settings": {
            "force_join": True,
            "force_join_title": "🔒 ACCESS RESTRICTED",
            "force_join_message": "📌 Join {count} channel(s) to continue",
            "channels": [],
            "channel_groups": [],
            "custom_commands": {},
            "maintenance_mode": False,
            "welcome_message": f"✨ Welcome to {BOT_NAME} Premium Bot! ✨\n\n👋 Hello {{name}}!",
            "auto_delete": False,
            "delete_after": 60,
            "log_channel": None,
            "auto_reply": {},
            "referral_bonus": 5,
            "daily_reward": 10,
            "sub_admins": [],
            "admin_password": ADMIN_PASSWORD_HASH,
            "conn_sources": [],
            "conn_dest": "",
            "conn_settings": {"copy_text": True, "copy_files": True, "copy_links": True},
            "reaction_bots": [],
            "saved_bot_tokens": [],
            "user_limit_enabled": True
        },
        "scheduled_messages": [],
        "scheduled_posts": [],
        "scheduled_deletions": [],
        "giveaway": {"active": False, "prize": "", "participants": [], "end_time": None, "winners": []},
        "referrals": {}
    }

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default = get_default_db()
                for k in default:
                    if k not in data:
                        data[k] = default[k]
                for k in default["bot_settings"]:
                    if k not in data["bot_settings"]:
                        data["bot_settings"][k] = default["bot_settings"][k]
                for k in default["giveaway"]:
                    if k not in data["giveaway"]:
                        data["giveaway"][k] = default["giveaway"][k]
                if not isinstance(data["bot_settings"].get("channels"), list):
                    data["bot_settings"]["channels"] = []
                if not isinstance(data.get("scheduled_posts"), list):
                    data["scheduled_posts"] = []
                if not isinstance(data.get("scheduled_deletions"), list):
                    data["scheduled_deletions"] = []
                return data
        except Exception as e:
            logger.error(f"DB load error: {e}")
            return get_default_db()
    return get_default_db()

def save_db(data):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        with open(f"{DB_FILE}.backup", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
            logger.error(f"DB save error: {e}")

db = load_db()
file_database = db["file_database"]
registered_users = set(db["registered_users"])
banned_users = set(db.get("banned_users", []))
user_activity = db.get("user_activity", {})
signup_log = db.get("signup_log", {})
bot_settings = db["bot_settings"]
scheduled_messages = db.get("scheduled_messages", [])
scheduled_posts = db.get("scheduled_posts", [])
scheduled_deletions = db.get("scheduled_deletions", [])
giveaway = db.get("giveaway", {"active": False, "prize": "", "participants": [], "end_time": None, "winners": []})
referrals = db.get("referrals", {})
user_states = {}
data_lock = threading.Lock()

def sync_db():
    with data_lock:
        db["file_database"] = file_database
        db["project_files"] = db.get("project_files", [])
        db.setdefault("admin_sessions", {})
        db["registered_users"] = list(registered_users)
        db["banned_users"] = list(banned_users)
        db["user_activity"] = user_activity
        db["bot_settings"] = bot_settings
        db["scheduled_messages"] = scheduled_messages
        db["scheduled_posts"] = scheduled_posts
        db["scheduled_deletions"] = scheduled_deletions
        db["giveaway"] = giveaway
        db["referrals"] = referrals
        db["signup_log"] = signup_log
        save_db(db)

def auto_backup():
    while True:
        try:
            time.sleep(BACKUP_INTERVAL)
            sync_db()
        except Exception as e:
            logger.error(f"Backup error: {e}")

def log_user_activity(user_id, action):
    with data_lock:
        if user_id not in user_activity:
            user_activity[user_id] = {"total_actions": 0, "last_active": None, "actions": [], "points": 0, "rank": "Member"}
        user_activity[user_id]["total_actions"] += 1
        user_activity[user_id]["last_active"] = datetime.now().isoformat()
        if "points" not in user_activity[user_id]:
            user_activity[user_id]["points"] = 0
        if action not in ["voice_generated", "daily_reward"]:
            user_activity[user_id]["points"] += 1
        p = user_activity[user_id]["points"]
        if p >= 100:
            user_activity[user_id]["rank"] = "💎 Diamond"
        elif p >= 50:
            user_activity[user_id]["rank"] = "🥇 Gold"
        elif p >= 25:
            user_activity[user_id]["rank"] = "🥈 Silver"
        elif p >= 10:
            user_activity[user_id]["rank"] = "🥉 Bronze"
    sync_db()

def is_user_banned(user_id):
    return user_id in banned_users

# =====================================================
#           🎨 KEYBOARDS
# =====================================================

def get_join_keyboard(file_id_param=None, unjoined=None):
    markup = InlineKeyboardMarkup(row_width=2)
    if unjoined is None:
        unjoined = get_all_channels()
    join_buttons = []
    for ch in unjoined:
        url = ch.get("join_url", "").strip()
        name = ch.get("name", "Channel")
        if url and (url.startswith("http://") or url.startswith("https://")):
            btn_text = (ch.get("join_btn_name") or "").strip() or f"📢 {name}"
            join_buttons.append(InlineKeyboardButton(text=btn_text, url=url))
    for i in range(0, len(join_buttons), 2):
        if i + 1 < len(join_buttons):
            markup.row(join_buttons[i], join_buttons[i + 1])
        else:
            markup.row(join_buttons[i])
    callback = f"verify_{file_id_param}" if file_id_param else "check_verification"
    markup.row(InlineKeyboardButton(text="✅ Verify & Continue", callback_data=callback))
    return markup

def get_main_keyboard():
    if not bot_settings.get("user_limit_enabled", True):
        return telebot.types.ReplyKeyboardRemove()
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("🎙️ Voice"), KeyboardButton("📊 Stats"), KeyboardButton("🎁 Daily Reward"))
    markup.add(KeyboardButton("ℹ️ Help"), KeyboardButton("👤 Profile"), KeyboardButton("🏆 Leaderboard"))
    return markup

def get_login_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔑 Login as Admin", callback_data="admin_login"),
        InlineKeyboardButton("👤 Continue as User", callback_data="continue_as_user")
    )
    markup.add(InlineKeyboardButton("💾 Save & Auto-Login", callback_data="admin_login_save"))
    return markup

def get_admin_panel_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    
    auto = "🔄 Auto-Login: ✅" if is_admin_logged_in(ADMIN_ID) else "🔄 Auto-Login: ❌"
    fs = "✅ Force Join: ON" if bot_settings["force_join"] else "❌ Force Join: OFF"
    ms = "🛠️ Maintenance: ON" if bot_settings.get("maintenance_mode", False) else "🛠️ Maintenance: OFF"
    
    markup.add(
        InlineKeyboardButton(text=auto, callback_data="auto_login_status"),
        InlineKeyboardButton(text=fs, callback_data="toggle_force_join")
    )
    markup.add(
        InlineKeyboardButton(text=ms, callback_data="toggle_maintenance"),
        InlineKeyboardButton("📊 Stats", callback_data="view_stats")
    )
    
    markup.add(
        InlineKeyboardButton("📢 Channel Manager", callback_data="channel_manager"),
        InlineKeyboardButton("🔗 Connect Channel", callback_data="conn_main")
    )
    user_limit_label = "👥 User Limit: ON" if bot_settings.get("user_limit_enabled", True) else "👥 User Limit: OFF"
    markup.add(
        InlineKeyboardButton(user_limit_label, callback_data="toggle_user_limit"),
        InlineKeyboardButton("📁 Project Files", callback_data="project_files_main")
    )
    
    markup.add(
        InlineKeyboardButton("🤖 AI Auto-Reply", callback_data="ub_main"),
        InlineKeyboardButton("🔄 Reset Userbot", callback_data="ub_reset")
    )

    markup.add(
        InlineKeyboardButton("🎭 Reaction Bot", callback_data="rb_main"),
        InlineKeyboardButton("🗑️ Clear All Data", callback_data="admin_clear_data")
    )
    
    markup.add(
        InlineKeyboardButton("📝 Join Text Editor", callback_data="join_text_editor"),
        InlineKeyboardButton("👋 Welcome Msg Editor", callback_data="welcome_editor")
    )
    
    markup.add(
        InlineKeyboardButton("📤 Auto Post Scheduler", callback_data="aps_main"),
        InlineKeyboardButton("🗑️ Delete Channel Msg", callback_data="del_main")
    )
    
    markup.add(
        InlineKeyboardButton("🔑 Create Key", callback_data="admin_create_key"),
        InlineKeyboardButton("📋 View Files", callback_data="admin_view_files")
    )
    markup.add(
        InlineKeyboardButton("🗑️ Delete File", callback_data="admin_delete_file"),
        InlineKeyboardButton("🔑 Keys Count", callback_data="admin_view_keys_count")
    )
    
    markup.add(
        InlineKeyboardButton("👥 Users", callback_data="admin_view_users"),
        InlineKeyboardButton("🎁 Giveaway", callback_data="admin_giveaway")
    )
    markup.add(
        InlineKeyboardButton("⛔ Ban User", callback_data="admin_ban_user"),
        InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_user")
    )
    
    markup.add(
        InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
        InlineKeyboardButton("📝 Auto Reply", callback_data="admin_auto_reply")
    )
    
    markup.add(
        InlineKeyboardButton("📋 Settings", callback_data="view_settings"),
        InlineKeyboardButton("👑 Admins", callback_data="admin_manage_admins")
    )
    
    markup.add(
        InlineKeyboardButton("🚪 Logout", callback_data="admin_logout"),
        InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")
    )
    
    return markup

def get_project_files_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("➕ Add Project", callback_data="project_add"))
    markup.add(InlineKeyboardButton("📋 View Projects", callback_data="project_view"))
    markup.add(InlineKeyboardButton("🗑️ Delete Project", callback_data="project_delete"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    return markup


def get_project_confirm_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("💾 Save", callback_data="project_save"),
        InlineKeyboardButton("✖️ Close", callback_data="project_close")
    )
    return markup


def _project_file_kind(message):
    if message.document:
        return message.document.file_id, "document", message.document.file_name or "document"
    if message.photo:
        return message.photo[-1].file_id, "photo", "photo.jpg"
    if message.video:
        return message.video.file_id, "video", message.video.file_name or "video.mp4"
    if message.audio:
        return message.audio.file_id, "audio", message.audio.file_name or "audio"
    if message.voice:
        return message.voice.file_id, "voice", "voice.ogg"
    if message.animation:
        return message.animation.file_id, "animation", message.animation.file_name or "animation.mp4"
    if message.sticker:
        return message.sticker.file_id, "sticker", "sticker.webp"
    if getattr(message, "video_note", None):
        return message.video_note.file_id, "video_note", "video_note.mp4"
    return None, None, None


def _send_project_file(chat_id, item):
    fid = item.get("file_id")
    kind = item.get("file_type", "document")
    caption = f"📁 {html.escape(item.get('name', 'Project'))}\n📎 {html.escape(item.get('file_name', 'file'))}"
    methods = {
        "document": bot.send_document, "photo": bot.send_photo,
        "video": bot.send_video, "audio": bot.send_audio,
        "voice": bot.send_voice, "animation": bot.send_animation,
        "sticker": bot.send_sticker, "video_note": bot.send_video_note
    }
    method = methods.get(kind, bot.send_document)
    if kind in ("sticker", "video_note"):
        method(chat_id, fid)
    else:
        method(chat_id, fid, caption=caption)


def get_rb_main_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("➕ Add Bot", callback_data="rb_add"))
    markup.add(InlineKeyboardButton("💾 Save Bot Tokens", callback_data="rb_saved_main"))
    markup.add(InlineKeyboardButton("👁️ Preview Bot", callback_data="rb_preview"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    return markup

def get_rb_remove_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    bots_list = bot_settings.get("reaction_bots", [])
    if not bots_list:
        markup.add(InlineKeyboardButton("❌ No bots found", callback_data="rb_main"))
    else:
        for i, b in enumerate(bots_list):
            emoji = b.get('emoji', '❓')
            bot_id = b.get('bot_id', 'Unknown')
            markup.add(InlineKeyboardButton(f"🗑️ Delete {emoji} {bot_id}", callback_data=f"rb_del_{i}"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="rb_main"))
    return markup

def get_ub_main_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("➕ Connect New AI", callback_data="ub_connect"))
    markup.add(InlineKeyboardButton("📋 AI Session List", callback_data="ub_list"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    return markup

def get_welcome_editor_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✏️ Edit Welcome Text", callback_data="welcome_edit_text"),
        InlineKeyboardButton("👁️ Live Preview", callback_data="welcome_preview")
    )
    markup.add(InlineKeyboardButton("🔄 Reset to Default", callback_data="welcome_reset"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    return markup

def get_conn_main_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("➕ Setup New Connection", callback_data="conn_setup"))
    if bot_settings.get("conn_dest"):
        markup.add(InlineKeyboardButton("📊 View Active Connection", callback_data="conn_view"))
        markup.add(InlineKeyboardButton("⚙️ Copy Settings (Filters)", callback_data="conn_settings"))
        markup.add(InlineKeyboardButton("🔴 Remove Connection", callback_data="conn_remove"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    return markup

def get_conn_settings_keyboard():
    settings = bot_settings.get("conn_settings", {"copy_text": True, "copy_files": True, "copy_links": True})
    t_txt = "✅ Copy Text: ON" if settings.get("copy_text", True) else "❌ Copy Text: OFF"
    t_file = "✅ Copy Files: ON" if settings.get("copy_files", True) else "❌ Copy Files: OFF"
    t_link = "✅ Copy Links: ON" if settings.get("copy_links", True) else "❌ Copy Links: OFF"
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(t_txt, callback_data="conn_toggle_text"))
    markup.add(InlineKeyboardButton(t_file, callback_data="conn_toggle_files"))
    markup.add(InlineKeyboardButton(t_link, callback_data="conn_toggle_links"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="conn_main"))
    return markup

def get_channel_manager_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("➕ Add Channel 1", callback_data="ch_add_1"),
        InlineKeyboardButton("➕ Add Channel 2", callback_data="ch_add_2")
    )
    markup.add(InlineKeyboardButton("➕ Add More Channel (3, 4, 5...)", callback_data="ch_add_more"))
    markup.add(InlineKeyboardButton("📋 View All Channels", callback_data="ch_view_all"))
    markup.add(InlineKeyboardButton("🗑️ Delete a Channel", callback_data="ch_delete_menu"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    return markup

def get_channel_type_keyboard(slot):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🌐 Public Channel", callback_data=f"chtype_public_{slot}"),
        InlineKeyboardButton("🔒 Private Channel", callback_data=f"chtype_private_{slot}")
    )
    markup.add(InlineKeyboardButton("🔙 Cancel", callback_data="channel_manager"))
    return markup

def get_channel_type_keyboard_for_more(name):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🌐 Public Channel", callback_data="chtype_more_public"),
        InlineKeyboardButton("🔒 Private Channel", callback_data="chtype_more_private")
    )
    markup.add(InlineKeyboardButton("🔙 Cancel", callback_data="channel_manager"))
    return markup

def get_join_text_editor_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✏️ Edit Title", callback_data="jtext_edit_title"),
        InlineKeyboardButton("✏️ Edit Message", callback_data="jtext_edit_msg")
    )
    markup.add(
        InlineKeyboardButton("🔘 Edit Button Names", callback_data="jtext_edit_btns")
    )
    markup.add(
        InlineKeyboardButton("👁️ Preview", callback_data="jtext_preview"),
        InlineKeyboardButton("🔄 Reset", callback_data="jtext_reset")
    )
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    return markup

def get_aps_main_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("📄 File Post", callback_data="aps_type_file"))
    markup.add(InlineKeyboardButton("🖼️ Photo Post", callback_data="aps_type_photo"))
    markup.add(InlineKeyboardButton("🎬 Video Post", callback_data="aps_type_video"))
    markup.add(InlineKeyboardButton("🔄 Forward Post (Auto)", callback_data="aps_type_forward")) # NEW
    markup.add(InlineKeyboardButton("📋 My Admin Channels", callback_data="aps_my_admin_channels"))
    markup.add(InlineKeyboardButton("📅 View Scheduled Posts", callback_data="aps_view_scheduled"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    return markup

def get_aps_time_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("⏱️ Minutes", callback_data="aps_cat_min"),
        InlineKeyboardButton("🕐 Hours", callback_data="aps_cat_hour"),
        InlineKeyboardButton("📅 Days", callback_data="aps_cat_day")
    )
    markup.add(InlineKeyboardButton("🔙 Cancel", callback_data="aps_main"))
    return markup

def get_aps_caption_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✍️ Add Text", callback_data="aps_cap_text"),
        InlineKeyboardButton("🎤 Add Voice", callback_data="aps_cap_voice")
    )
    markup.add(
        InlineKeyboardButton("➕ Add Custom Buttons", callback_data="aps_cap_add_btn"),
        InlineKeyboardButton("↩️ Add Reply", callback_data="aps_cap_reply")
    )
    markup.add(InlineKeyboardButton("🔗 Set Join Button Link", callback_data="aps_cap_join_btn"))
    markup.add(InlineKeyboardButton("⏭️ Skip", callback_data="aps_cap_skip"))
    return markup

def get_aps_after_voice_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✍️ Add File Text", callback_data="aps_cap_text"),
        InlineKeyboardButton("📝 Add Voice Text", callback_data="aps_cap_vtext")
    )
    markup.add(
        InlineKeyboardButton("➕ Add File Buttons", callback_data="aps_cap_add_btn"),
        InlineKeyboardButton("↩️ Add Reply", callback_data="aps_cap_reply")
    )
    markup.add(InlineKeyboardButton("🔗 Set Join Button Link", callback_data="aps_cap_join_btn"))
    markup.add(
        InlineKeyboardButton("🔗 Add Voice Buttons", callback_data="aps_cap_add_vbtn")
    )
    markup.add(InlineKeyboardButton("⏭️ Next / Skip", callback_data="aps_cap_skip"))
    return markup

def get_aps_after_text_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🎤 Add Voice", callback_data="aps_cap_voice"),
        InlineKeyboardButton("📝 Add Voice Text", callback_data="aps_cap_vtext")
    )
    markup.add(
        InlineKeyboardButton("➕ Add Custom Buttons", callback_data="aps_cap_add_btn"),
        InlineKeyboardButton("↩️ Add Reply", callback_data="aps_cap_reply")
    )
    markup.add(InlineKeyboardButton("⏭️ Next / Skip", callback_data="aps_cap_skip"))
    return markup

def get_del_time_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("⏱️ Minutes", callback_data="del_cat_min"),
        InlineKeyboardButton("🕐 Hours", callback_data="del_cat_hour"),
        InlineKeyboardButton("📅 Days", callback_data="del_cat_day")
    )
    markup.add(InlineKeyboardButton("🔙 Cancel", callback_data="back_to_main"))
    return markup

# =====================================================
#           📁 FILE SENDING
# =====================================================

def send_target_file(chat_id, file_key):
    if file_key not in file_database:
        bot.send_message(chat_id, "❌ Link expired or file not found.", reply_markup=get_main_keyboard())
        return False
    data = file_database[file_key]
    if data.get("is_text_key"):
        panel = data.get("panel_name", "PANEL")
        key = data.get("secret_value", "NO KEY")
        msg = (
            f"🔐 **{panel}**\n\n"
            "✅ Verification Successful!\n"
            "🔑 **Your Secret Key:**\n\n"
            f"`{key}`"
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📋 Copy Secret Key", callback_data=f"copy_val|{key}"))
        bot.send_message(chat_id, msg, parse_mode="Markdown", reply_markup=markup)
        log_user_activity(chat_id, "secret_key_accessed")
        return True
    else:
        fid = data["file_id"]
        ftype = data["file_type"]
        fname = data.get("file_name", "file")
        safe_fname = html.escape(fname)
        msg = (
            "✅ **Verification Successful!**\n\n"
            f"📁 File: `{safe_fname}`\n"
            "📥 Your file is ready!"
        )
        bot.send_message(chat_id, msg, parse_mode="Markdown", reply_markup=get_main_keyboard())
        try:
            if ftype == 'document':
                bot.send_document(chat_id, fid)
            elif ftype == 'photo':
                bot.send_photo(chat_id, fid)
            elif ftype == 'video':
                bot.send_video(chat_id, fid)
            elif ftype == 'audio':
                bot.send_audio(chat_id, fid)
            elif ftype == 'sticker':
                bot.send_sticker(chat_id, fid)
            elif ftype == 'animation':
                bot.send_animation(chat_id, fid)
            log_user_activity(chat_id, "file_accessed")
            data["downloads"] = data.get("downloads", 0) + 1
            sync_db()
            return True
        except Exception as e:
            logger.error(f"File send error: {e}")
            bot.send_message(chat_id, "❌ File send error.")
    return False

# =====================================================
#           📝 LIVE UPDATERS
# =====================================================

def generate_scheduled_text(pending):
    msg = "📅 **Scheduled Posts:**\n━━━━━━━━━━━━━━━━━━━━━\n"
    markup = InlineKeyboardMarkup(row_width=1)
    for idx, item in enumerate(pending, 1):
        try:
            when = datetime.fromisoformat(item["scheduled_time"])
            remain = when - datetime.now()
            secs = int(remain.total_seconds())
            if secs > 0:
                d = secs // 86400
                h = (secs % 86400) // 3600
                m = (secs % 3600) // 60
                s = secs % 60
                parts = []
                if d > 0: parts.append(f"{d}d")
                if h > 0: parts.append(f"{h}h")
                if m > 0: parts.append(f"{m}m")
                if s > 0 or (d==0 and h==0 and m==0): parts.append(f"{s}s") 
                remain_str = " ".join(parts)
            else:
                remain_str = "Sending..."
        except:
            remain_str = "?"
        ptype = item.get("post_type", "file")
        emoji = {"file": "📄", "photo": "🖼️", "video": "🎬", "forward": "🔄"}.get(ptype, "📄")
        
        files = item.get("files", [])
        if len(files) == 1:
            file_text = f"`{html.escape(files[0]['file_name'])}`"
        elif len(files) > 1:
            file_text = f"`{len(files)}` files"
        else:
            file_text = "No files"
            
        msg += f"\n{idx}. {emoji} **{file_text}**\n"
        
        ch_ids = item.get("channel_ids", [])
        if ch_ids:
            for ch in ch_ids:
                ch_title = ch.get("title", "Unknown")
                msg += f"   📢 Channel: **{html.escape(ch_title)}**\n"
        else:
            msg += f"   📢 Channel: **None**\n"
            
        msg += f"   ⏰ In: **{remain_str}**\n"
        
        # 🔥 Added Edit Button here 🔥
        markup.add(InlineKeyboardButton(
            text=f"❌ Cancel #{idx}",
            callback_data=f"aps_cancel_{item['id']}"
        ))
        markup.add(InlineKeyboardButton(
            text=f"✏️ Edit Post #{idx}",
            callback_data=f"aps_edit_{item['id']}"
        ))
        # 🔥 END 🔥

    markup.add(InlineKeyboardButton("🔄 Refresh", callback_data="aps_view_scheduled"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="aps_main"))
    return msg, markup

def generate_deletion_text(pending):
    msg = "🗑️ **Scheduled Deletions:**\n━━━━━━━━━━━━━━━━━━━━━\n"
    markup = InlineKeyboardMarkup(row_width=1)
    for idx, item in enumerate(pending, 1):
        try:
            when = datetime.fromisoformat(item["delete_at"])
            remain = when - datetime.now()
            secs = int(remain.total_seconds())
            if secs > 0:
                d = secs // 86400
                h = (secs % 86400) // 3600
                m = (secs % 3600) // 60
                s = secs % 60
                parts = []
                if d > 0: parts.append(f"{d}d")
                if h > 0: parts.append(f"{h}h")
                if m > 0: parts.append(f"{m}m")
                if s > 0 or (d==0 and h==0 and m==0): parts.append(f"{s}s") 
                remain_str = " ".join(parts)
            else:
                remain_str = "Deleting..."
        except:
            remain_str = "?"
        
        msg += f"\n{idx}. 📢 **{html.escape(item.get('channel_title', 'Unknown'))}**\n"
        msg += f"   🆔 Message ID: `{item.get('message_id')}`\n"
        msg += f"   ⏰ Deletes in: **{remain_str}**\n"
        markup.add(InlineKeyboardButton(
            text=f"❌ Cancel Deletion #{idx}",
            callback_data=f"del_cancel_{item['id']}"
        ))
    markup.add(InlineKeyboardButton("🔄 Refresh", callback_data="del_view"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    return msg, markup

def live_updater():
    while True:
        try:
            time.sleep(3)
            
            if live_schedule_messages:
                for chat_id, msg_id in list(live_schedule_messages.items()):
                    pending = [x for x in scheduled_posts if not x.get("sent")]
                    if not pending:
                        try:
                            bot.edit_message_text("❌ No pending scheduled posts.", chat_id, msg_id)
                        except:
                            pass
                        if chat_id in live_schedule_messages:
                            del live_schedule_messages[chat_id]
                        continue
                    
                    msg, markup = generate_scheduled_text(pending)
                    try:
                        bot.edit_message_text(msg, chat_id, msg_id, parse_mode="Markdown", reply_markup=markup)
                    except Exception as e:
                        if "message is not modified" not in str(e).lower():
                            if chat_id in live_schedule_messages:
                                del live_schedule_messages[chat_id]

            if live_deletion_messages:
                for chat_id, msg_id in list(live_deletion_messages.items()):
                    pending = [x for x in scheduled_deletions if not x.get("deleted")]
                    if not pending:
                        try:
                            bot.edit_message_text("❌ No pending scheduled deletions.", chat_id, msg_id)
                        except:
                            pass
                        if chat_id in live_deletion_messages:
                            del live_deletion_messages[chat_id]
                        continue
                    
                    msg, markup = generate_deletion_text(pending)
                    try:
                        bot.edit_message_text(msg, chat_id, msg_id, parse_mode="Markdown", reply_markup=markup)
                    except Exception as e:
                        if "message is not modified" not in str(e).lower():
                            if chat_id in live_deletion_messages:
                                del live_deletion_messages[chat_id]

        except Exception as e:
            logger.error(f"Live updater error: {e}")

# =====================================================
#           📝 COMMAND HANDLERS
# =====================================================

@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        chat_id = message.chat.id
        user_id = message.from_user.id
        first_name = message.from_user.first_name

        try:
            bot.delete_message(chat_id, message.message_id)
        except:
            pass

        if is_user_banned(user_id):
            bot.send_message(chat_id, "⛔ You are banned!")
            return
        if user_id != ADMIN_ID and bot_settings.get("maintenance_mode", False):
            bot.send_message(chat_id, "🔧 Bot is under maintenance. Please try again later.")
            return

        user_states[chat_id] = None
        args = message.text.split()

        if len(args) > 1 and args[1].startswith("ref_"):
            try:
                ref_id = int(args[1].replace("ref_", ""))
                if ref_id != user_id and user_id not in registered_users:
                    if "referrals" not in db:
                        db["referrals"] = {}
                    if str(ref_id) not in db["referrals"]:
                        db["referrals"][str(ref_id)] = []
                    if user_id not in db["referrals"][str(ref_id)]:
                        db["referrals"][str(ref_id)].append(user_id)
                        sync_db()
                        bot.send_message(ref_id, f"🎉 New referral!")
            except:
                pass

        if user_id not in registered_users:
            registered_users.add(user_id)
            log_user_activity(user_id, "registered")
            today = datetime.now().strftime("%Y-%m-%d")
            signup_log[today] = signup_log.get(today, 0) + 1
            sync_db()

        file_key = args[1] if len(args) > 1 and not args[1].startswith("ref_") else None


        unjoined = get_unjoined_channels(user_id)
        if not unjoined:
            if file_key:
                send_target_file(chat_id, file_key)
            else:
                welcome_text = bot_settings.get("welcome_message", f"✨ Welcome to {BOT_NAME} Premium Bot! ✨\n\n👋 Hello {{name}}!")
                formatted_welcome = welcome_text.replace("{name}", first_name).replace("{id}", str(user_id))
                
                try:
                    bot.send_message(chat_id, formatted_welcome, reply_markup=get_main_keyboard(), parse_mode="HTML")
                except:
                    bot.send_message(chat_id, formatted_welcome, reply_markup=get_main_keyboard())
            return

        msg = get_join_message(unjoined)
        bot.send_message(chat_id, msg, reply_markup=get_join_keyboard(file_key, unjoined))
    except Exception as e:
        logger.error(f"Start error: {e}")

# =====================================================
#           📝 CALLBACK HANDLER
# =====================================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    # GLOBAL DECLARATION FIX
    global db, file_database, registered_users, banned_users, user_activity, bot_settings, scheduled_messages, scheduled_posts, scheduled_deletions, giveaway, referrals

    try:
        chat_id = call.message.chat.id
        user_id = call.from_user.id
        message_id = call.message.message_id

        try:
            bot.answer_callback_query(call.id)
        except:
            pass

        if call.data.startswith("pubch|"):
            st = user_states.get(user_id, {})
            if st.get("stage") != "managed_admin_choose_channel":
                bot.answer_callback_query(call.id, "Session expired. Send /publish again.", show_alert=True)
                return
            try:
                idx = int(call.data.split("|", 1)[1])
                selected = st.get("channels", [])[idx]
                channel_id = str(selected["id"])
            except Exception:
                bot.answer_callback_query(call.id, "Invalid channel selection.", show_alert=True)
                return
            bucket = bot_settings.get("channel_managed_admins", {}).get(channel_id, {})
            policy = bucket.get(str(user_id))
            if not policy:
                user_states.pop(user_id, None)
                bot.send_message(user_id, "❌ You are not registered as a managed admin for this channel.")
                return
            user_states[user_id] = {"stage": "managed_admin_wait_content", "channel_id": channel_id}
            bot.edit_message_text(f"📢 Selected channel: {selected.get('name', channel_id)}\nNow send ONE text, photo, video, voice or file to publish. Send /cancel to stop.", chat_id, message_id)
            return

        if is_user_banned(user_id) and user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "⛔ Banned!", show_alert=True)
            return

        if user_id != ADMIN_ID and bot_settings.get("maintenance_mode", False):
            bot.answer_callback_query(call.id, "🔧 Bot is under maintenance. Please try again later.", show_alert=True)
            return

        if call.data.startswith("copy_val|"):
            val = call.data.split("copy_val|", 1)[1]
            bot.answer_callback_query(call.id, text=f"✅ Copied!\n\n{val}", show_alert=True)
            return

        if call.data.startswith("copy_key|"):
            key = call.data.split("copy_key|", 1)[1]
            try:
                bi = bot.get_me()
                link = f"https://t.me/{bi.username}?start={key}"
                bot.answer_callback_query(call.id, text="✅ Link sent below! Tap to copy.")
                bot.send_message(chat_id, f"🔗 Your Link:\n`{link}`", parse_mode="Markdown")
            except Exception as e:
                bot.answer_callback_query(call.id, text=f"❌ Error generating link.", show_alert=True)
            return

        if call.data.startswith("verify_"):
            file_key = call.data.split("verify_")[-1]
            unjoined = get_unjoined_channels(user_id)
            if not unjoined:
                try:
                    bot.delete_message(chat_id, message_id)
                except:
                    pass
                if file_key and file_key != "None":
                    send_target_file(chat_id, file_key)
                else:
                    welcome_text = bot_settings.get("welcome_message", f"✨ Welcome to {BOT_NAME} Premium Bot! ✨\n\n👋 Hello {{name}}!")
                    formatted_welcome = welcome_text.replace("{name}", call.from_user.first_name).replace("{id}", str(user_id))
                    try:
                        bot.send_message(chat_id, formatted_welcome, reply_markup=get_main_keyboard(), parse_mode="HTML")
                    except:
                        bot.send_message(chat_id, formatted_welcome, reply_markup=get_main_keyboard())
                bot.answer_callback_query(call.id, "✅ Verified!")
            else:
                bot.answer_callback_query(call.id, f"❌ Join {len(unjoined)} more!", show_alert=True)
                msg = get_join_message(unjoined)
                try:
                    bot.edit_message_text(msg, chat_id, message_id, reply_markup=get_join_keyboard(file_key, unjoined))
                except:
                    pass
            return

        if call.data == "check_verification":
            unjoined = get_unjoined_channels(user_id)
            if not unjoined:
                try:
                    bot.delete_message(chat_id, message_id)
                except:
                    pass
                welcome_text = bot_settings.get("welcome_message", f"✨ Welcome to {BOT_NAME} Premium Bot! ✨\n\n👋 Hello {{name}}!")
                formatted_welcome = welcome_text.replace("{name}", call.from_user.first_name).replace("{id}", str(user_id))
                try:
                    bot.send_message(chat_id, formatted_welcome, reply_markup=get_main_keyboard(), parse_mode="HTML")
                except:
                    bot.send_message(chat_id, formatted_welcome, reply_markup=get_main_keyboard())
                bot.answer_callback_query(call.id, "✅ Verified!")
            else:
                bot.answer_callback_query(call.id, f"❌ Join {len(unjoined)} more!", show_alert=True)
            return

        if call.data == "my_stats":
            if user_id in user_activity:
                s = user_activity[user_id]
                bot.send_message(chat_id, f"📊 **Stats:**\n🔄 {s['total_actions']}\n⭐ {s.get('points', 0)}\n🏅 {s.get('rank', 'Member')}")
            return
        if call.data == "daily_reward":
            today = datetime.now().date().isoformat()
            if user_activity.get(user_id, {}).get("last_daily") == today:
                bot.answer_callback_query(call.id, "❌ Already claimed!", show_alert=True)
                return
            if user_id not in user_activity:
                user_activity[user_id] = {"points": 0}
            user_activity[user_id]["points"] = user_activity[user_id].get("points", 0) + bot_settings.get("daily_reward", 10)
            user_activity[user_id]["last_daily"] = today
            sync_db()
            bot.answer_callback_query(call.id, f"🎁 +{bot_settings.get('daily_reward', 10)} points!")
            return
        if call.data == "get_referral":
            bi = bot.get_me()
            rl = f"https://t.me/{bi.username}?start=ref_{user_id}"
            rc = len(db.get("referrals", {}).get(str(user_id), []))
            bot.send_message(chat_id, f"🔗 `{rl}`\n\n📊 Count: {rc}", parse_mode="Markdown")
            return
        if call.data == "user_help":
            bot.send_message(chat_id, f"✨ {BOT_NAME}\n\n🎙️ Voice\n📊 Stats\n🎁 Daily\n🏆 Leaderboard")
            return

        # (all admin controls live in the web admin panel - admin.html)
    except Exception as e:
        logger.error(f"Callback Handler Error: {e}")
        try:
            bot.answer_callback_query(call.id, f"❌ Error: {e}", show_alert=True)
        except:
            pass

# =====================================================
#           📁 MESSAGE COPIER (CONNECT CHANNEL) & REACTION
# =====================================================

def process_single_reaction(bot_data, message, delay=0):
    """Apply a configured reaction using the selected reaction-bot token."""
    if delay:
        time.sleep(delay)
    token = (bot_data.get("token") or "").strip()
    emoji = bot_data.get("emoji")
    bot_id = bot_data.get("bot_id", "Unknown")
    if not token or not emoji:
        logger.warning("Skipping reaction bot with missing token/emoji: %s", bot_id)
        return
    payload = {
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "reaction": [{"type": "emoji", "emoji": emoji}],
        "is_big": False
    }
    try:
        res = requests.post(f"https://api.telegram.org/bot{token}/setMessageReaction", json=payload, timeout=12)
        data = {}
        try:
            data = res.json()
        except Exception:
            pass
        if not res.ok or not data.get("ok", False):
            desc = data.get("description", res.text[:300] if res.text else "Unknown Telegram API error")
            logger.error("Reaction failed (%s) on chat %s message %s: %s", bot_id, message.chat.id, message.message_id, desc)
    except Exception as exc:
        logger.exception("Reaction request error (%s): %s", bot_id, exc)


def _reaction_channel_matches(configured_channel_id, message):
    """Match the configured channel itself and its linked discussion group."""
    if str(configured_channel_id) == str(message.chat.id):
        return True
    # A channel's replies live in its linked discussion supergroup. Resolve that
    # link so replies/forwarded posts in the discussion group can also react.
    try:
        cached = _reaction_link_cache.get(str(configured_channel_id))
        now = time.time()
        if not cached or now - cached[0] > 600:
            info = bot.get_chat(configured_channel_id)
            linked_id = getattr(info, "linked_chat_id", None)
            _reaction_link_cache[str(configured_channel_id)] = (now, linked_id)
        else:
            linked_id = cached[1]
        return linked_id is not None and str(linked_id) == str(message.chat.id)
    except Exception as exc:
        logger.debug("Could not resolve linked discussion group for %s: %s", configured_channel_id, exc)
        return False


_reaction_link_cache = {}


def schedule_reactions_for_message(message):
    """Schedule all reaction bots configured for this channel or linked discussion."""
    if not message or getattr(message.chat, "type", None) == "private":
        return
    try:
        for reaction_bot in list(bot_settings.get("reaction_bots", [])):
            channel_id = reaction_bot.get("channel_id")
            if channel_id is None or not _reaction_channel_matches(channel_id, message):
                continue
            delay = random.uniform(1.0, 2.5)
            threading.Thread(
                target=process_single_reaction,
                args=(reaction_bot, message, delay),
                daemon=True
            ).start()
    except Exception as exc:
        logger.exception("Reaction trigger error: %s", exc)


@bot.channel_post_handler(content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation', 'video_note', 'contact', 'location', 'venue', 'poll', 'dice'])
def auto_copy_source_messages(message):
    # React to manually published/forwarded posts in configured channels.
    schedule_reactions_for_message(message)

    # Copy source posts to the configured destination. Bot API does not send an
    # update back for the bot's own copied post, so trigger destination reactions
    # explicitly using the returned message ID.
    dest = bot_settings.get("conn_dest")
    if dest:
        try:
            bi = bot.get_me()
            if message.author_signature == bi.first_name or (message.from_user and message.from_user.id == bi.id):
                return
            if str(message.chat.id) == str(dest):
                return

            sources = bot_settings.get("conn_sources", [])
            if sources and str(message.chat.id) not in {str(x) for x in sources}:
                return

            settings = bot_settings.get("conn_settings", {"copy_text": True, "copy_files": True, "copy_links": True})
            is_text = bool(message.text)
            is_media = bool(message.photo or message.video or message.document or message.audio or message.voice or message.sticker or message.animation or getattr(message, "video_note", None))
            check_text = message.text or message.caption or ""
            has_link = bool(re.search(r"https?://|t\.me/", check_text, re.I))

            if is_text and not is_media:
                if not settings.get("copy_text", True):
                    return
                if has_link and not settings.get("copy_links", True):
                    return
            elif is_media:
                if not settings.get("copy_files", True):
                    return
                if has_link and not settings.get("copy_links", True):
                    return

            copied = bot.copy_message(dest, message.chat.id, message.message_id)
            copied_id = getattr(copied, "message_id", None)
            if copied_id:
                copied_message = SimpleNamespace(
                    chat=SimpleNamespace(id=int(dest), title="Connected destination", type="channel"),
                    message_id=copied_id
                )
                schedule_reactions_for_message(copied_message)
        except Exception as exc:
            logger.error("Auto copy failed from %s to %s: %s", message.chat.id, dest, exc)


@bot.message_handler(func=lambda m: getattr(m.chat, "type", None) in ("group", "supergroup"), content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation', 'video_note', 'contact', 'location', 'venue', 'poll', 'dice'])
def react_to_discussion_messages(message):
    # Includes ordinary group posts, forwarded files, and replies in a linked
    # channel discussion group. This handler intentionally does not copy them.
    schedule_reactions_for_message(message)

# =====================================================
# =====================================================
#   CHANNEL ADMIN UPLOAD GATEWAY + ENFORCEMENT
# =====================================================
PUBLISH_CONTENT_TYPES = ['text', 'photo', 'video', 'document', 'audio', 'voice', 'animation', 'video_note']


def managed_admin_buckets(user_id):
    """Return (channel_id, policy) pairs for admins registered by Channel Admin Manager."""
    out = []
    for channel_id, admins in bot_settings.get("channel_managed_admins", {}).items():
        policy = (admins or {}).get(str(user_id))
        if policy:
            out.append((str(channel_id), policy))
    return out


def _policy_usage(policy):
    usage = policy.setdefault("usage", {})
    counts = usage.setdefault("counts", {k: 0 for k in ("files", "photos", "videos", "voice", "text")})
    for key in ("files", "photos", "videos", "voice", "text"):
        counts.setdefault(key, 0)
    usage.setdefault("warnings", 0)
    return usage, counts


def _content_quota_keys(message):
    if getattr(message, "text", None) is not None:
        return ["text"]
    keys = ["files"]
    if getattr(message, "photo", None): keys.append("photos")
    elif getattr(message, "video", None) or getattr(message, "animation", None) or getattr(message, "video_note", None): keys.append("videos")
    elif getattr(message, "voice", None) or getattr(message, "audio", None): keys.append("voice")
    return keys


def _cooldown_seconds(policy):
    try:
        hours = float(policy.get("cooldown_hours", 0) or 0)
    except (TypeError, ValueError):
        hours = 0
    return max(1, min(hours * 3600, 8760 * 3600)) if hours > 0 else 3600


def _restore_managed_admin(channel_id, user_id, policy):
    try:
        until = float(policy.get("suspended_until", 0) or 0)
        if until and time.time() < until:
            return False
        rights = dict(policy.get("permissions") or {})
        # Publishing is routed through /publish so per-user quotas are enforceable.
        # Do not restore direct posting/editing rights, which would bypass the gateway.
        rights["can_post_messages"] = False
        rights["can_edit_messages"] = False
        bot.promote_chat_member(channel_id, int(user_id), **{k: bool(v) for k, v in rights.items()})
        reason = policy.pop("suspension_reason", "")
        policy.pop("suspended_until", None)
        usage, counts = _policy_usage(policy)
        usage["warnings"] = 0
        if reason == "ALL UPLOAD LIMITS REACHED":
            for key in counts:
                counts[key] = 0
        policy["last_restored_at"] = datetime.now().isoformat()
        sync_db()
        try:
            bot.send_message(int(user_id), "✅ Your channel admin permissions have been restored. You can publish again now.")
        except Exception:
            pass
        return True
    except Exception as exc:
        logger.exception("Could not restore managed admin %s in %s: %s", user_id, channel_id, exc)
        return False


def _suspend_managed_admin(channel_id, user_id, policy, reason):
    """Temporarily revoke every permission granted by this manager, then restore after cooldown."""
    if policy.get("suspended_until") and float(policy.get("suspended_until", 0) or 0) > time.time():
        return
    seconds = _cooldown_seconds(policy)
    until = time.time() + seconds
    try:
        rights = policy.get("permissions") or {}
        # Clear every right known to this manager. This affects only admins recorded by this manager.
        bot.promote_chat_member(channel_id, int(user_id), **{k: False for k in rights.keys()})
        policy["suspended_until"] = until
        policy["suspension_reason"] = reason
        policy["suspended_at"] = datetime.now().isoformat()
        sync_db()
        wait_text = str(timedelta(seconds=int(seconds)))
        bot.send_message(int(user_id), f"⛔ ADMIN LIMIT REACHED\nYour channel admin permissions are temporarily disabled.\nReason: {reason}\nWait now: {wait_text}\nYour permissions will be restored automatically after the cooldown.")
        try:
            bot.send_message(ADMIN_ID, f"⛔ Managed admin suspended\nChannel ID: {channel_id}\nUser ID: {user_id}\nReason: {reason}\nCooldown: {wait_text}")
        except Exception:
            pass
        timer = threading.Timer(seconds, _restore_managed_admin, args=(channel_id, user_id, policy))
        timer.daemon = True
        timer.start()
    except Exception as exc:
        logger.exception("Could not suspend managed admin %s in %s: %s", user_id, channel_id, exc)
        try:
            bot.send_message(int(user_id), "⚠️ Your upload was blocked, but Telegram did not allow the bot to change your admin rights. Please contact the channel owner.")
        except Exception:
            pass


def _all_enabled_quotas_exhausted(policy):
    usage, counts = _policy_usage(policy)
    limits = policy.get("limits") or {}
    enabled = [k for k in ("files", "photos", "videos", "voice", "text") if int(limits.get(k, 0) or 0) > 0]
    return bool(enabled) and all(counts.get(k, 0) >= int(limits.get(k, 0) or 0) for k in enabled)


def _publish_admin_message(message, channel_id, policy):
    user_id = message.from_user.id
    usage, counts = _policy_usage(policy)
    if policy.get("suspended_until"):
        until = float(policy.get("suspended_until", 0) or 0)
        if until > time.time():
            remaining = str(timedelta(seconds=max(0, int(until - time.time()))))
            bot.send_message(user_id, f"⛔ ADMIN ACCESS TEMPORARILY DISABLED\nWait now: {remaining}")
            return
        _restore_managed_admin(channel_id, user_id, policy)

    keys = _content_quota_keys(message)
    limits = policy.get("limits") or {}
    blocked = [k for k in keys if int(limits.get(k, 0) or 0) == 0 or counts.get(k, 0) >= int(limits.get(k, 0) or 0)]
    body = (getattr(message, "text", None) or getattr(message, "caption", None) or "")
    contains_link = bool(re.search(r"https?://|t\.me/|telegram\.me/", body, re.I))
    if policy.get("auto_delete_links") and contains_link:
        blocked.append("links")
    if blocked:
        # Best effort only: Telegram may not allow a bot to delete a user's private-chat message.
        try: bot.delete_message(message.chat.id, message.message_id)
        except Exception: pass
        usage["warnings"] = int(usage.get("warnings", 0) or 0) + 1
        sync_db()
        label = ", ".join(k.upper() for k in blocked)
        warning_no = usage["warnings"]
        detail = "Links are not allowed by your channel policy." if "links" in blocked else "Your configured limit is reached or set to 0."
        bot.send_message(user_id, f"⚠️ WARNING {warning_no}/3: {label} UPLOAD NOT ALLOWED. {detail}")
        if _all_enabled_quotas_exhausted(policy):
            _suspend_managed_admin(channel_id, user_id, policy, "ALL UPLOAD LIMITS REACHED")
        elif warning_no >= 3:
            _suspend_managed_admin(channel_id, user_id, policy, "3 UPLOAD WARNINGS")
        return

    try:
        bot.copy_message(channel_id, message.chat.id, message.message_id)
        for key in keys:
            counts[key] = int(counts.get(key, 0) or 0) + 1
        usage["last_upload_at"] = datetime.now().isoformat()
        sync_db()
        bot.send_message(user_id, "✅ Published successfully.\nUsage: " + " | ".join(f"{k.upper()} {counts[k]}/{int(limits.get(k, 0) or 0)}" for k in keys))
        if _all_enabled_quotas_exhausted(policy):
            _suspend_managed_admin(channel_id, user_id, policy, "ALL UPLOAD LIMITS REACHED")
    except Exception as exc:
        logger.exception("Managed admin publish failed: %s", exc)
        bot.send_message(user_id, "❌ Could not publish. Check that the bot can post in the selected channel.")


def _enforce_gateway_for_existing_admins():
    """On deploy, remove direct post/edit rights from previously managed admins."""
    time.sleep(8)
    for channel_id, admins in list(bot_settings.get("channel_managed_admins", {}).items()):
        for user_id, policy in list((admins or {}).items()):
            try:
                if float((policy or {}).get("suspended_until", 0) or 0) > time.time():
                    continue
                rights = dict((policy or {}).get("permissions") or {})
                if not rights:
                    continue
                rights["can_post_messages"] = False
                rights["can_edit_messages"] = False
                bot.promote_chat_member(str(channel_id), int(user_id), **{k: bool(v) for k, v in rights.items()})
            except Exception as exc:
                logger.warning("Could not enforce publish gateway for existing admin %s in %s: %s", user_id, channel_id, exc)


def _managed_admin_restore_loop():
    """Recover expired temporary suspensions after process restarts as well."""
    while True:
        try:
            for channel_id, admins in list(bot_settings.get("channel_managed_admins", {}).items()):
                for user_id, policy in list((admins or {}).items()):
                    until = float((policy or {}).get("suspended_until", 0) or 0)
                    if until and until <= time.time():
                        _restore_managed_admin(str(channel_id), str(user_id), policy)
        except Exception:
            logger.exception("Managed admin cooldown recovery loop failed")
        time.sleep(20)


threading.Thread(target=_enforce_gateway_for_existing_admins, daemon=True, name="managed-admin-gateway").start()
threading.Thread(target=_managed_admin_restore_loop, daemon=True, name="managed-admin-restore").start()


def _start_admin_publish(message):
    user_id = message.from_user.id
    targets = managed_admin_buckets(user_id)
    if not targets:
        return False
    available = []
    for channel_id, policy in targets:
        until = float(policy.get("suspended_until", 0) or 0)
        if until > time.time():
            continue
        if until:
            _restore_managed_admin(channel_id, user_id, policy)
        try:
            chat = bot.get_chat(channel_id)
            available.append((channel_id, chat.title or str(channel_id), policy))
        except Exception:
            continue
    if not available:
        bot.send_message(user_id, "⛔ No channel is currently available. Your admin access may be in cooldown or the bot cannot access the channel.")
        return True
    if len(available) == 1:
        channel_id, title, policy = available[0]
        user_states[user_id] = {"stage": "managed_admin_wait_content", "channel_id": channel_id}
        bot.send_message(user_id, f"📢 Selected channel: {title}\nNow send ONE text, photo, video, voice or file to publish. Send /cancel to stop.")
        return True
    markup = InlineKeyboardMarkup(row_width=1)
    for i, (_, title, _) in enumerate(available):
        markup.add(InlineKeyboardButton(title[:50], callback_data=f"pubch|{i}"))
    user_states[user_id] = {"stage": "managed_admin_choose_channel", "channels": [{"id": x[0], "name": x[1]} for x in available]}
    bot.send_message(user_id, "Choose the channel where you want to publish:", reply_markup=markup)
    return True


@bot.message_handler(func=lambda m: True, content_types=["text", "photo", "video", "document", "audio", "voice", "animation", "video_note", "sticker", "contact", "location", "venue", "poll", "dice"])
def handle_messages(message):
    try:
        if message.chat.type != 'private':
            return
            
        chat_id = message.chat.id
        user_id = message.from_user.id
        text = message.text

        # Managed channel admins must publish through /publish so limits can be
        # attributed to their Telegram User ID. Direct channel posts cannot be
        # reliably attributed by the Bot API.
        state = user_states.get(user_id, {})
        if text == "/cancel" and state.get("stage", "").startswith("managed_admin_"):
            user_states.pop(user_id, None)
            bot.send_message(user_id, "Cancelled.")
            return
        if text == "/publish":
            if not _start_admin_publish(message):
                bot.send_message(user_id, "This account is not registered as a managed channel admin.")
            return
        if state.get("stage") == "managed_admin_wait_content":
            if text and text.startswith("/"):
                bot.send_message(user_id, "Send one post or use /cancel.")
                return
            channel_id = state.get("channel_id")
            policy = bot_settings.get("channel_managed_admins", {}).get(str(channel_id), {}).get(str(user_id))
            user_states.pop(user_id, None)
            if not policy:
                bot.send_message(user_id, "❌ Your managed admin policy was not found. Contact the owner.")
                return
            _publish_admin_message(message, str(channel_id), policy)
            return

        if is_user_banned(user_id):
            bot.send_message(chat_id, "⛔ Banned!")
            return
        if user_id != ADMIN_ID and bot_settings.get("maintenance_mode", False):
            bot.send_message(chat_id, "🔧 Maintenance.")
            return

        if user_id not in registered_users:
            registered_users.add(user_id)
            log_user_activity(user_id, "registered")
            today = datetime.now().strftime("%Y-%m-%d")
            signup_log[today] = signup_log.get(today, 0) + 1
            sync_db()

        if text:
            ar = bot_settings.get("auto_reply", {})
            for k, r in ar.items():
                if k.lower() in text.lower():
                    bot.send_message(chat_id, r)
                    return

        st = user_states.get(chat_id)

        if True:
            unjoined = get_unjoined_channels(user_id)
            if unjoined:
                msg = get_join_message(unjoined)
                bot.send_message(chat_id, msg, reply_markup=get_join_keyboard(None, unjoined))
                return

        if text and text.startswith("/"):
            c = text[1:].strip().lower()
            cmds = bot_settings.get("custom_commands", {})
            if c in cmds:
                bot.send_message(chat_id, cmds[c])
                return

        if text == "🎙️ Voice" or text == "/voice":
            if not GTTS_AVAILABLE:
                bot.send_message(chat_id, "❌ Not available!")
                return
            user_states[chat_id] = {"stage": "waiting_for_voice_text"}
            bot.send_message(chat_id, "🎤 Send text:", reply_markup=telebot.types.ReplyKeyboardRemove())
        elif text == "📊 Stats" or text == "/stats":
            if user_id in user_activity:
                s = user_activity[user_id]
                bot.send_message(chat_id, f"📊 **Stats:**\n🔄 {s['total_actions']}\n⭐ {s.get('points', 0)}\n🏅 {s.get('rank', 'Member')}")
        elif text == "🎁 Daily Reward" or text == "/daily":
            today = datetime.now().date().isoformat()
            if user_activity.get(user_id, {}).get("last_daily") == today:
                bot.send_message(chat_id, "❌ Already claimed!")
                return
            if user_id not in user_activity:
                user_activity[user_id] = {"points": 0}
            user_activity[user_id]["points"] = user_activity[user_id].get("points", 0) + bot_settings.get("daily_reward", 10)
            user_activity[user_id]["last_daily"] = today
            sync_db()
            bot.send_message(chat_id, f"🎁 +{bot_settings.get('daily_reward', 10)} points!")
        elif text == "🏆 Leaderboard" or text == "/leaderboard":
            if not user_activity:
                bot.send_message(chat_id, "❌ No users!")
                return
            su = sorted(user_activity.items(), key=lambda x: x[1].get("points", 0), reverse=True)[:10]
            m = "🏆 **Top 10:**\n"
            for i, (uid, d) in enumerate(su, 1):
                try:
                    u = bot.get_chat(int(uid))
                    n = u.first_name or "Unknown"
                except:
                    n = "Unknown"
                m += f"{i}. {n} - `{d.get('points', 0)}`\n"
            bot.send_message(chat_id, m, parse_mode="Markdown")
        elif text == "ℹ️ Help" or text == "/help":
            bot.send_message(chat_id, f"✨ **{BOT_NAME} Bot**\n\n🎙️ Voice\n📊 Stats\n🎁 Daily\n🏆 Leaderboard\n👤 Profile", reply_markup=get_main_keyboard())
        elif text == "👤 Profile" or text == "/profile":
            u = message.from_user
            p = user_activity.get(user_id, {}).get("points", 0)
            r = user_activity.get(user_id, {}).get("rank", "Member")
            bot.send_message(chat_id, f"👤 **Profile:**\n📛 {html.escape(u.first_name)}\n🆔 `{u.id}`\n⭐ `{p}`\n🏅 `{r}`", parse_mode="Markdown")
        elif isinstance(user_states.get(chat_id), dict) and user_states.get(chat_id).get("stage") == "waiting_for_voice_text":
            if not GTTS_AVAILABLE:
                bot.send_message(chat_id, "❌ Not available!")
                user_states[chat_id] = None
                return
            user_states[chat_id] = None
            sm = bot.send_message(chat_id, "🎵 Generating...")
            fp = f"voice_{chat_id}_{int(time.time())}.mp3"
            try:
                tts = gTTS(text=text, lang='bn', slow=False)
                tts.save(fp)
                with open(fp, 'rb') as a:
                    bot.send_audio(chat_id, a, caption="🎵 Voice!", reply_markup=get_main_keyboard())
                bot.delete_message(chat_id, sm.message_id)
            except:
                bot.send_message(chat_id, "❌ Failed!", reply_markup=get_main_keyboard())
            finally:
                if os.path.exists(fp):
                    os.remove(fp)
        else:
            bot.send_message(chat_id, "❌ Unknown. Use /help", reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Message Handler Error: {e}")

# =====================================================
#           🔄 BACKGROUND THREADS
# =====================================================

def check_scheduled_messages():
    while True:
        try:
            now = datetime.now()
            for m in scheduled_messages:
                if not m.get("sent"):
                    mt = datetime.fromisoformat(m["time"])
                    if now >= mt:
                        for u in list(registered_users):
                            try:
                                bot.send_message(u, f"📅 {m['message']}")
                            except:
                                pass
                        m["sent"] = True
                        sync_db()
            time.sleep(60)
        except:
            time.sleep(60)

def check_scheduled_posts():
    while True:
        try:
            now = datetime.now()
            items_copy = list(scheduled_posts)
            for item in items_copy:
                if item.get("sent"):
                    continue
                try:
                    sched_time = datetime.fromisoformat(item["scheduled_time"])
                    if now >= sched_time:
                        files = item.get("files", [])
                        channel_ids = item.get("channel_ids", [])

                        if not channel_ids or not files:
                            item["sent"] = True
                            continue

                        total_sent = 0
                        total_failed = 0

                        for ch in channel_ids:
                            ch_id = ch["id"] if isinstance(ch, dict) else ch
                            for file_item in files:
                                fid = file_item["file_id"]
                                ftype = file_item["file_type"]
                                caption = file_item.get("caption", "")
                                voice_data = file_item.get("voice_data")
                                voice_buttons = file_item.get("voice_buttons", []) # 🔥 Voice Buttons 🔥
                                custom_buttons = file_item.get("buttons", []) # 🔥 File Buttons 🔥
                                from_chat = file_item.get("from_chat_id", ch_id)
                                reply_data = file_item.get("reply_data") or {}
                                reply_to_id = None
                                try:
                                    if str(reply_data.get("target_chat_id")) == str(ch_id):
                                        reply_to_id = int(reply_data.get("target_message_id"))
                                    elif reply_data.get("target_chat_id"):
                                        logger.warning(
                                            "Reply target channel %s differs from destination %s; sending file without reply.",
                                            reply_data.get("target_chat_id"), ch_id
                                        )
                                except (TypeError, ValueError):
                                    reply_to_id = None
                                reply_kwargs = {"reply_to_message_id": reply_to_id} if reply_to_id else {}

                                # 🔥 Build Inline Keyboard for Custom Buttons 🔥
                                reply_markup = None
                                if custom_buttons:
                                    # Arrange URL buttons in two columns: 2 per row.
                                    # An odd final button naturally occupies the left column.
                                    reply_markup = InlineKeyboardMarkup(row_width=2)
                                    custom_inline_buttons = [
                                        InlineKeyboardButton(text=btn["name"], url=btn["url"])
                                        for btn in custom_buttons
                                        if btn.get("name") and btn.get("url")
                                    ]
                                    for button_index in range(0, len(custom_inline_buttons), 2):
                                        reply_markup.row(*custom_inline_buttons[button_index:button_index + 2])
                                # 🔥 END 🔥

                                # 🔥 Build Inline Keyboard for Voice Buttons 🔥
                                voice_reply_markup = None
                                if voice_buttons:
                                    voice_reply_markup = InlineKeyboardMarkup(row_width=1)
                                    for btn in voice_buttons:
                                        voice_reply_markup.add(InlineKeyboardButton(text=btn["name"], url=btn["url"]))
                                # 🔥 END 🔥

                                sent_file_msg = None
                                try:
                                    if ftype == 'forward':
                                        sent_file_msg = bot.forward_message(
                                            ch_id, from_chat, fid
                                        )
                                    elif ftype == 'document':
                                        sent_file_msg = bot.send_document(ch_id, fid, caption=caption if caption else None, parse_mode="HTML", reply_markup=reply_markup, **reply_kwargs)
                                    elif ftype == 'photo':
                                        sent_file_msg = bot.send_photo(ch_id, fid, caption=caption if caption else None, parse_mode="HTML", reply_markup=reply_markup, **reply_kwargs)
                                    elif ftype == 'video':
                                        sent_file_msg = bot.send_video(ch_id, fid, caption=caption if caption else None, parse_mode="HTML", reply_markup=reply_markup, **reply_kwargs)
                                    elif ftype == 'audio':
                                        sent_file_msg = bot.send_audio(ch_id, fid, caption=caption if caption else None, parse_mode="HTML", reply_markup=reply_markup, **reply_kwargs)
                                    elif ftype == 'animation':
                                        sent_file_msg = bot.send_animation(ch_id, fid, caption=caption if caption else None, parse_mode="HTML", reply_markup=reply_markup, **reply_kwargs)
                                    elif ftype == 'sticker':
                                        sent_file_msg = bot.send_sticker(ch_id, fid, **reply_kwargs)
                                        if caption or reply_markup:
                                            bot.send_message(ch_id, caption if caption else "🎭 Sticker", parse_mode="HTML", reply_markup=reply_markup)
                                    elif ftype == 'text':
                                        if caption:
                                            try:
                                                sent_file_msg = bot.send_message(ch_id, caption, parse_mode="HTML", disable_web_page_preview=False, reply_markup=reply_markup, **reply_kwargs)
                                            except Exception:
                                                sent_file_msg = bot.send_message(ch_id, caption, disable_web_page_preview=False, reply_markup=reply_markup, **reply_kwargs)
                                        else:
                                            sent_file_msg = bot.copy_message(ch_id, from_chat, fid, reply_markup=reply_markup, **reply_kwargs)

                                    if voice_data and voice_data.get("file_id"):
                                        time.sleep(1)
                                        v_cap = voice_data.get("caption", "")
                                        
                                        if sent_file_msg:
                                            # 🔥 Send Voice as Reply to the File 🔥
                                            bot.send_voice(
                                                ch_id, 
                                                voice_data["file_id"], 
                                                caption=v_cap if v_cap else None, 
                                                parse_mode="HTML", 
                                                reply_markup=voice_reply_markup,
                                                reply_to_message_id=sent_file_msg.message_id # Auto Reply Feature
                                            )
                                        else:
                                            # Fallback if no file msg (shouldn't happen)
                                            bot.send_voice(ch_id, voice_data["file_id"], caption=v_cap if v_cap else None, parse_mode="HTML", reply_markup=voice_reply_markup)
                                    
                                    # React to scheduler-published posts using the saved reaction bots
                                    # configured for this destination channel. This is explicit because
                                    # Telegram does not reliably send our own channel posts back as updates.
                                    if sent_file_msg:
                                        try:
                                            target_channel_id = str(sent_file_msg.chat.id)
                                            for reaction_bot_data in bot_settings.get("reaction_bots", []):
                                                if str(reaction_bot_data.get("channel_id")) == target_channel_id:
                                                    reaction_delay = random.uniform(2.0, 4.0)
                                                    threading.Thread(
                                                        target=process_single_reaction,
                                                        args=(reaction_bot_data, sent_file_msg, reaction_delay),
                                                        daemon=True
                                                    ).start()
                                        except Exception as reaction_exc:
                                            logger.error(f"Scheduled-post reaction trigger failed: {reaction_exc}")

                                    total_sent += 1
                                    time.sleep(0.5) 
                                except Exception as e:
                                    total_failed += 1
                                    logger.error(f"Post failed to {ch_id} for file {fid}: {e}")

                        item["sent"] = True
                        item["sent_at"] = datetime.now().isoformat()
                        
                        if ADMIN_ID in live_schedule_messages:
                            pending_after = [x for x in scheduled_posts if not x.get("sent")]
                            msg_id = live_schedule_messages[ADMIN_ID]
                            if not pending_after:
                                try:
                                    bot.edit_message_text("❌ No pending scheduled posts.", ADMIN_ID, msg_id)
                                except: pass
                                del live_schedule_messages[ADMIN_ID]
                            else:
                                msg_new, markup_new = generate_scheduled_text(pending_after)
                                try:
                                    bot.edit_message_text(msg_new, ADMIN_ID, msg_id, parse_mode="Markdown", reply_markup=markup_new)
                                except: pass

                        try:
                            bot.send_message(
                                ADMIN_ID,
                                f"✅ **Auto Post Process Finished!**\n\n"
                                f"📁 Total Files: `{len(files)}`\n"
                                f"📢 Sent to: `{len(channel_ids)}` channels\n"
                                f"✅ Success: `{total_sent}` | ❌ Failed: `{total_failed}`\n"
                                f"⏰ Time: {now.strftime('%Y-%m-%d %H:%M')}",
                                parse_mode="Markdown"
                            )
                        except:
                            pass
                        sync_db()
                except Exception as e:
                    logger.error(f"Post check error: {e}")
            time.sleep(30)
        except:
            time.sleep(30)

def check_scheduled_deletions():
    while True:
        try:
            now = datetime.now()
            items_copy = list(scheduled_deletions)
            changed = False
            for item in items_copy:
                if item.get("deleted"):
                    continue
                try:
                    del_time = datetime.fromisoformat(item["delete_at"])
                    if now >= del_time:
                        ch_id = item["channel_id"]
                        msg_id = item["message_id"]
                        
                        try:
                            bot.delete_message(ch_id, msg_id)
                            item["deleted"] = True
                            item["status"] = "success"
                            logger.info(f"Deleted message {msg_id} from {ch_id}")
                        except Exception as e:
                            logger.error(f"Failed to delete msg {msg_id} from {ch_id}: {e}")
                            item["deleted"] = True
                            item["status"] = "failed"
                            item["error"] = str(e)
                        
                        changed = True
                        
                        try:
                            bot.send_message(
                                ADMIN_ID,
                                f"🗑️ **Message Deletion {'Success' if item['status'] == 'success' else 'Failed'}!**\n\n"
                                f"📢 Channel: `{item.get('channel_title')}`\n"
                                f"🆔 Msg ID: `{msg_id}`\n"
                                f"⏰ Time: {now.strftime('%Y-%m-%d %H:%M')}\n\n"
                                f"⚠️ **Note:** If failed, it's likely because the message is older than 48 hours (Telegram Limit) or bot lacks permission.",
                                parse_mode="Markdown"
                            )
                        except:
                            pass

                except Exception as e:
                    logger.error(f"Deletion check error: {e}")
            
            if changed:
                sync_db()
                if ADMIN_ID in live_deletion_messages:
                    pending_after = [x for x in scheduled_deletions if not x.get("deleted")]
                    msg_id = live_deletion_messages[ADMIN_ID]
                    if not pending_after:
                        try:
                            bot.edit_message_text("❌ No pending scheduled deletions.", ADMIN_ID, msg_id)
                        except: pass
                        del live_deletion_messages[ADMIN_ID]
                    else:
                        msg_new, markup_new = generate_deletion_text(pending_after)
                        try:
                            bot.edit_message_text(msg_new, ADMIN_ID, msg_id, parse_mode="Markdown", reply_markup=markup_new)
                        except: pass

            time.sleep(10) 
        except:
            time.sleep(10)

def check_giveaway_loop():
    while True:
        try:
            if giveaway.get("active") and giveaway.get("end_time"):
                et = datetime.fromisoformat(giveaway["end_time"])
                if datetime.now() >= et:
                    p = giveaway.get("participants", [])
                    if p:
                        w = random.choice(p)
                        pr = giveaway.get("prize", "Prize")
                        if "winners" not in giveaway:
                            giveaway["winners"] = []
                        giveaway["winners"].append({"user_id": w, "prize": pr})
                        bot.send_message(ADMIN_ID, f"🎉 Winner: `{w}`")
                    giveaway["active"] = False
                    giveaway["participants"] = []
                    giveaway["end_time"] = None
                    sync_db()
            time.sleep(60)
        except:
            time.sleep(60)

# =====================================================
#           🚀 MAIN
# =====================================================

if __name__ == "__main__":
    try:
        from flask import Flask
        af = Flask(__name__)
        try:
            import admin_api
            admin_api.register(af, globals())
        except Exception as _e:
            logger.error(f"Admin panel failed to mount: {_e}")

        @af.route('/')
        def home():
            from flask import redirect
            return redirect("/admin.html", code=302)

        @af.route('/health')
        def health():
            return "OK"

        _polling_started = threading.Event()
        _background_started = threading.Event()

        def run_bot():
            if _polling_started.is_set():
                logger.warning("Polling runner already started in this process; skipping duplicate start.")
                return
            _polling_started.set()
            print_banner()
            
            def _restore():
                time.sleep(5)
                try:
                    if USERBOT_AVAILABLE:
                        run_async(ub_module.restore_sessions())
                        logger.info("✅ Userbot sessions restored")
                except Exception as e:
                    logger.error(f"Restore err: {e}")
            threading.Thread(target=_restore, daemon=True).start()

            if not _background_started.is_set():
                _background_started.set()
                threading.Thread(target=auto_backup, daemon=True).start()
                threading.Thread(target=check_scheduled_messages, daemon=True).start()
                threading.Thread(target=check_scheduled_posts, daemon=True).start()
                threading.Thread(target=check_scheduled_deletions, daemon=True).start()
                threading.Thread(target=check_giveaway_loop, daemon=True).start()
                threading.Thread(target=live_updater, daemon=True).start()
            logger.info(f"✅ {BOT_NAME} Bot Running...")
            while True:
                try:
                    bot.infinity_polling(timeout=30, long_polling_timeout=20)
                except Exception as e:
                    error_text = str(e)
                    if "409" in error_text or "Conflict" in error_text or "terminated by other getUpdates" in error_text:
                        logger.error("Telegram polling conflict (409). Another process is using this bot token. Stop the other instance before this service can receive updates.")
                        time.sleep(30)
                    else:
                        logger.exception("Telegram polling stopped; retrying in 5 seconds: %s", e)
                        time.sleep(5)
                    try:
                        sync_db()
                    except Exception:
                        logger.exception("Database sync failed during recovery")

        threading.Thread(target=run_bot, daemon=True).start()
        port = int(os.environ.get("PORT", 5000))
        af.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

    except ImportError:
        print_banner()
        
        def _restore2():
            time.sleep(5)
            try:
                if USERBOT_AVAILABLE:
                    run_async(ub_module.restore_sessions())
            except Exception:
                pass
        threading.Thread(target=_restore2, daemon=True).start()

        threading.Thread(target=auto_backup, daemon=True).start()
        threading.Thread(target=check_scheduled_messages, daemon=True).start()
        threading.Thread(target=check_scheduled_posts, daemon=True).start()
        threading.Thread(target=check_scheduled_deletions, daemon=True).start()
        threading.Thread(target=check_giveaway_loop, daemon=True).start()
        threading.Thread(target=live_updater, daemon=True).start()
        logger.info(f"✅ {BOT_NAME} Bot Running...")
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            logger.error(f"Crash: {e}")
            sync_db()
