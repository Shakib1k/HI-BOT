import asyncio
import json
import os
import time
import random
import logging
from datetime import datetime

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError
from telethon.tl.types import UserStatusOnline, UserStatusOffline
from google import genai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("USERBOT")

USERBOT_DB = "userbot_sessions.json"

API_ID = int(os.environ.get("TELEGRAM_API_ID", "32240182").strip())
API_HASH = os.environ.get("TELEGRAM_API_HASH", "").strip()
if not API_HASH:
    logger.warning("TELEGRAM_API_HASH is not set; userbot login will fail until it is configured.")

MODEL_LIST = [
    "gemini-3-flash-preview",
    "gemini-1.5-pro"
]

# ট্র্যাক করবে আপনি (Owner) অনলাইনে আছেন কিনা
OWNER_ACTIVE = {}

def load_ub():
    if os.path.exists(USERBOT_DB):
        try:
            with open(USERBOT_DB, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_ub(data):
    with open(USERBOT_DB, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

ACTIVE_CLIENTS = {}

# Callback supplied by main.py for direct channel-post quota enforcement.
CHANNEL_POST_CALLBACK = None
_MONITORED_CLIENTS = set()


def set_channel_post_callback(callback):
    global CHANNEL_POST_CALLBACK
    CHANNEL_POST_CALLBACK = callback
    for info in list(ACTIVE_CLIENTS.values()):
        client = info.get("client")
        if client:
            _attach_channel_post_monitor(client)


def _attach_channel_post_monitor(client):
    """Observe ordinary broadcast-channel posts and pass the real sender ID to main."""
    marker = id(client)
    if marker in _MONITORED_CLIENTS:
        return
    _MONITORED_CLIENTS.add(marker)

    @client.on(events.NewMessage)
    async def managed_channel_post_observer(event):
        try:
            if CHANNEL_POST_CALLBACK is None or not event.is_channel or event.is_group:
                return
            sender_id = event.sender_id
            channel_id = event.chat_id
            if not sender_id or not channel_id:
                # Anonymous admins / send-as-channel do not expose a human sender.
                return
            await asyncio.to_thread(CHANNEL_POST_CALLBACK, channel_id, sender_id, event.message)
        except Exception as exc:
            logger.exception("Managed channel post observer failed: %s", exc)

def cleanup_old_sessions():
    for file in os.listdir("."):
        if file.startswith("session_") and file.endswith(".session"):
            try:
                os.remove(file)
            except:
                pass

async def process_and_reply(api_keys, user_text):
    prompt = (
        "You are a friendly, natural, respectful AI assistant replying to a Telegram private message. "
        "Always reply in clear, natural English, regardless of the language used by the sender. "
        "Keep the reply concise, engaging, and context-aware. Use emojis sparingly and naturally. "
        "Do not claim to be a human, and do not prefix the reply with 'AI'.\n\n"
        f"User message: {user_text}"
    )
    
    for api_key in api_keys:
        try:
            ai_client = genai.Client(api_key=api_key)
            for model_name in MODEL_LIST:
                try:
                    response = await asyncio.to_thread(
                        ai_client.models.generate_content,
                        model=model_name,
                        contents=prompt
                    )
                    if response.text:
                        return response.text.strip()
                except Exception as e:
                    err = str(e)
                    if "429" in err or "RESOURCE_EXHAUSTED" in err:
                        logger.warning(f"API Key Quota Exceeded. Trying next key...")
                        break 
                    elif "404" in err:
                        continue 
                    else:
                        logger.error(f"Model {model_name} error: {e}")
                        continue
        except Exception as e:
            logger.error(f"Key init error: {e}")
            continue
            
    return None

async def start_userbot(phone: str, api_keys_list: list, user_id: int):
    cleanup_old_sessions()
    if phone in ACTIVE_CLIENTS:
        try:
            await ACTIVE_CLIENTS[phone]["client"].disconnect()
        except:
            pass
        del ACTIVE_CLIENTS[phone]
        await asyncio.sleep(2)

    session_file = f"session_{phone}_{int(time.time())}"
    client = TelegramClient(session_file, API_ID, API_HASH, timeout=60)
    
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        ACTIVE_CLIENTS[phone] = {
            "client": client,
            "api_keys": api_keys_list,
            "user_id": user_id,
            "phone_code_hash": sent.phone_code_hash,
            "logged_in": False,
            "session_file": session_file
        }
        OWNER_ACTIVE[phone] = False
        return {"ok": True, "msg": "OTP sent"}
    except FloodWaitError as e:
        return {"ok": False, "msg": f"Flood wait {e.seconds}s"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}

async def verify_code(phone: str, code: str):
    if phone not in ACTIVE_CLIENTS:
        return {"ok": False, "msg": "No pending session"}

    info = ACTIVE_CLIENTS[phone]
    client = info["client"]

    try:
        await client.sign_in(phone, code, phone_code_hash=info["phone_code_hash"])
    except SessionPasswordNeededError:
        return {"ok": False, "msg": "2FA_PASSWORD_REQUIRED"}
    except PhoneCodeInvalidError:
        return {"ok": False, "msg": "Invalid code"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}

    return await _finish_login(phone)


async def verify_2fa(phone: str, password: str):
    if phone not in ACTIVE_CLIENTS:
        return {"ok": False, "msg": "No pending session"}
    client = ACTIVE_CLIENTS[phone]["client"]
    try:
        await client.sign_in(password=password)
    except Exception as e:
        return {"ok": False, "msg": str(e)}
    return await _finish_login(phone)


async def _finish_login(phone: str):
    info = ACTIVE_CLIENTS[phone]
    client = info["client"]
    info["logged_in"] = True
    _attach_channel_post_monitor(client)
    keys = info["api_keys"]
    my_id = info["user_id"]

    # অনলাইন/অফলাইন ট্র্যাকার
    @client.on(events.UserUpdate)
    async def user_status_update(event):
        if event.user_id == my_id:
            if isinstance(event.status, UserStatusOnline):
                OWNER_ACTIVE[phone] = True
                logger.info(f"Owner is ONLINE. AI Paused.")
            elif isinstance(event.status, UserStatusOffline):
                OWNER_ACTIVE[phone] = False
                logger.info(f"Owner is OFFLINE. AI Active.")

    # অটো রিপ্লাই লিসেনার
    @client.on(events.NewMessage(incoming=True))
    async def auto_reply(event):
        try:
            # ✅ ১. আপনার নিজের পাঠানো মেসেজে কখনো রিপ্লাই দেবে না
            if event.out:
                logger.info("Owner's own message detected. Skipping reply.")
                return
                
            if not event.is_private: 
                return
            
            # ✅ ২. আপনি যদি অনলাইনে থাকেন, তবে রিপ্লাই দেবে না
            if OWNER_ACTIVE.get(phone, False):
                logger.info("Owner online. Skipping.")
                return

            user_text = event.message.text or ""
            if not user_text.strip() or user_text.startswith("🤖 AI:"): 
                return

            reply_text = await process_and_reply(keys, user_text)
            
            if reply_text:
                await asyncio.sleep(random.randint(1, 3))
                await event.reply(reply_text)
        except Exception as e:
            logger.error(f"Auto-reply error: {e}")

    data = load_ub()
    data[phone] = {
        "user_id": info["user_id"],
        "api_keys": keys,
        "logged_in": True,
        "connected_at": datetime.now().isoformat(),
        "session_file": info["session_file"]
    }
    save_ub(data)

    asyncio.create_task(client.run_until_disconnected())
    return {"ok": True, "msg": "Logged in & AI active"}

async def stop_userbot(phone: str):
    if phone in ACTIVE_CLIENTS:
        try:
            await ACTIVE_CLIENTS[phone]["client"].disconnect()
        except: pass
        del ACTIVE_CLIENTS[phone]
    data = load_ub()
    if phone in data:
        data[phone]["logged_in"] = False
        save_ub(data)
    if phone in OWNER_ACTIVE:
        del OWNER_ACTIVE[phone]
    return {"ok": True}

async def restore_sessions():
    data = load_ub()
    for phone, info in data.items():
        if not info.get("logged_in") or phone in ACTIVE_CLIENTS: 
            continue
        try:
            session_file = info.get("session_file")
            if not session_file or not os.path.exists(session_file + ".session"): 
                continue

            client = TelegramClient(session_file, API_ID, API_HASH, timeout=60)
            await client.connect()
            if not await client.is_user_authorized(): 
                continue

            keys = info.get("api_keys", [])
            my_id = info["user_id"]

            ACTIVE_CLIENTS[phone] = {
                "client": client,
                "api_keys": keys,
                "user_id": info["user_id"],
                "logged_in": True,
                "session_file": session_file
            }
            OWNER_ACTIVE[phone] = False # রিস্টার্টে ডিফল্ট অফলাইন
            _attach_channel_post_monitor(client)

            @client.on(events.UserUpdate)
            async def user_status_update(event, ph=phone, uid=my_id):
                if event.user_id == uid:
                    if isinstance(event.status, UserStatusOnline):
                        OWNER_ACTIVE[ph] = True
                        logger.info(f"Restored: Owner ONLINE.")
                    elif isinstance(event.status, UserStatusOffline):
                        OWNER_ACTIVE[ph] = False
                        logger.info(f"Restored: Owner OFFLINE.")

            @client.on(events.NewMessage(incoming=True))
            async def auto_reply(event, ph=phone):
                try:
                    if event.out: # নিজের মেসেজে রিপ্লাই বন্ধ
                        return
                    if not event.is_private: 
                        return
                    if OWNER_ACTIVE.get(ph, False):
                        return
                        
                    user_text = event.message.text or ""
                    if not user_text.strip() or user_text.startswith("🤖 AI:"): 
                        return

                    k = ACTIVE_CLIENTS[ph]["api_keys"]
                    reply = await process_and_reply(k, user_text)
                    if reply:
                        await asyncio.sleep(random.randint(1, 3))
                        await event.reply(reply)
                except Exception as e:
                    logger.error(f"Restore reply err: {e}")

            asyncio.create_task(client.run_until_disconnected())
            logger.info(f"Restored session: {phone}")
        except Exception as e:
            logger.error(f"Restore failed {phone}: {e}")