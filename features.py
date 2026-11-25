"""
Runtime features (kept separate from the channel-admin limit logic in main.py):

  1. Join-request auto approve (private channels / groups)
  2. Group Guard  (links, stickers, scammers, flood, translate, welcome ...)
  3. Silent Target Purge (auto-delete everything posted by chosen user IDs)
  4. Member history snapshots (live member counts + growth)
  5. Keep-alive ping (so Render free instances do not fall asleep)

main.py calls install(globals()) once.
"""
import html
import inspect
import json
import logging
import os
import queue
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from urllib.parse import quote

import requests
import telebot
from telebot.types import ChatPermissions

logger = logging.getLogger("features")

C = None          # main.py globals
bot = None
_dirty = threading.Event()


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def S():
    """Always read bot_settings fresh (clear-all / restore may rebind it)."""
    return C["bot_settings"]


def sync_later():
    _dirty.set()


def _flusher():
    while True:
        _dirty.wait()
        time.sleep(3)          # coalesce bursts into a single disk write
        _dirty.clear()
        try:
            C["sync_db"]()
        except Exception:
            logger.exception("features: sync failed")


def same_chat(a, b):
    a, b = str(a).strip(), str(b).strip()
    if a == b:
        return True

    def core(x):
        x = x.lstrip("-")
        return x[3:] if x.startswith("100") and len(x) > 10 else x
    return a.lstrip("-").isdigit() and b.lstrip("-").isdigit() and core(a) == core(b)


def find_in(mapping, chat_id):
    for k, v in (mapping or {}).items():
        if same_chat(k, chat_id):
            return k, v
    return None, None


def owner_notify(text):
    try:
        bot.send_message(C["ADMIN_ID"], text)
    except Exception:
        pass


def _kw(func, **kwargs):
    """Call-safe kwargs: drop parameters this telebot version does not know."""
    try:
        names = set(inspect.signature(func).parameters)
    except Exception:
        return kwargs
    return {k: v for k, v in kwargs.items() if k in names}


def _perms(**kwargs):
    return ChatPermissions(**_kw(ChatPermissions.__init__, **kwargs))


def _retry_after(exc):
    try:
        return int(exc.result_json["parameters"]["retry_after"])
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# 1. JOIN REQUESTS
# ---------------------------------------------------------------------------
_jr_queue = queue.Queue()


def jr_cfg():
    return S().setdefault("join_requests", {})


def jr_log(chat_key, text):
    lg = S().setdefault("join_request_log", [])
    lg.append({"t": datetime.now().isoformat(timespec="seconds"), "ch": str(chat_key), "d": str(text)[:160]})
    del lg[:-200]


def on_join_request(req):
    try:
        key, cfg = find_in(jr_cfg(), req.chat.id)
        if not cfg or not cfg.get("auto", True):
            return
        _jr_queue.put((key, cfg, req))
    except Exception:
        logger.exception("join request handler")


def _jr_worker():
    while True:
        key, cfg, req = _jr_queue.get()
        try:
            delay = min(float(cfg.get("delay", 0) or 0), 900)
            wait = delay - (time.time() - getattr(req, "date", time.time()) if isinstance(getattr(req, "date", None), (int, float)) else 0)
            if delay > 0 and wait > 0:
                time.sleep(min(wait, delay))
            for attempt in range(3):
                try:
                    bot.approve_chat_join_request(req.chat.id, req.from_user.id)
                    break
                except Exception as exc:
                    ra = _retry_after(exc)
                    msg = str(exc)
                    if ra and attempt < 2:
                        time.sleep(ra + 1)
                        continue
                    if "USER_ALREADY_PARTICIPANT" in msg or "HIDE_REQUESTER_MISSING" in msg:
                        break
                    cfg["failed"] = int(cfg.get("failed", 0) or 0) + 1
                    cfg["last_error"] = msg[:160]
                    jr_log(key, f"approve failed for {req.from_user.id}: {msg[:100]}")
                    sync_later()
                    if cfg["failed"] in (1, 10, 100):
                        owner_notify(f"⚠️ Could not approve a join request in {cfg.get('title') or key}.\n{msg[:200]}\n\nGive the bot the 'Invite users via link' admin right in that chat.")
                    raise
            cfg["approved"] = int(cfg.get("approved", 0) or 0) + 1
            cfg["last_approved_at"] = datetime.now().isoformat(timespec="seconds")
            today = datetime.now().strftime("%Y-%m-%d")
            days = cfg.setdefault("by_day", {})
            days[today] = int(days.get(today, 0) or 0) + 1
            for d in sorted(days)[:-14]:
                days.pop(d, None)
            cfg.pop("last_error", None)
            sync_later()
            if cfg.get("welcome"):
                txt = (cfg.get("welcome_text") or "👋 Welcome {name}! Your request to join {chat} was approved.")
                txt = txt.replace("{name}", html.escape(req.from_user.first_name or "friend")).replace("{chat}", html.escape(cfg.get("title") or req.chat.title or "the channel"))
                try:
                    bot.send_message(getattr(req, "user_chat_id", None) or req.from_user.id, txt, parse_mode="HTML")
                except Exception:
                    pass
            time.sleep(0.06)     # ~16 approvals / second max: stays under Telegram flood limits
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 2. GROUP GUARD
# ---------------------------------------------------------------------------
GROUP_DEFAULTS = {
    "enabled": True,
    "delete_links": False, "delete_mentions": False,
    "no_stickers": False, "no_gifs": False, "no_forwards": False, "no_voice": False,
    "anti_scam": True, "cas_check": True, "block_impersonation": True,
    "anti_flood": False, "flood_limit": 6, "flood_seconds": 8,
    "delete_service": False, "banned_words": [],
    "new_member_minutes": 0,
    "warn_limit": 3, "action": "mute", "mute_minutes": 30,
    "notify": True,
    "welcome": False, "welcome_text": "👋 Welcome {name} to {group}!\nPlease be respectful and follow the rules.",
    "translate": True, "lock": False,
}
_gr_admin_cache = {}
_flood = defaultdict(lambda: deque(maxlen=40))
_cas_cache = {}
BOT_ID = None

SCAM_RE = re.compile(
    r"(airdrop|crypto\s*giveaway|usdt|binance|forex|investment|guaranteed\s*profit|double\s*your|"
    r"whats\s*app\s*\+?\d|dm\s*me|inbox\s*me|earn\s*\$?\s*\d+|trust\s*wallet|seed\s*phrase|private\s*key|"
    r"claim\s*(your\s*)?(reward|prize|bonus)|hack(ing)?\s*service|recover\s*(your\s*)?(funds|account)|"
    r"পার্ট\s*টাইম\s*জব|ইনকাম\s*করুন|ডলার\s*ইনকাম)", re.I)
LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|\b[a-z0-9-]+\.(com|net|org|io|xyz|me|info|ru|in|bd|top|site|link|live|co)\b)", re.I)
MENTION_RE = re.compile(r"(?<!\w)@[A-Za-z][A-Za-z0-9_]{3,}")
SUSPICIOUS_NAME_RE = re.compile(r"(support|official|admin|helpdesk|customer\s*care|airdrop|giveaway|crypto|invest|signal|forex|earn\s*\$)", re.I)


def gr_cfg():
    return S().setdefault("groups", {})


def gr_full(cfg):
    out = dict(GROUP_DEFAULTS)
    out.update({k: v for k, v in cfg.items() if k in GROUP_DEFAULTS})
    return out


def _gr_stat(cfg, name, n=1):
    st = cfg.setdefault("stats", {})
    st[name] = int(st.get(name, 0) or 0) + n
    sync_later()


def gr_log(chat_key, uid, ev, detail=""):
    lg = S().setdefault("group_log", [])
    lg.append({"t": datetime.now().isoformat(timespec="seconds"), "ch": str(chat_key), "uid": str(uid), "ev": ev, "d": str(detail)[:140]})
    del lg[:-300]
    sync_later()


def _admins(chat_id):
    t, data = _gr_admin_cache.get(str(chat_id), (0, None))
    if data is not None and time.time() - t < 60:
        return data
    try:
        data = {a.user.id: a for a in bot.get_chat_administrators(chat_id)}
    except Exception:
        data = data or {}
    _gr_admin_cache[str(chat_id)] = (time.time(), data)
    return data


def _is_exempt(chat_id, message):
    u = message.from_user
    if getattr(message, "sender_chat", None) is not None:       # anonymous admin / linked channel / channel post
        return True
    if getattr(message, "is_automatic_forward", False):
        return True
    if not u:
        return True
    if u.id == C["ADMIN_ID"]:
        return True
    return u.id in _admins(chat_id)


def _delete(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
        return True
    except Exception as exc:
        logger.warning("group guard delete failed %s/%s: %s", chat_id, message_id, exc)
        return False


def _notice(chat_id, text, cfg, seconds=9):
    if not cfg.get("notify", True):
        return
    try:
        m = bot.send_message(chat_id, text, parse_mode="HTML")
        threading.Timer(seconds, lambda: _delete(chat_id, m.message_id)).start()
    except Exception:
        pass


def _name(u):
    n = ((u.first_name or "") + " " + (u.last_name or "")).strip() or (u.username or str(u.id))
    return html.escape(n)


def has_link(message, text):
    if LINK_RE.search(text or ""):
        return True
    for e in list(message.entities or []) + list(message.caption_entities or []):
        if getattr(e, "type", "") in ("url", "text_link"):
            return True
    return False


def scam_text(message, text):
    hits = {m.group(0).lower() for m in SCAM_RE.finditer(text or "")}
    if len(hits) >= 2:
        return True
    return bool(hits) and (has_link(message, text) or bool(MENTION_RE.search(text or "")) or bool(re.search(r"\+?\d[\d\s-]{8,}", text or "")))


def _punish(chat_id, cfg, user, why, key):
    """Warn / mute / kick / ban according to the group's ladder."""
    warns = cfg.setdefault("warns", {})
    rec = warns.get(str(user.id)) or [0, 0]
    if time.time() - rec[1] > 86400:
        rec = [0, 0]
    rec = [rec[0] + 1, time.time()]
    warns[str(user.id)] = rec
    limit = max(1, int(cfg.get("warn_limit", 3) or 3))
    _gr_stat(cfg, "warned")
    gr_log(key, user.id, "warned", f"{why} ({rec[0]}/{limit})")
    if rec[0] < limit:
        _notice(chat_id, f"⚠️ <b>{_name(user)}</b>, {html.escape(why)}.\nWarning <b>{rec[0]}/{limit}</b>", cfg)
        return
    warns.pop(str(user.id), None)
    action = cfg.get("action", "mute")
    try:
        if action == "ban":
            bot.ban_chat_member(chat_id, user.id)
            _gr_stat(cfg, "banned")
            msg = "banned"
        elif action == "kick":
            bot.ban_chat_member(chat_id, user.id)
            bot.unban_chat_member(chat_id, user.id, only_if_banned=True)
            _gr_stat(cfg, "kicked")
            msg = "removed from the group"
        else:
            mins = max(1, int(cfg.get("mute_minutes", 30) or 30))
            bot.restrict_chat_member(chat_id, user.id, permissions=_perms(can_send_messages=False), until_date=int(time.time()) + mins * 60)
            _gr_stat(cfg, "muted")
            msg = f"muted for {mins} min"
        gr_log(key, user.id, action, why)
        _notice(chat_id, f"⛔ <b>{_name(user)}</b> was {msg} after {limit} warnings.", cfg)
    except Exception as exc:
        gr_log(key, user.id, "punish_failed", str(exc)[:100])
        owner_notify(f"⚠️ Group Guard could not {action} user {user.id} in {key}.\n{str(exc)[:160]}\nGive the bot 'Ban users' (restrict) rights.")


def _cas_banned(uid):
    t, val = _cas_cache.get(uid, (0, None))
    if val is not None and time.time() - t < 3600:
        return val
    val = False
    try:
        r = requests.get("https://api.cas.chat/check", params={"user_id": uid}, timeout=4)
        val = bool(r.json().get("ok"))
    except Exception:
        pass
    _cas_cache[uid] = (time.time(), val)
    return val


def translate_text(text, target="bn"):
    r = requests.get("https://translate.googleapis.com/translate_a/single",
                     params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text[:3500]}, timeout=8)
    data = r.json()
    out = "".join(part[0] for part in data[0] if part and part[0])
    return out, (data[2] if len(data) > 2 else "auto")


def _handle_translate(message, cfg):
    parts = (message.text or "").split()
    target = (parts[1] if len(parts) > 1 else "bn").lower()[:8]
    src = message.reply_to_message
    text = (src.text or src.caption) if src else " ".join(parts[2:]) if len(parts) > 2 else ""
    if not text:
        bot.reply_to(message, "ℹ️ Reply to a message with <code>/tr en</code> (or <code>/tr bn</code>) to translate it.", parse_mode="HTML")
        return
    try:
        out, detected = translate_text(text, target)
        bot.reply_to(message, f"🌐 <b>{html.escape(detected)} → {html.escape(target)}</b>\n\n{html.escape(out)}", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "❌ Translation service is busy. Try again in a moment.")


def group_guard(message):
    """Returns True when the message was removed (caller should stop processing it)."""
    try:
        chat_id = message.chat.id
        key, cfg0 = find_in(gr_cfg(), chat_id)
        if not cfg0:
            return False
        cfg = gr_full(cfg0)
        if not cfg["enabled"]:
            return False
        text = message.text or message.caption or ""
        if cfg["translate"] and message.text and re.match(r"^/(tr|translate)(@\w+)?(\s|$)", message.text):
            _handle_translate(message, cfg)
            return False
        if _is_exempt(chat_id, message):
            return False
        u = message.from_user
        ct = message.content_type
        why = None
        strong = False
        if cfg["anti_scam"] and scam_text(message, text):
            why, strong = "scam / spam content is not allowed", True
        elif cfg["delete_links"] and has_link(message, text):
            why = "links are not allowed"
        elif cfg["delete_mentions"] and MENTION_RE.search(text):
            why = "@mentions are not allowed"
        elif cfg["no_stickers"] and ct in ("sticker", "dice"):
            why = "stickers and emoji are disabled"
        elif cfg["no_gifs"] and ct == "animation":
            why = "GIFs are disabled"
        elif cfg["no_voice"] and ct in ("voice", "video_note"):
            why = "voice messages are disabled"
        elif cfg["no_forwards"] and (getattr(message, "forward_date", None) or getattr(message, "forward_origin", None)):
            why = "forwarded messages are not allowed"
        elif cfg["banned_words"] and any(w and w.lower() in text.lower() for w in cfg["banned_words"]):
            why = "that word is not allowed here"
        elif cfg["anti_flood"]:
            dq = _flood[(str(chat_id), u.id)]
            now = time.time()
            dq.append(now)
            recent = [t for t in dq if now - t <= int(cfg["flood_seconds"])]
            if len(recent) > int(cfg["flood_limit"]):
                why = "please do not flood the chat"
        if not why:
            return False
        _delete(chat_id, message.message_id)
        _gr_stat(cfg0, "deleted")
        gr_log(key, u.id, "deleted", why)
        if strong and cfg["anti_scam"]:
            try:
                bot.ban_chat_member(chat_id, u.id)
                _gr_stat(cfg0, "banned")
                gr_log(key, u.id, "banned", "scam content")
                _notice(chat_id, f"🚫 <b>{_name(u)}</b> was banned for posting scam content.", cfg)
            except Exception as exc:
                gr_log(key, u.id, "ban_failed", str(exc)[:100])
                _punish(chat_id, cfg0_with_defaults(cfg0), u, why, key)
        else:
            _punish(chat_id, cfg0_with_defaults(cfg0), u, why, key)
        return True
    except Exception:
        logger.exception("group guard")
        return False


def cfg0_with_defaults(cfg0):
    for k, v in GROUP_DEFAULTS.items():
        cfg0.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)(v))
    return cfg0


def on_service_message(message):
    try:
        chat_id = message.chat.id
        key, cfg0 = find_in(gr_cfg(), chat_id)
        if not cfg0:
            return
        cfg = gr_full(cfg0)
        if not cfg["enabled"]:
            return
        ct = message.content_type
        if ct == "new_chat_members":
            for u in message.new_chat_members or []:
                if BOT_ID and u.id == BOT_ID:
                    continue
                _gr_stat(cfg0, "joined")
                banned = False
                if not u.is_bot and cfg["anti_scam"]:
                    reason = None
                    full = ((u.first_name or "") + " " + (u.last_name or "")).strip()
                    if cfg["block_impersonation"]:
                        for aid, a in _admins(chat_id).items():
                            an = ((a.user.first_name or "") + " " + (a.user.last_name or "")).strip()
                            if full and an and full.casefold() == an.casefold() and aid != u.id:
                                reason = "impersonating an admin"
                                break
                        if not reason and SUSPICIOUS_NAME_RE.search(full + " " + (u.username or "")):
                            reason = "suspicious profile name"
                    if not reason and cfg["cas_check"] and _cas_banned(u.id):
                        reason = "listed in the global spam database (CAS)"
                    if reason:
                        try:
                            bot.ban_chat_member(chat_id, u.id)
                            banned = True
                            _gr_stat(cfg0, "banned")
                            gr_log(key, u.id, "banned", reason)
                            _notice(chat_id, f"🚫 <b>{_name(u)}</b> was removed: {reason}.", cfg)
                        except Exception as exc:
                            gr_log(key, u.id, "ban_failed", str(exc)[:100])
                if banned:
                    continue
                mins = int(cfg["new_member_minutes"] or 0)
                if mins > 0 and not u.is_bot:
                    try:
                        bot.restrict_chat_member(chat_id, u.id, permissions=_perms(
                            can_send_messages=True, can_send_media_messages=False, can_send_audios=False, can_send_documents=False,
                            can_send_photos=False, can_send_videos=False, can_send_video_notes=False, can_send_voice_notes=False,
                            can_send_polls=False, can_send_other_messages=False, can_add_web_page_previews=False),
                            until_date=int(time.time()) + mins * 60)
                    except Exception as exc:
                        gr_log(key, u.id, "restrict_failed", str(exc)[:100])
                if cfg["welcome"] and not u.is_bot:
                    txt = cfg["welcome_text"].replace("{name}", _name(u)).replace("{group}", html.escape(message.chat.title or ""))
                    try:
                        m = bot.send_message(chat_id, txt, parse_mode="HTML")
                        threading.Timer(60, lambda: _delete(chat_id, m.message_id)).start()
                    except Exception:
                        pass
        if cfg["delete_service"]:
            _delete(chat_id, message.message_id)
    except Exception:
        logger.exception("group service handler")


def group_set_lock(chat_id, lock):
    if lock:
        bot.set_chat_permissions(chat_id, _perms(can_send_messages=False))
    else:
        bot.set_chat_permissions(chat_id, _perms(
            can_send_messages=True, can_send_media_messages=True, can_send_audios=True, can_send_documents=True,
            can_send_photos=True, can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True,
            can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True))


# ---------------------------------------------------------------------------
# 3. SILENT TARGET PURGE
# ---------------------------------------------------------------------------
_purge_seen = {}
_purge_admins = {}


def purge_cfg():
    return S().setdefault("purge_targets", {})


def purge_log(uid, chat_id, what):
    lg = S().setdefault("purge_log", [])
    lg.append({"t": datetime.now().isoformat(timespec="seconds"), "uid": str(uid), "ch": str(chat_id), "d": what})
    del lg[:-200]


def _purge_names(chat_id, uid):
    """Names Telegram prints as the author signature for this admin in this channel."""
    t, data = _purge_admins.get(str(chat_id), (0, None))
    if data is None or time.time() - t > 120:
        try:
            data = {a.user.id: a for a in bot.get_chat_administrators(chat_id)}
        except Exception:
            data = data or {}
        _purge_admins[str(chat_id)] = (time.time(), data)
    a = data.get(int(uid))
    if not a:
        return None
    u = a.user
    names = {((u.first_name or "") + " " + (u.last_name or "")).strip(), u.first_name or "", u.username or "", getattr(a, "custom_title", "") or ""}
    return {n.casefold() for n in names if n}


def purge_check(chat_id, message, sender_id=None):
    """True if the post belongs to a purge target and was deleted. Completely silent."""
    try:
        targets = purge_cfg()
        if not targets:
            return False
        mid = getattr(message, "id", None) or getattr(message, "message_id", None)
        if not mid:
            return False
        key = f"{chat_id}:{mid}"
        if key in _purge_seen:
            return _purge_seen[key]
        sig = getattr(message, "author_signature", None) or getattr(message, "post_author", None)
        frm = getattr(getattr(message, "from_user", None), "id", None) or (sender_id if sender_id and int(sender_id) > 0 else None)
        for uid, t in targets.items():
            if not t.get("active", True):
                continue
            scope = t.get("channels") or []
            if scope and not any(same_chat(s, chat_id) for s in scope):
                continue
            hit = False
            if frm is not None and int(frm) == int(uid):
                hit = True
            elif sig:
                names = _purge_names(chat_id, uid)
                if names and sig.strip().casefold() in names:
                    hit = True
            if not hit:
                continue
            ok = False
            for attempt in range(2):
                try:
                    bot.delete_message(int(chat_id), int(mid))
                    ok = True
                    break
                except Exception as exc:
                    ra = _retry_after(exc)
                    if ra:
                        time.sleep(min(ra, 5))
                    else:
                        break
            _purge_seen[key] = True
            if len(_purge_seen) > 4000:
                for k in list(_purge_seen)[:2000]:
                    _purge_seen.pop(k, None)
            t["deleted"] = int(t.get("deleted", 0) or 0) + (1 if ok else 0)
            t["last_at"] = datetime.now().isoformat(timespec="seconds")
            purge_log(uid, chat_id, "deleted" if ok else "delete failed (bot needs Delete messages right)")
            sync_later()
            return True
        _purge_seen[key] = False
        return False
    except Exception:
        logger.exception("purge check")
        return False


# ---------------------------------------------------------------------------
# 4. MEMBER HISTORY
# ---------------------------------------------------------------------------
def tracked_chat_ids():
    ids = {}
    for ch in S().get("channels", []) or []:
        cid = str(ch.get("chat_id") or "").strip()
        if cid and cid.lstrip("-").isdigit():
            ids[cid] = ch.get("name")
    for cid in list(S().get("groups", {}) or {}) + list(S().get("join_requests", {}) or {}):
        ids.setdefault(str(cid), None)
    return ids


def record_members(cid, n):
    hist = S().setdefault("member_history", {}).setdefault(str(cid), [])
    now = int(time.time())
    if not hist or hist[-1][1] != n or now - hist[-1][0] > 6 * 3600:
        hist.append([now, int(n)])
        del hist[:-240]
        sync_later()


def _history_loop():
    time.sleep(25)
    while True:
        try:
            for cid in tracked_chat_ids():
                try:
                    record_members(cid, bot.get_chat_member_count(cid))
                except Exception:
                    pass
                time.sleep(0.4)
        except Exception:
            logger.exception("member history")
        time.sleep(1800)


def growth(cid, hours=24):
    hist = (S().get("member_history", {}) or {}).get(str(cid)) or []
    if len(hist) < 2:
        return 0
    cutoff = time.time() - hours * 3600
    base = next((n for t, n in hist if t >= cutoff), hist[0][1])
    return hist[-1][1] - base


# ---------------------------------------------------------------------------
# 5. KEEP-ALIVE
# ---------------------------------------------------------------------------
def _keepalive_loop():
    url = (os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("KEEPALIVE_URL") or "").strip()
    if not url:
        logger.info("keep-alive: no RENDER_EXTERNAL_URL / KEEPALIVE_URL set (fine on paid / always-on hosting)")
        return
    url = url.rstrip("/") + "/health"
    logger.info("keep-alive: pinging %s every 4 minutes", url)
    while True:
        time.sleep(240)
        try:
            requests.get(url, timeout=20)
        except Exception:
            pass


# ---------------------------------------------------------------------------
def install(ctx):
    global C, bot, BOT_ID
    C = ctx
    bot = ctx["bot"]
    try:
        BOT_ID = bot.get_me().id
    except Exception:
        BOT_ID = None

    if hasattr(bot, "chat_join_request_handler"):
        bot.chat_join_request_handler()(on_join_request)
    else:
        logger.warning("This pyTelegramBotAPI version has no chat_join_request_handler; run: pip install -U pyTelegramBotAPI")

    service = ["new_chat_members", "left_chat_member", "new_chat_title", "new_chat_photo", "delete_chat_photo",
               "pinned_message", "video_chat_started", "video_chat_ended", "video_chat_participants_invited",
               "message_auto_delete_timer_changed"]
    bot.message_handler(func=lambda m: getattr(m.chat, "type", None) in ("group", "supergroup"), content_types=service)(on_service_message)

    for target, name in ((_jr_worker, "join-requests"), (_flusher, "features-flush"),
                         (_history_loop, "member-history"), (_keepalive_loop, "keep-alive")):
        threading.Thread(target=target, daemon=True, name=name).start()
    logger.info("features installed: join requests, group guard, purge, member history, keep-alive")
