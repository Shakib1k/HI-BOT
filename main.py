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
import inspect
import queue
import shutil
from types import SimpleNamespace
import features
import vault
import atexit
import signal
from concurrent.futures import ThreadPoolExecutor

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

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=16)

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

_member_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="member-check")


def _check_one_channel(ch, user_id):
    """Returns the channel if the user has NOT joined it, otherwise None."""
    try:
        chat_id = ch.get("chat_id", "").strip()
        join_url = ch.get("join_url", "").strip()
        identifier = None
        if chat_id:
            if chat_id.startswith("@") or chat_id.startswith("-"):
                identifier = chat_id
        elif ch.get("type") == "public" and join_url:
            identifier = extract_public_username(join_url)
        if not identifier:
            return None
        member = bot.get_chat_member(identifier, user_id)
        if member.status in ['left', 'kicked']:
            return ch
    except Exception as e:
        logger.error(f"Membership check error ({ch.get('name')}): {e}")
    return None


def get_unjoined_channels(user_id):
    """All channels are checked at the same time, so 10 channels cost one round-trip, not ten."""
    if not bot_settings.get("force_join", True):
        return []
    chans = get_all_channels()
    if not chans:
        return []
    results = list(_member_pool.map(lambda c: _check_one_channel(c, user_id), chans))
    return [c for c in results if c]

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
            "user_limit_enabled": True,
            "delivery_loading": True,
            "reaction_gap": 1.0
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

_last_db_backup = 0
_db_dirty = threading.Event()
_flush_lock = threading.Lock()
DB_FLUSH_DELAY = 2.0


def save_db(data):
    """Compact + atomic write (no half-written file if the server restarts mid-save)."""
    global _last_db_backup
    text = None
    for _ in range(4):                    # another thread may resize a dict while it is being dumped
        try:
            text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            break
        except RuntimeError:
            time.sleep(0.05)
    if text is None:
        logger.error("DB save skipped: data kept changing while saving")
        return False
    try:
        tmp = f"{DB_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, DB_FILE)
        if time.time() - _last_db_backup > 120:
            shutil.copyfile(DB_FILE, f"{DB_FILE}.backup")
            _last_db_backup = time.time()
        try:
            vault.touch()
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"DB save error: {e}")
        return False


def _db_writer():
    """sync_db() only marks the data dirty; this thread writes it at most every couple of seconds.
    Writing the whole file on every user click made the bot and the panel lag."""
    while True:
        _db_dirty.wait()
        time.sleep(DB_FLUSH_DELAY)
        _db_dirty.clear()
        with _flush_lock:
            try:
                save_db(db)
            except Exception:
                logger.exception("db writer")


threading.Thread(target=_db_writer, daemon=True, name="db-writer").start()


def flush_db():
    """Write right now (backup export, shutdown, important state changes)."""
    with _flush_lock:
        _db_dirty.clear()
        return save_db(db)


def _persist_now():
    flush_db()
    try:
        vault.touch(urgent=True)
    except Exception:
        pass


# Hosts with an ephemeral disk (Render free) lose bot_database.json on every restart. The vault keeps a
# compressed copy pinned in the owner's chat and brings it back here before anything is loaded.
vault.init(bot, ADMIN_ID, DB_FILE, lambda: bool(globals().get("bot_settings", {}).get("vault_enabled", True)))
vault.restore_if_missing()
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

def sync_db(force=False):
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
    if force:
        flush_db()
    else:
        _db_dirty.set()


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

def _bar(pct, n=10):
    full = int(round(pct * n / 100))
    return "▰" * full + "▱" * (n - full)


def _loading_start(chat_id):
    if not bot_settings.get("delivery_loading", True):
        return None
    try:
        return bot.send_message(chat_id, f"⏳ <b>{html.escape(BOT_NAME)} · Secure Delivery</b>\n\n{_bar(10)} 10%\n🔎 Verifying your access…", parse_mode="HTML")
    except Exception:
        return None


def _loading_step(msg, chat_id, pct, line, pause=0.4):
    if not msg:
        return
    try:
        bot.edit_message_text(f"⏳ <b>{html.escape(BOT_NAME)} · Secure Delivery</b>\n\n{_bar(pct)} {pct}%\n{line}", chat_id, msg.message_id, parse_mode="HTML")
    except Exception:
        pass
    time.sleep(pause)


def _loading_end(msg, chat_id):
    if msg:
        try:
            bot.delete_message(chat_id, msg.message_id)
        except Exception:
            pass


# =====================================================
#           💬 USER-FACING CARDS (Help / Profile / Stats)
# =====================================================
_RANKS = [(0, "Member"), (10, "🥉 Bronze"), (25, "🥈 Silver"), (50, "🥇 Gold"), (100, "💎 Diamond")]


def _rank_progress(points):
    """(current rank, next rank or None, percent towards next)."""
    cur = _RANKS[0]
    nxt = None
    for i, (need, name) in enumerate(_RANKS):
        if points >= need:
            cur = (need, name)
            nxt = _RANKS[i + 1] if i + 1 < len(_RANKS) else None
    if not nxt:
        return cur[1], None, 100
    span = nxt[0] - cur[0]
    return cur[1], nxt[1], int((points - cur[0]) * 100 / span)


def _send_help(chat_id):
    try:
        bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass
    text = (
        f"✨ <b>{html.escape(BOT_NAME)} · Help Center</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📥 <b>Get a file</b>\n"
        "Tap a download link from our channel → join the required channels → press <b>Verify &amp; Continue</b>. "
        "Your file arrives automatically.\n\n"
        "🎙️ <b>Voice</b> – turn any text into a voice message\n"
        "📊 <b>Stats</b> – your activity\n"
        "🎁 <b>Daily Reward</b> – free points every day\n"
        "🏆 <b>Leaderboard</b> – top members\n"
        "👤 <b>Profile</b> – your rank and invite link\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💡 Link not working? Make sure you joined <u>every</u> listed channel, then tap the link again."
    )
    bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=get_main_keyboard())


def _send_profile(chat_id, u):
    try:
        bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass
    act = user_activity.get(u.id, {})
    points = int(act.get("points", 0) or 0)
    rank, nxt, pct = _rank_progress(points)
    bar = _bar(pct)
    refs = len((db.get("referrals", {}) or {}).get(str(u.id), []))
    try:
        link = f"https://t.me/{bot.get_me().username}?start=ref_{u.id}"
    except Exception:
        link = ""
    lines = [
        "👤 <b>Your Profile</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📛 <b>{html.escape(u.first_name or 'User')}</b>",
        f"🆔 <code>{u.id}</code>",
        f"🏅 Rank: <b>{rank}</b>",
        f"⭐ Points: <b>{points}</b>",
        (f"{bar} {pct}% → {nxt}" if nxt else f"{bar} Max rank reached 🎉"),
        f"👥 Friends invited: <b>{refs}</b>",
    ]
    if link:
        lines += ["━━━━━━━━━━━━━━━━━━", "🔗 <b>Your invite link</b>", f"<code>{link}</code>"]
    bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML", reply_markup=get_main_keyboard())


def _send_stats(chat_id, user_id):
    act = user_activity.get(user_id) or {}
    points = int(act.get("points", 0) or 0)
    rank, nxt, pct = _rank_progress(points)
    total = int(act.get("total_actions", 0) or 0)
    bot.send_message(chat_id,
        "📊 <b>Your Stats</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🔄 Actions: <b>{total}</b>\n⭐ Points: <b>{points}</b>\n🏅 Rank: <b>{rank}</b>\n"
        + (f"{_bar(pct)} {pct}% → {nxt}" if nxt else "👑 You reached the top rank!"),
        parse_mode="HTML", reply_markup=get_main_keyboard())



def send_target_file(chat_id, file_key, loader=None):
    if file_key not in file_database:
        _loading_end(loader, chat_id)
        bot.send_message(chat_id, "❌ <b>Link expired</b>\nThis file was removed or the link is no longer valid.", parse_mode="HTML", reply_markup=get_main_keyboard())
        return False
    data = file_database[file_key]
    loader = loader or _loading_start(chat_id)
    if data.get("is_text_key"):
        _loading_step(loader, chat_id, 60, "🔐 Generating your key…")
        _loading_step(loader, chat_id, 95, "✅ Almost done…", 0.2)
        _loading_end(loader, chat_id)
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
    fid = data["file_id"]
    ftype = data["file_type"]
    fname = data.get("file_name", "file")
    safe_fname = html.escape(fname)
    _loading_step(loader, chat_id, 45, "📦 Preparing your file…")
    _loading_step(loader, chat_id, 80, "🚀 Sending to you…", 0.25)
    action = {"photo": "upload_photo", "video": "upload_video", "audio": "upload_audio", "animation": "upload_video"}.get(ftype, "upload_document")
    try:
        bot.send_chat_action(chat_id, action)
    except Exception:
        pass
    caption = f"✅ <b>Verified</b>\n📁 <code>{safe_fname}</code>"
    try:
        if ftype == 'document':
            bot.send_document(chat_id, fid, caption=caption, parse_mode="HTML", reply_markup=get_main_keyboard())
        elif ftype == 'photo':
            bot.send_photo(chat_id, fid, caption=caption, parse_mode="HTML", reply_markup=get_main_keyboard())
        elif ftype == 'video':
            bot.send_video(chat_id, fid, caption=caption, parse_mode="HTML", reply_markup=get_main_keyboard())
        elif ftype == 'audio':
            bot.send_audio(chat_id, fid, caption=caption, parse_mode="HTML", reply_markup=get_main_keyboard())
        elif ftype == 'animation':
            bot.send_animation(chat_id, fid, caption=caption, parse_mode="HTML", reply_markup=get_main_keyboard())
        elif ftype == 'sticker':
            bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=get_main_keyboard())
            bot.send_sticker(chat_id, fid)
        else:
            bot.send_document(chat_id, fid, caption=caption, parse_mode="HTML", reply_markup=get_main_keyboard())
        _loading_end(loader, chat_id)
        log_user_activity(chat_id, "file_accessed")
        data["downloads"] = data.get("downloads", 0) + 1
        sync_db()
        return True
    except Exception as e:
        logger.error(f"File send error: {e}")
        _loading_end(loader, chat_id)
        bot.send_message(chat_id, "❌ <b>Could not send the file.</b>\nPlease tap the link again in a moment.", parse_mode="HTML")
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


        loader = _loading_start(chat_id) if file_key else None      # progress appears instantly
        unjoined = get_unjoined_channels(user_id)
        if not unjoined:
            if file_key:
                send_target_file(chat_id, file_key, loader)
            else:
                welcome_text = bot_settings.get("welcome_message", f"✨ Welcome to {BOT_NAME} Premium Bot! ✨\n\n👋 Hello {{name}}!")
                formatted_welcome = welcome_text.replace("{name}", first_name).replace("{id}", str(user_id))
                
                try:
                    bot.send_message(chat_id, formatted_welcome, reply_markup=get_main_keyboard(), parse_mode="HTML")
                except:
                    bot.send_message(chat_id, formatted_welcome, reply_markup=get_main_keyboard())
            return

        _loading_end(loader, chat_id)
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
    """Apply a configured reaction using the selected reaction-bot token. Retries once on flood-wait."""
    if delay:
        time.sleep(delay)
    token = (bot_data.get("token") or "").strip()
    emoji = bot_data.get("emoji")
    bot_id = bot_data.get("bot_id", "Unknown")
    if not token or not emoji:
        logger.warning("Skipping reaction bot with missing token/emoji: %s", bot_id)
        return False
    payload = {
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "reaction": [{"type": "emoji", "emoji": emoji}],
        "is_big": False
    }
    for attempt in range(2):
        try:
            res = requests.post(f"https://api.telegram.org/bot{token}/setMessageReaction", json=payload, timeout=12)
            data = {}
            try:
                data = res.json()
            except Exception:
                pass
            if res.ok and data.get("ok", False):
                return True
            retry = (data.get("parameters") or {}).get("retry_after")
            if retry and attempt == 0:
                time.sleep(min(int(retry) + 1, 15))
                continue
            desc = data.get("description", res.text[:300] if res.text else "Unknown Telegram API error")
            logger.error("Reaction failed (%s) on chat %s message %s: %s", bot_id, message.chat.id, message.message_id, desc)
            return False
        except Exception as exc:
            logger.exception("Reaction request error (%s): %s", bot_id, exc)
            return False
    return False


# One worker = one reaction at a time, with a gap between bots. Many bots reacting at the
# same instant overloaded small servers and made most of them fail, so they now go one by one.
_reaction_queue = queue.Queue()


def _reaction_worker():
    while True:
        bot_data, message = _reaction_queue.get()
        try:
            process_single_reaction(bot_data, message, 0)
        except Exception:
            logger.exception("reaction worker")
        try:
            gap = float(bot_settings.get("reaction_gap", 1.0) or 1.0)
        except (TypeError, ValueError):
            gap = 1.0
        time.sleep(max(0.3, min(gap, 10.0)))


threading.Thread(target=_reaction_worker, daemon=True, name="reaction-queue").start()


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
            _reaction_queue.put((reaction_bot, message))
    except Exception as exc:
        logger.exception("Reaction trigger error: %s", exc)


@bot.channel_post_handler(content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation', 'video_note', 'contact', 'location', 'venue', 'poll', 'dice'])
def auto_copy_source_messages(message):
    # Silent target purge runs first (separate feature from the managed-admin limits).
    try:
        if features.purge_check(message.chat.id, message):
            return
    except Exception:
        logger.exception("purge check (Bot API) failed")
    # Enforce per-admin upload limits first (identifies the admin via author signature).
    # A blocked post is deleted, so there is nothing left to react to / copy.
    try:
        if _enforce_direct_channel_post(message.chat.id, None, message):
            return
    except Exception:
        logger.exception("Managed admin enforcement (Bot API) failed")
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
    try:
        if features.purge_check(message.chat.id, message) or features.group_guard(message):
            return
    except Exception:
        logger.exception("group guard / purge failed")
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



# ---- remember posts the BOT itself sends, so they are never charged to a human admin ----
_BOT_SENT = {}
_channel_events = {}
_admins_cache = {}


def _chan_core(x):
    x = str(x).strip().lstrip("-")
    return x[3:] if x.startswith("100") and len(x) > 10 else x


def _remember_bot_post(chat_id, result):
    try:
        items = result if isinstance(result, (list, tuple)) else [result]
        for m in items:
            mid = getattr(m, "message_id", None)
            cid = getattr(getattr(m, "chat", None), "id", None) or chat_id
            if mid and cid is not None:
                _BOT_SENT[_chan_core(cid) + ":" + str(mid)] = time.time()
        if len(_BOT_SENT) > 4000:
            for k, _ in sorted(_BOT_SENT.items(), key=lambda i: i[1])[:2000]:
                _BOT_SENT.pop(k, None)
    except Exception:
        pass


def _install_bot_post_tracker():
    for name in ("send_message", "send_photo", "send_video", "send_document", "send_audio", "send_voice",
                 "send_animation", "send_video_note", "send_media_group", "copy_message", "send_poll", "send_sticker"):
        orig = getattr(bot, name, None)
        if not orig:
            continue

        def make(orig_fn):
            def wrapper(*a, **k):
                res = orig_fn(*a, **k)
                _remember_bot_post(a[0] if a else k.get("chat_id"), res)
                return res
            return wrapper
        setattr(bot, name, make(orig))


_install_bot_post_tracker()


def _log_channel_event(channel_id, message_id, source, result):
    lst = _channel_events.setdefault(str(channel_id), [])
    lst.append({"t": datetime.now().strftime("%H:%M:%S"), "id": message_id, "src": source, "result": result})
    del lst[:-15]
    logger.info("[ADMIN-LIMIT] ch=%s msg=%s via=%s -> %s", channel_id, message_id, source, result)


def _get_admins_cached(channel_key):
    t, data = _admins_cache.get(channel_key, (0, None))
    if time.time() - t < 60 and data is not None:
        return data
    try:
        data = bot.get_chat_administrators(channel_key)
        _admins_cache[channel_key] = (time.time(), data)
        return data
    except Exception as exc:
        logger.warning("get_chat_administrators failed for %s: %s", channel_key, exc)
        return None


def _infer_single_admin(channel_key, bucket, observer_id, is_out, channel_id, message_id):
    """No signature and no sender ID: use elimination. Returns (uid|None, reason)."""
    if is_out:
        return (str(observer_id) if str(observer_id) in bucket else None), "post by the observer's own account (owner) - ignored"
    if _chan_core(channel_id) + ":" + str(message_id) in _BOT_SENT:
        return None, "post sent by this bot itself - ignored"
    admins = _get_admins_cached(channel_key)
    if admins is None:
        return None, "could not read the channel admin list (bot must be admin)"
    creator, humans = None, []
    for a in admins:
        if getattr(a.user, "is_bot", False):
            continue
        humans.append(a.user.id)
        if a.status == "creator":
            creator = a.user.id
    if creator is None or int(observer_id) != int(creator):
        return None, "no admin name on post. Turn ON Sign Messages, or log the Telethon userbot in with the channel OWNER account"
    managed = [str(u) for u in humans if str(u) in bucket]
    others = [u for u in humans if str(u) not in bucket and u != creator]
    if others:
        return None, "channel has other unmanaged admins - turn ON Sign Messages to tell admins apart"
    if len(managed) != 1:
        return None, "several managed admins and no admin name - turn ON Sign Messages"
    return managed[0], "inferred: only one managed admin exists and the owner did not post"

_ENFORCE_LOCK = threading.RLock()
_QUOTA_KEYS = ("files", "photos", "videos", "voice", "text")
_chat_id_cache = {}
_names_refresh_at = {}
_notice_at = {}
_blocked_posts = {}


def _policy_usage(policy):
    usage = policy.setdefault("usage", {})
    counts = usage.setdefault("counts", {k: 0 for k in _QUOTA_KEYS})
    for key in _QUOTA_KEYS:
        counts.setdefault(key, 0)
    usage.setdefault("warnings", 0)
    return usage, counts


def _content_quota_keys(message):
    """Return exactly one usage bucket for a post (Bot API message or Telethon message)."""
    # ---- Bot API (pyTelegramBotAPI) messages carry content_type ----
    ctype = getattr(message, "content_type", None)
    if ctype:
        if ctype == "text":
            return ["text"]
        if ctype == "photo":
            return ["photos"]
        if ctype in ("video", "animation", "video_note"):
            return ["videos"]
        if ctype == "voice":
            return ["voice"]
        return ["files"]  # document, audio, sticker, poll, ...

    # ---- Telethon messages ----
    media = getattr(message, "media", None)
    # A link preview is still a plain text message.
    if media is None or media.__class__.__name__ in ("MessageMediaWebPage", "MessageMediaEmpty"):
        return ["text"]
    if getattr(message, "voice", None):
        return ["voice"]
    if getattr(message, "video", None) or getattr(message, "video_note", None) or getattr(message, "gif", None):
        return ["videos"]
    if getattr(message, "photo", None):
        return ["photos"]
    return ["files"]


def _post_has_link(message, body):
    if re.search(r"https?://|t\.me/|telegram\.me/|www\.", str(body or ""), re.I):
        return True
    ents = list(getattr(message, "entities", None) or []) + list(getattr(message, "caption_entities", None) or [])
    for e in ents:
        kind = str(getattr(e, "type", "") or e.__class__.__name__).lower()
        if "url" in kind or "text_link" in kind:
            return True
    return False


def _cooldown_seconds(policy, warning=False):
    """Suspension length. 'warning' suspensions can use their own time (warning_cooldown_hours)."""
    raw = policy.get("warning_cooldown_hours") if warning else None
    if raw in (None, ""):
        raw = policy.get("cooldown_hours", 0)
    try:
        hours = float(raw or 0)
    except (TypeError, ValueError):
        hours = 0
    return max(30, min(hours * 3600, 8760 * 3600)) if hours > 0 else 3600


def _warning_limit(policy):
    try:
        return max(1, min(int(policy.get("warning_limit", 3) or 3), 20))
    except (TypeError, ValueError):
        return 3


def _fmt_duration(seconds):
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d} day{'s' if d != 1 else ''}")
    if h: parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m: parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if not parts: parts.append(f"{sec} seconds")
    return " ".join(parts[:2])


# ---------------------------------------------------------------
#  Who posted this? (the core of per-admin counting)
#  Telegram never gives a bot the human author of a broadcast-channel post,
#  so we identify the admin by (1) the real sender ID if Telegram exposes it,
#  otherwise (2) the author signature that "Sign messages" adds to each post.
# ---------------------------------------------------------------
def _norm_name(value):
    return re.sub(r"\s+", " ", str(value or "").replace("@", " ")).strip().casefold()


def _same_channel(a, b):
    a, b = str(a).strip(), str(b).strip()
    if a == b:
        return True

    def core(x):
        x = x.lstrip("-")
        return x[3:] if x.startswith("100") and len(x) > 10 else x
    return a.lstrip("-").isdigit() and b.lstrip("-").isdigit() and core(a) == core(b)


def _find_channel_bucket(channel_id):
    managed = bot_settings.get("channel_managed_admins", {}) or {}
    cid = str(channel_id)
    if cid in managed:
        return cid, managed[cid]
    for key, bucket in managed.items():
        if _same_channel(key, cid):
            return key, bucket
        if not str(key).lstrip("-").isdigit():  # channel saved as @username
            resolved = _chat_id_cache.get(key)
            if not resolved:
                try:
                    resolved = str(bot.get_chat(key).id)
                    _chat_id_cache[key] = resolved
                except Exception:
                    resolved = ""
            if resolved and _same_channel(resolved, cid):
                return key, bucket
    return None, None


def _store_admin_names(policy, user, custom_title=None):
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    names = []
    if first and last:
        names.append(first + " " + last)
    if getattr(user, "username", None):
        names.append(user.username)
    if custom_title:
        names.append(custom_title)
    if first and not last:
        names.append(first)
    policy["names"] = names
    policy["name_aliases"] = [first] if first and last else []


def _refresh_admin_names(channel_key, bucket):
    now = time.time()
    if now - _names_refresh_at.get(channel_key, 0) < 60:
        return
    _names_refresh_at[channel_key] = now
    try:
        for a in bot.get_chat_administrators(channel_key):
            pol = bucket.get(str(a.user.id))
            if pol is not None:
                _store_admin_names(pol, a.user, getattr(a, "custom_title", None))
    except Exception as exc:
        logger.warning("Could not refresh admin names for %s: %s", channel_key, exc)


def _attribute_channel_post(channel_key, bucket, sender_id, signature):
    """Return the managed admin's user_id (str) that made this post, or None."""
    try:
        if sender_id and int(sender_id) > 0 and str(int(sender_id)) in bucket:
            return str(int(sender_id))
    except (TypeError, ValueError):
        pass
    sig = _norm_name(signature)
    if not sig:
        return None
    for attempt in (0, 1):
        full = [u for u, p in bucket.items() if sig in {_norm_name(n) for n in (p.get("names") or [])}]
        if len(full) == 1:
            return full[0]
        if len(full) > 1:
            logger.warning("Ambiguous author signature %r in %s; two managed admins share it.", signature, channel_key)
            return None
        alias = [u for u, p in bucket.items() if sig in {_norm_name(n) for n in (p.get("name_aliases") or [])}]
        if len(alias) == 1:
            return alias[0]
        if len(alias) > 1:
            return None
        if attempt == 0:
            _refresh_admin_names(channel_key, bucket)
    return None


def _notify_owner_once(key, text, every=6 * 3600):
    now = time.time()
    if now - _notice_at.get(key, 0) < every:
        return
    _notice_at[key] = now
    try:
        bot.send_message(ADMIN_ID, text)
    except Exception:
        pass


def _record_channel_diag(channel_key, signed, attributed):
    d = bot_settings.setdefault("managed_admin_diag", {}).setdefault(str(channel_key), {})
    d["last_post_at"] = datetime.now().isoformat()
    d["last_post_signed"] = bool(signed)
    d["last_post_attributed"] = bool(attributed)
    if attributed:
        d["last_attributed_at"] = d["last_post_at"]


# ---------------------------------------------------------------
#  Suspend / restore
# ---------------------------------------------------------------
_PROMOTE_PARAMS = None
_CORE_RIGHTS = ("can_post_messages", "can_edit_messages", "can_delete_messages", "can_invite_users", "can_change_info")
_OPTIONAL_RIGHTS = {"can_post_stories", "can_edit_stories", "can_delete_stories", "can_manage_direct_messages",
                    "can_send_welcome_messages", "can_manage_video_chats", "can_manage_topics", "is_anonymous"}
_restore_status = {"last_run": 0.0}


def _managed_log(channel_key, uid, event, detail=""):
    """Persistent activity log shown in the panel (last 300 entries)."""
    try:
        lg = bot_settings.setdefault("managed_admin_log", [])
        lg.append({"t": datetime.now().isoformat(timespec="seconds"), "ch": str(channel_key), "uid": str(uid), "ev": event, "d": str(detail)[:160]})
        del lg[:-300]
    except Exception:
        pass


def _promote_safe(channel_id, user_id, rights):
    """promote_chat_member that ignores keyword arguments this telebot version does not know."""
    global _PROMOTE_PARAMS
    if _PROMOTE_PARAMS is None:
        try:
            _PROMOTE_PARAMS = set(inspect.signature(bot.promote_chat_member).parameters)
        except Exception:
            _PROMOTE_PARAMS = set()
    kw = {k: bool(v) for k, v in rights.items() if (not _PROMOTE_PARAMS) or k in _PROMOTE_PARAMS}
    bot.promote_chat_member(channel_id, int(user_id), **kw)


def _revoke_rights(channel_key, user_id, policy):
    rights = policy.get("permissions") or {}
    last_exc = None
    for attempt in range(3):
        try:
            _promote_safe(channel_key, user_id, {k: False for k in rights.keys()})
            return True, ""
        except Exception as exc:
            last_exc = exc
            time.sleep(1)
    return False, str(last_exc)


def _restore_rights(channel_id, user_id, rights):
    """Give the saved rights back. Falls back to fewer rights if Telegram rejects the full set,
    and verifies that the user really is an admin afterwards. Returns (ok, tier, error)."""
    tiers = [("full", dict(rights)),
             ("reduced", {k: v for k, v in rights.items() if k not in _OPTIONAL_RIGHTS}),
             ("core", {k: bool(v) for k, v in rights.items() if k in _CORE_RIGHTS})]
    last = ""
    for name, r in tiers:
        if not any(r.values()):
            continue
        try:
            _promote_safe(channel_id, user_id, r)
        except Exception as exc:
            last = str(exc)
            continue
        try:
            m = bot.get_chat_member(channel_id, int(user_id))
            if m.status in ("administrator", "creator"):
                return True, name, ""
            last = f"Telegram accepted the request but the user's status is '{m.status}'"
        except Exception:
            return True, name, ""
    return False, "", last or "unknown error"


def _restore_managed_admin(channel_id, user_id, policy, force=False):
    with _ENFORCE_LOCK:
        try:
            until = float(policy.get("suspended_until", 0) or 0)
            if not until:
                return False
            if not force and time.time() < until:
                return False
            if not force and float(policy.get("restore_retry_at", 0) or 0) > time.time():
                return False
            ok_, tier, error = _restore_rights(channel_id, user_id, dict(policy.get("permissions") or {}))
            if not ok_:
                policy["restore_retry_at"] = time.time() + 120
                policy["last_restore_error"] = error[:200]
                _managed_log(channel_id, user_id, "restore_failed", error)
                sync_db()
                if policy.get("restore_error_notified") != error[:60]:
                    policy["restore_error_notified"] = error[:60]
                    try:
                        bot.send_message(ADMIN_ID, f"⚠️ The cooldown finished but I could not give the admin rights back.\nChannel ID: {channel_id}\nUser ID: {user_id}\nTelegram said: {error}\n\nI retry every 2 minutes. Check that the bot has 'Add new admins' and that the user is still in the channel.")
                    except Exception:
                        pass
                return False
            for k in ("suspended_until", "suspension_reason", "suspended_at", "restore_retry_at", "restore_error_notified", "rights_revoked", "last_restore_error"):
                policy.pop(k, None)
            usage, counts = _policy_usage(policy)
            usage["warnings"] = 0
            for key in counts:
                counts[key] = 0
            policy["last_restored_at"] = datetime.now().isoformat()
            _managed_log(channel_id, user_id, "restored", ("manual" if force else "cooldown finished") + ("" if tier == "full" else f" ({tier} rights only)"))
            sync_db()
            _persist_now()
            try:
                bot.send_message(int(user_id), "✅ <b>Admin access restored</b>\nYour permissions are back and your counters are reset. You can post again.", parse_mode="HTML")
            except Exception:
                pass
            try:
                note = "" if tier == "full" else f"\n⚠️ Telegram only accepted {tier} rights."
                bot.send_message(ADMIN_ID, f"✅ Managed admin restored\nChannel ID: {channel_id}\nUser ID: {user_id}{note}")
            except Exception:
                pass
            return True
        except Exception as exc:
            logger.exception("Could not restore managed admin %s in %s: %s", user_id, channel_id, exc)
            policy["restore_retry_at"] = time.time() + 120
            return False


def _suspend_managed_admin(channel_id, user_id, policy, reason, warning=False):
    """Revoke every permission granted by this manager, then restore after the chosen time."""
    with _ENFORCE_LOCK:
        if float(policy.get("suspended_until", 0) or 0) > time.time():
            return
        seconds = _cooldown_seconds(policy, warning)
        revoked, error = _revoke_rights(channel_id, user_id, policy)
        # The suspension is recorded even if Telegram refused the demotion: every post made
        # during the cooldown is still deleted by the enforcement path.
        policy["suspended_until"] = time.time() + seconds
        policy["suspension_reason"] = reason
        policy["suspended_at"] = datetime.now().isoformat()
        policy["rights_revoked"] = bool(revoked)
        policy.pop("restore_retry_at", None)
        policy.pop("restore_error_notified", None)
        policy.pop("last_restore_error", None)
        _managed_log(channel_id, user_id, "suspended", f"{reason} · {_fmt_duration(seconds)}")
        sync_db()
        _persist_now()
        wait_text = _fmt_duration(seconds)
        try:
            bot.send_message(int(user_id), f"⛔ <b>Admin access paused</b>\nReason: {html.escape(reason)}\nYour permissions come back automatically in <b>{wait_text}</b>.", parse_mode="HTML")
        except Exception:
            pass
        try:
            extra = "" if revoked else f"\n⚠️ Telegram refused to remove the rights ({error}). Posts by this admin will be deleted until the time ends."
            bot.send_message(ADMIN_ID, f"⛔ Managed admin suspended\nChannel ID: {channel_id}\nUser ID: {user_id}\nReason: {reason}\nTime: {wait_text}{extra}")
        except Exception:
            pass


def _limit_of(policy, kind):
    try:
        return int((policy.get("limits") or {}).get(kind, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _all_enabled_quotas_exhausted(policy):
    """True when every type that is allowed (limit > 0) is used up.
    limit 0 = blocked type, limit -1 = unlimited (an unlimited type can never be 'used up')."""
    usage, counts = _policy_usage(policy)
    limits = [(k, _limit_of(policy, k)) for k in _QUOTA_KEYS]
    if any(v < 0 for _, v in limits):
        return False
    enabled = [(k, v) for k, v in limits if v > 0]
    return bool(enabled) and all(int(counts.get(k, 0) or 0) >= v for k, v in enabled)


def _delete_channel_post(channel_id, message_id):
    try:
        bot.delete_message(int(channel_id), int(message_id))
        return True
    except Exception as exc:
        logger.warning("Could not delete channel post %s/%s: %s", channel_id, message_id, exc)
        _notify_owner_once(("nodelete", str(channel_id)),
                           f"⚠️ I could not delete a post in channel {channel_id}.\nGive the bot the 'Delete messages' admin right there.\nTelegram said: {exc}", every=1800)
        return False


def _enforce_direct_channel_post(channel_id, user_id, message, observer_id=None, is_out=False):
    """Count / block a post made directly in a channel. Returns True if the post was blocked."""
    try:
        if features.purge_check(channel_id, message, user_id):
            return True
        if observer_id is not None and not (getattr(message, "post_author", None) or getattr(message, "author_signature", None)):
            _, b = _find_channel_bucket(channel_id)
            # Give the Bot API a moment to register its own sends before deciding who posted.
            if b:
                time.sleep(2.5)
        with _ENFORCE_LOCK:
            return _enforce_direct_channel_post_locked(channel_id, user_id, message, observer_id, is_out)
    except Exception:
        logger.exception("Direct channel post enforcement failed")
        return False


def _enforce_direct_channel_post_locked(channel_id, user_id, message, observer_id=None, is_out=False):
    src = "telethon" if observer_id is not None else "bot"
    mid0 = getattr(message, "id", None) or getattr(message, "message_id", None)
    channel_key, bucket = _find_channel_bucket(channel_id)
    if not bucket:
        _log_channel_event(channel_id, mid0, src, "channel not in managed-admin list (saved keys: %s)" % ", ".join(map(str, (bot_settings.get("channel_managed_admins") or {}).keys())) )
        return False
    if message.__class__.__name__ == "MessageService":
        return False
    message_id = getattr(message, "id", None) or getattr(message, "message_id", None)
    if not message_id:
        return False

    signature = getattr(message, "author_signature", None) or getattr(message, "post_author", None)
    uid = _attribute_channel_post(channel_key, bucket, user_id, signature)
    has_real_sender = False
    try:
        has_real_sender = bool(user_id) and int(user_id) > 0
    except (TypeError, ValueError):
        pass
    why = ""
    if not uid and not signature and not has_real_sender and observer_id is not None:
        uid, why = _infer_single_admin(channel_key, bucket, observer_id, is_out, channel_id, message_id)
    _record_channel_diag(channel_key, bool(signature) or has_real_sender, bool(uid))
    if not uid:
        _log_channel_event(channel_id, message_id, src, "not attributed: " + (why or ("author %r matches no managed admin" % signature if signature else "no admin name on post (Sign Messages OFF)")))
        if not signature and not has_real_sender:
            _notify_owner_once(("unsigned", channel_key),
                               f"⚠️ Admin limits cannot work in channel {channel_key} yet.\nTelegram does not tell bots which admin made a post. Open the channel → Edit → turn ON 'Sign Messages' so each admin's name is attached to their posts. Then limits will be counted per admin.")
        return False

    policy = bucket[uid]
    usage, counts = _policy_usage(policy)

    # Several listeners (Bot API + Telethon) can see the same post - count it once.
    seen = bot_settings.setdefault("managed_admin_seen_posts", {})
    key = f"{channel_key}:{message_id}"
    if key in seen:
        return key in _blocked_posts
    seen[key] = time.time()
    if len(seen) > 3000:
        for old_key, _ in sorted(seen.items(), key=lambda item: item[1])[:1000]:
            seen.pop(old_key, None)
            _blocked_posts.pop(old_key, None)

    until = float(policy.get("suspended_until", 0) or 0)
    if until > time.time():
        _delete_channel_post(channel_id, message_id)
        _blocked_posts[key] = time.time()
        _log_channel_event(channel_id, message_id, src, "admin %s is suspended -> post deleted" % uid)
        if not policy.get("rights_revoked", True):
            ok_, _err = _revoke_rights(channel_key, uid, policy)
            policy["rights_revoked"] = bool(ok_)
            sync_db()
        return True
    if until:
        _restore_managed_admin(channel_key, uid, policy)
        usage, counts = _policy_usage(policy)

    kind = _content_quota_keys(message)[0]
    limit = _limit_of(policy, kind)          # 0 = blocked, -1 = unlimited, N = allowed count
    used = int(counts.get(kind, 0) or 0)
    label = {"files": "FILES", "photos": "PHOTOS", "videos": "VIDEOS", "voice": "VOICE", "text": "TEXT"}[kind]
    body = (getattr(message, "text", None) or getattr(message, "message", None)
            or getattr(message, "caption", None) or "")
    blocked_reason = None
    if policy.get("auto_delete_links") and _post_has_link(message, body):
        blocked_reason = "Links are not allowed in your posts."
    elif limit == 0:
        blocked_reason = f"You are NOT allowed to upload {label} in this channel."
    elif limit > 0 and used >= limit:
        blocked_reason = f"Your {label} limit is finished ({used}/{limit})."

    if blocked_reason:
        # 1) delete immediately  2) warn  3) third warning -> suspend for the cooldown
        _delete_channel_post(channel_id, message_id)
        _blocked_posts[key] = time.time()
        _log_channel_event(channel_id, message_id, src, "admin %s: %s -> deleted" % (uid, blocked_reason))
        usage["warnings"] = int(usage.get("warnings", 0) or 0) + 1
        _managed_log(channel_key, uid, "deleted", blocked_reason)
        sync_db()
        warning_no = usage["warnings"]
        wlimit = _warning_limit(policy)
        final = warning_no >= wlimit
        wait_text = _fmt_duration(_cooldown_seconds(policy, True))
        try:
            bot.send_message(int(uid),
                f"⚠️ <b>WARNING {min(warning_no, wlimit)}/{wlimit}</b>\n{html.escape(blocked_reason)}\nYour post was deleted automatically."
                + (f"\n\n⛔ Warning limit reached: your admin rights are removed for <b>{wait_text}</b> and come back automatically." if final else f"\n{wlimit - warning_no} warning(s) left before your rights are paused.")
                + f"\n\n⚠️ সতর্কতা {min(warning_no, wlimit)}/{wlimit}: আপনার পোস্টটি অটোমেটিক মুছে ফেলা হয়েছে।", parse_mode="HTML")
        except Exception:
            pass
        if final:
            _suspend_managed_admin(channel_key, uid, policy, f"{wlimit} WARNINGS", warning=True)
        return True

    counts[kind] = int(counts.get(kind, 0) or 0) + 1
    usage["last_upload_at"] = datetime.now().isoformat()
    _managed_log(channel_key, uid, "counted", f"{kind} {counts[kind]}/{'inf' if limit < 0 else limit}")
    sync_db()
    _log_channel_event(channel_id, message_id, src, "admin %s: counted %s %s/%s%s" % (uid, kind, counts[kind], ("inf" if limit < 0 else limit), (" (" + why + ")") if why else ""))
    try:
        shown = "∞" if limit < 0 else limit
        left = "unlimited" if limit < 0 else f"{max(0, limit - counts[kind])} left"
        bot.send_message(int(uid), f"✅ <b>Post counted</b>\n{label}: <b>{counts[kind]}/{shown}</b> ({left})", parse_mode="HTML")
    except Exception:
        pass
    if _all_enabled_quotas_exhausted(policy):
        _suspend_managed_admin(channel_key, uid, policy, "ALL UPLOAD LIMITS REACHED")
    return False


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
    blocked = [k for k in keys if int(limits.get(k, 0) or 0) == 0 or (int(limits.get(k, 0) or 0) > 0 and counts.get(k, 0) >= int(limits.get(k, 0) or 0))]
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
    """On deploy, re-apply saved managed-admin rights without stripping direct posting."""
    time.sleep(8)
    for channel_id, admins in list(bot_settings.get("channel_managed_admins", {}).items()):
        for user_id, policy in list((admins or {}).items()):
            try:
                if (policy or {}).get("suspended_until"):
                    continue          # suspended admins are handled by the restore loop
                rights = dict((policy or {}).get("permissions") or {})
                if not rights:
                    continue
                _promote_safe(str(channel_id), int(user_id), rights)
            except Exception as exc:
                logger.warning("Could not re-apply saved rights for managed admin %s in %s: %s", user_id, channel_id, exc)


def _restore_due():
    """Give back the rights of every admin whose suspension time is over."""
    n = 0
    for channel_id, admins in list(bot_settings.get("channel_managed_admins", {}).items()):
        for user_id, policy in list((admins or {}).items()):
            until = float((policy or {}).get("suspended_until", 0) or 0)
            if until and until <= time.time():
                if _restore_managed_admin(str(channel_id), str(user_id), policy):
                    n += 1
    _restore_status["last_run"] = time.time()
    return n


def _managed_admin_restore_loop():
    """Runs every 10 s (also right after a restart / wake-up) so no admin stays locked out."""
    time.sleep(2)
    while True:
        try:
            _restore_due()
        except Exception:
            logger.exception("Managed admin cooldown recovery loop failed")
        time.sleep(10)


threading.Thread(target=_enforce_gateway_for_existing_admins, daemon=True, name="managed-admin-gateway").start()
threading.Thread(target=_managed_admin_restore_loop, daemon=True, name="managed-admin-restore").start()

# Telethon (the logged-in user account) can see the actual sender ID on ordinary
# channel posts, unlike the Bot API. Anonymous / send-as-channel posts remain unattributable.
if USERBOT_AVAILABLE and hasattr(ub_module, "set_channel_post_callback"):
    ub_module.set_channel_post_callback(_enforce_direct_channel_post)

# Join-request auto approve, Group Guard, silent purge, member history, keep-alive.
features.install(globals())
try:
    bot.set_my_commands([telebot.types.BotCommand("start", "Start the bot"),
                         telebot.types.BotCommand("help", "How to use this bot"),
                         telebot.types.BotCommand("profile", "Your profile & points")])
except Exception:
    pass

ALLOWED_UPDATES = ["message", "edited_message", "channel_post", "edited_channel_post", "callback_query", "inline_query",
                   "chosen_inline_result", "my_chat_member", "chat_member", "chat_join_request", "message_reaction",
                   "poll", "poll_answer", "pre_checkout_query", "shipping_query"]


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
            _send_stats(chat_id, user_id)
        elif text == "🎁 Daily Reward" or text == "/daily":
            today = datetime.now().date().isoformat()
            if user_activity.get(user_id, {}).get("last_daily") == today:
                bot.send_message(chat_id, "⏳ <b>Already claimed today.</b>\nYour next reward unlocks tomorrow.", parse_mode="HTML")
                return
            if user_id not in user_activity:
                user_activity[user_id] = {"points": 0}
            user_activity[user_id]["points"] = user_activity[user_id].get("points", 0) + bot_settings.get("daily_reward", 10)
            user_activity[user_id]["last_daily"] = today
            sync_db()
            bot.send_message(chat_id, f"🎁 <b>Daily reward claimed!</b>\n+{bot_settings.get('daily_reward', 10)} points added to your profile.\nCome back tomorrow for more.", parse_mode="HTML")
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
            _send_help(chat_id)
        elif text == "👤 Profile" or text == "/profile":
            _send_profile(chat_id, message.from_user)
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
            bot.send_message(chat_id, "🤔 <b>I did not understand that.</b>\nUse the buttons below or send /help.", parse_mode="HTML", reply_markup=get_main_keyboard())
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
                    bot.infinity_polling(timeout=30, long_polling_timeout=20, allowed_updates=ALLOWED_UPDATES)
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

        def _graceful_exit(signum=None, frame=None):
            try:
                logger.warning("Shutting down - saving data ...")
                flush_db()
                vault.save_now()
            finally:
                os._exit(0)

        try:
            signal.signal(signal.SIGTERM, _graceful_exit)
        except Exception:
            pass
        atexit.register(lambda: flush_db())
        vault.start()
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
            bot.infinity_polling(timeout=30, long_polling_timeout=20, allowed_updates=ALLOWED_UPDATES)
        except Exception as e:
            logger.error(f"Crash: {e}")
            sync_db()
