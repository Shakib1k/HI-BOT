# -*- coding: utf-8 -*-
"""
Telegram vault: keeps the bot database alive across restarts / redeploys on hosts with an
ephemeral disk (Render free plan).

How it works
  * The database file is gzip-compressed and stored as ONE document message in the owner's
    private chat with the bot. That message is pinned and edited in place (no spam).
  * On start-up, if the local database file is missing, the pinned message is downloaded and
    restored before the bot loads its data - links, users, channel admins and the time at
    which suspended admins get their rights back all survive a restart.
  * Uploads are rate-limited (at most one per 5 minutes, or 15 seconds for urgent events such as
    an admin suspension) and only happen when the data changed.

The owner can switch it off in the panel (Backup & Restore). Nothing here touches Telegram
unless the owner has opened the bot chat at least once (Telegram forbids bots to message
people who never started them).
"""
import gzip
import io
import json
import logging
import os
import threading
import time
from datetime import datetime

logger = logging.getLogger("vault")

TAG = "BOT_DB_VAULT"
MIN_INTERVAL = 300          # seconds between normal uploads
URGENT_INTERVAL = 15
MAX_BYTES = 45 * 1024 * 1024

_bot = None
_owner = None
_db_file = None
_enabled = lambda: True
_dirty = threading.Event()
_urgent = threading.Event()
_lock = threading.Lock()
_msg_id = None
_last_upload = 0.0
status = {"last_ok": None, "last_error": None, "last_size": 0, "restored": False, "message_id": None}


def init(bot, owner_id, db_file, enabled_fn=None):
    global _bot, _owner, _db_file, _enabled
    _bot, _owner, _db_file = bot, int(owner_id), db_file
    if enabled_fn:
        _enabled = enabled_fn


def _pinned_vault():
    """The vault message pinned in the owner's chat (or None)."""
    chat = _bot.get_chat(_owner)
    pm = getattr(chat, "pinned_message", None)
    if pm is not None and getattr(pm, "document", None) and TAG in (pm.caption or ""):
        return pm
    return None


def restore_if_missing():
    """Call once, before the database is loaded. Returns True when the file was restored."""
    try:
        if os.path.isfile(_db_file) and os.path.getsize(_db_file) > 2:
            return False
        pm = _pinned_vault()
        if pm is None:
            logger.info("vault: nothing to restore (no pinned vault message)")
            return False
        info = _bot.get_file(pm.document.file_id)
        raw = _bot.download_file(info.file_path)
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass                       # was stored uncompressed
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("bot_settings"), dict):
            raise ValueError("vault file is not a bot database")
        tmp = _db_file + ".vault_tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, _db_file)
        global _msg_id
        _msg_id = pm.message_id
        status.update(restored=True, message_id=_msg_id, last_ok=datetime.now().isoformat(timespec="seconds"))
        logger.warning("vault: database restored from Telegram (%d users, %d files)",
                       len(data.get("registered_users", [])), len(data.get("file_database", {})))
        return True
    except Exception as exc:
        status["last_error"] = str(exc)[:200]
        logger.error("vault restore failed: %s", exc)
        return False


def touch(urgent=False):
    """Tell the vault the database changed."""
    if urgent:
        _urgent.set()
    _dirty.set()


def _caption(raw_len):
    return f"🗄 {TAG}\nAutomatic database copy · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nSize: {raw_len/1024:.0f} KB\nDo not delete this pinned message - it restores your bot after a restart."


def _upload_locked():
    global _msg_id, _last_upload
    if not _enabled() or not os.path.isfile(_db_file):
        return False
    with open(_db_file, "rb") as f:
        raw = f.read()
    blob = gzip.compress(raw, 6)
    if len(blob) > MAX_BYTES:
        status["last_error"] = "database is larger than 45 MB; vault skipped"
        return False
    from telebot import types

    def make_file():
        b = io.BytesIO(blob)
        b.name = "bot_database.json.gz"
        return b

    caption = _caption(len(raw))
    done = False
    if _msg_id is None:
        try:
            pm = _pinned_vault()
            _msg_id = pm.message_id if pm else None
        except Exception:
            _msg_id = None
    if _msg_id:
        try:
            _bot.edit_message_media(types.InputMediaDocument(make_file(), caption=caption), _owner, _msg_id)
            done = True
        except Exception as exc:
            if "not modified" in str(exc).lower():
                done = True
            else:
                _msg_id = None         # message deleted / cannot be edited -> send a fresh one
    if not done:
        sent = _bot.send_document(_owner, make_file(), caption=caption, disable_notification=True)
        old = _msg_id
        _msg_id = sent.message_id
        try:
            _bot.pin_chat_message(_owner, _msg_id, disable_notification=True)
        except Exception as exc:
            logger.warning("vault: could not pin the vault message: %s", exc)
        if old:
            try:
                _bot.delete_message(_owner, old)
            except Exception:
                pass
    _last_upload = time.time()
    status.update(last_ok=datetime.now().isoformat(timespec="seconds"), last_error=None, last_size=len(blob), message_id=_msg_id)
    return True


def save_now():
    """Upload immediately (panel button / shutdown). Returns (ok, error)."""
    with _lock:
        try:
            ok = _upload_locked()
            return ok, status.get("last_error")
        except Exception as exc:
            status["last_error"] = str(exc)[:200]
            logger.error("vault upload failed: %s", exc)
            return False, status["last_error"]


def _loop():
    time.sleep(20)
    while True:
        _dirty.wait()
        while True:                    # wait out the interval, but let an urgent event cut it short
            since = time.time() - _last_upload
            if since >= MIN_INTERVAL or (_urgent.is_set() and since >= URGENT_INTERVAL):
                break
            time.sleep(1)
        _dirty.clear()
        _urgent.clear()
        if _enabled():
            save_now()
        time.sleep(2)


def start():
    threading.Thread(target=_loop, daemon=True, name="vault").start()
