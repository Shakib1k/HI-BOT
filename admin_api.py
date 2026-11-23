# -*- coding: utf-8 -*-
"""
admin_api.py  -  Web Admin Panel backend for the Telegram bot (main.py)

Usage (already wired in main.py):
    import admin_api
    admin_api.register(flask_app, globals())

Auth: POST /api/admin/login {password} -> bearer token (sent in Authorization header).
No cookies are used, so CSRF is not applicable. Login attempts are rate limited.
"""
import os
import io
import re
import json
import time
import uuid
import hmac
import secrets
import hashlib
import threading
import mimetypes
from datetime import datetime, timedelta, timezone

from flask import jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "admin_web_tokens.json")
TOKEN_TTL = 60 * 60 * 24 * 30          # 30 days
MAX_UPLOAD = 50 * 1024 * 1024          # Telegram bot API limit for uploads


def register(app, ctx):
    """Attach all admin routes to the Flask app. ctx = main.py globals()."""

    bot = ctx["bot"]
    ADMIN_ID = ctx["ADMIN_ID"]
    logger = ctx["logger"]
    lock = threading.RLock()

    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD + 1024 * 1024

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def G(name):
        return ctx[name]

    def sync():
        ctx["sync_db"]()

    def now_iso():
        return datetime.now().isoformat()

    def ok(**kw):
        d = {"ok": True}
        d.update(kw)
        return jsonify(d)

    def err(msg, code=400):
        return jsonify({"ok": False, "error": msg}), code

    # ---------------- token store ----------------
    tokens = {}

    def _load_tokens():
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            t = time.time()
            for h, exp in raw.items():
                if exp > t:
                    tokens[h] = exp
        except Exception:
            pass

    def _save_tokens():
        try:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump(tokens, f)
        except Exception:
            pass

    def _hash(tok):
        return hashlib.sha256(tok.encode()).hexdigest()

    _load_tokens()

    def issue_token():
        tok = secrets.token_urlsafe(32)
        with lock:
            tokens[_hash(tok)] = time.time() + TOKEN_TTL
            _save_tokens()
        return tok

    def valid_token(tok):
        if not tok:
            return False
        h = _hash(tok)
        with lock:
            exp = tokens.get(h)
            if not exp:
                return False
            if exp < time.time():
                tokens.pop(h, None)
                _save_tokens()
                return False
        return True

    # ---------------- rate limit ----------------
    fails = {}

    def client_ip():
        fwd = request.headers.get("X-Forwarded-For", "")
        return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "?"

    def locked_out(ip):
        rec = fails.get(ip)
        if not rec:
            return 0
        cnt, first = rec
        if time.time() - first > 600:
            fails.pop(ip, None)
            return 0
        if cnt >= 5:
            return int(600 - (time.time() - first))
        return 0

    def add_fail(ip):
        rec = fails.get(ip)
        if not rec or time.time() - rec[1] > 600:
            fails[ip] = [1, time.time()]
        else:
            rec[0] += 1

    # ---------------- decorator ----------------
    def auth(fn):
        def wrapper(*a, **kw):
            h = request.headers.get("Authorization", "")
            tok = h[7:].strip() if h.lower().startswith("bearer ") else ""
            if not valid_token(tok):
                return err("Unauthorized", 401)
            try:
                return fn(*a, **kw)
            except Exception as e:  # never leak a stack trace to the browser
                logger.exception("admin_api error in %s", fn.__name__)
                return err(f"Server error: {e}", 500)
        wrapper.__name__ = fn.__name__
        return wrapper

    def route(path, **kw):
        def deco(fn):
            app.add_url_rule(path, endpoint="adm_" + fn.__name__, view_func=auth(fn), **kw)
            return fn
        return deco

    def bot_username():
        try:
            return bot.get_me().username
        except Exception:
            return ""

    def deep_link(key):
        u = bot_username()
        return f"https://t.me/{u}?start={key}" if u else key

    # ------------------------------------------------------------------
    # static page
    # ------------------------------------------------------------------
    def serve_admin():
        resp = send_from_directory(HERE, "admin.html")
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    app.add_url_rule("/admin", endpoint="adm_page", view_func=serve_admin)
    app.add_url_rule("/admin.html", endpoint="adm_page2", view_func=serve_admin)

    # ------------------------------------------------------------------
    # login / logout
    # ------------------------------------------------------------------
    @app.route("/api/admin/login", methods=["POST"])
    def adm_login():
        pw_env = os.environ.get("ADMIN_PASSWORD", "").strip()
        if not pw_env:
            return err("ADMIN_PASSWORD environment variable is not set on the server.", 503)
        ip = client_ip()
        wait = locked_out(ip)
        if wait:
            return err(f"Too many attempts. Try again in {wait // 60 + 1} min.", 429)
        data = request.get_json(silent=True) or {}
        pw = str(data.get("password", ""))
        if hmac.compare_digest(pw.encode(), pw_env.encode()):
            fails.pop(ip, None)
            return ok(token=issue_token())
        add_fail(ip)
        time.sleep(0.6)
        return err("Wrong password", 403)

    @route("/api/admin/logout", methods=["POST"])
    def adm_logout():
        h = request.headers.get("Authorization", "")[7:].strip()
        with lock:
            tokens.pop(_hash(h), None)
            _save_tokens()
        return ok()

    @route("/api/admin/ping")
    def adm_ping():
        return ok()

    # ------------------------------------------------------------------
    # dashboard
    # ------------------------------------------------------------------
    @route("/api/admin/stats")
    def adm_stats():
        fd = G("file_database")
        bs = G("bot_settings")
        sp = G("scheduled_posts")
        sd = G("scheduled_deletions")
        ua = G("user_activity")
        db = G("db")
        gw = G("giveaway")
        keys = sum(1 for v in fd.values() if v.get("is_text_key"))
        return ok(
            bot_username=bot_username(),
            users=len(G("registered_users")),
            banned=len(G("banned_users")),
            files=len(fd) - keys,
            keys=keys,
            projects=len(db.get("project_files", [])),
            channels=len(bs.get("channels", [])),
            reaction_bots=len(bs.get("reaction_bots", [])),
            pending_posts=len([x for x in sp if not x.get("sent")]),
            pending_deletions=len([x for x in sd if not x.get("deleted")]),
            points=sum(u.get("points", 0) for u in ua.values()),
            auto_replies=len(bs.get("auto_reply", {})),
            giveaway_active=bool(gw.get("active")),
            toggles={
                "force_join": bool(bs.get("force_join", True)),
                "maintenance_mode": bool(bs.get("maintenance_mode", False)),
                "user_limit_enabled": bool(bs.get("user_limit_enabled", True)),
                "quote_captions": bool(bs.get("quote_captions", False)),
            },
            server_time=now_iso(),
        )

    @route("/api/admin/toggle", methods=["POST"])
    def adm_toggle():
        key = (request.get_json(silent=True) or {}).get("key")
        if key not in ("force_join", "maintenance_mode", "user_limit_enabled", "quote_captions"):
            return err("Unknown toggle")
        bs = G("bot_settings")
        default = key not in ("maintenance_mode", "quote_captions")
        bs[key] = not bs.get(key, default)
        sync()
        return ok(key=key, value=bs[key])

    # ------------------------------------------------------------------
    # file manager: files + secret keys
    # ------------------------------------------------------------------
    def classify(filename, mime):
        ext = os.path.splitext(filename.lower())[1]
        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            return "photo"
        if ext in (".mp4", ".mov", ".mkv", ".webm"):
            return "video"
        if ext in (".mp3", ".m4a", ".ogg", ".wav", ".flac"):
            return "audio"
        return "document"

    def upload_to_telegram(fs):
        """Send an uploaded browser file to the admin chat via the bot, return (file_id, type, name)."""
        raw = fs.read()
        if not raw:
            raise ValueError("Empty file")
        if len(raw) > MAX_UPLOAD:
            raise ValueError("File too large (Telegram bot limit is 50 MB)")
        name = os.path.basename(fs.filename or "file") or "file"
        kind = classify(name, fs.mimetype)

        def mk():
            b = io.BytesIO(raw)
            b.name = name
            return b

        def done(m, fid, k):
            # the upload only exists to obtain a Telegram file_id; remove it from the chat
            try:
                bot.delete_message(ADMIN_ID, m.message_id)
            except Exception:
                pass
            return fid, k, name

        if kind == "photo" and len(raw) <= 10 * 1024 * 1024:
            try:
                m = bot.send_photo(ADMIN_ID, mk(), disable_notification=True)
                return done(m, m.photo[-1].file_id, "photo")
            except Exception:
                kind = "document"
        if kind == "video":
            try:
                m = bot.send_video(ADMIN_ID, mk(), disable_notification=True)
                if m.video:
                    return done(m, m.video.file_id, "video")
            except Exception:
                kind = "document"
        if kind == "audio":
            try:
                m = bot.send_audio(ADMIN_ID, mk(), disable_notification=True)
                if m.audio:
                    return done(m, m.audio.file_id, "audio")
            except Exception:
                kind = "document"
        m = bot.send_document(ADMIN_ID, mk(), disable_notification=True)
        return done(m, m.document.file_id, "document")

    @route("/api/admin/files")
    def adm_files():
        fd = G("file_database")
        out = []
        for k, v in fd.items():
            item = {
                "id": k,
                "is_key": bool(v.get("is_text_key")),
                "uploaded_at": v.get("uploaded_at"),
                "link": deep_link(k),
            }
            if v.get("is_text_key"):
                item["panel_name"] = v.get("panel_name", "Unnamed")
                item["secret_value"] = v.get("secret_value", "")
            else:
                item["file_name"] = v.get("file_name", k)
                item["file_type"] = v.get("file_type", "document")
            out.append(item)
        out.sort(key=lambda x: x.get("uploaded_at") or "", reverse=True)
        return ok(items=out)

    @route("/api/admin/files/upload", methods=["POST"])
    def adm_files_upload():
        files = request.files.getlist("file")
        if not files:
            return err("No file selected")
        fd = G("file_database")
        done, failed = [], []
        for fs in files:
            try:
                fid, ftype, fname = upload_to_telegram(fs)
                key = "file_" + uuid.uuid4().hex[:8]
                fd[key] = {
                    "file_id": fid, "file_type": ftype, "file_name": fname,
                    "is_text_key": False, "uploaded_at": now_iso(),
                }
                done.append({"id": key, "name": fname, "link": deep_link(key)})
            except Exception as e:
                failed.append({"name": fs.filename, "error": str(e)})
        if done:
            sync()
        return ok(uploaded=done, failed=failed)

    @route("/api/admin/keys", methods=["POST"])
    def adm_key_create():
        d = request.get_json(silent=True) or {}
        panel = str(d.get("panel_name", "")).strip()
        val = str(d.get("secret_value", "")).strip()
        if not panel or not val:
            return err("Panel name and secret value are required")
        key = "key_" + uuid.uuid4().hex[:8]
        G("file_database")[key] = {
            "is_text_key": True, "panel_name": panel,
            "secret_value": val, "uploaded_at": now_iso(),
        }
        sync()
        return ok(id=key, link=deep_link(key))

    @route("/api/admin/files/<key>", methods=["DELETE"])
    def adm_file_delete(key):
        fd = G("file_database")
        if key not in fd:
            return err("Not found", 404)
        del fd[key]
        sync()
        return ok()

    @route("/api/admin/files/<key>/view")
    def adm_file_view(key):
        """Stream a stored file to the panel so it can be previewed in the browser."""
        import requests as _rq
        from flask import Response
        item = G("file_database").get(key)
        if not item or item.get("is_text_key"):
            return err("Not found", 404)
        try:
            info = bot.get_file(item.get("file_id"))
        except Exception as e:
            return err("Telegram cannot give this file to the panel: %s (files over 20 MB cannot be previewed)" % e)
        url = "https://api.telegram.org/file/bot%s/%s" % (bot.token, info.file_path)
        try:
            r = _rq.get(url, timeout=60)
        except Exception as e:
            return err(str(e))
        if r.status_code != 200:
            return err("Could not download the file")
        ctype = mimetypes.guess_type(info.file_path)[0] or "application/octet-stream"
        return Response(r.content, mimetype=ctype)

    @route("/api/admin/channels/check")
    def adm_channels_check():
        out = []
        try:
            me = bot.get_me().id
        except Exception:
            me = None
        for c in G("get_all_channels")():
            ident = (c.get("chat_id") or "").strip()
            st = "no chat id"
            members = None
            if ident and me:
                try:
                    m = bot.get_chat_member(ident, me)
                    st = "ok" if m.status in ("administrator", "creator") else "bot is not admin"
                    if st == "ok":
                        try:
                            members = bot.get_chat_member_count(ident)
                        except Exception:
                            members = None
                except Exception:
                    st = "cannot access"
            out.append({"id": c.get("id"), "name": c.get("name"), "status": st, "members": members})
        return ok(items=out)

    # ------------------------------------------------------------------
    # channel groups (named presets of channels, for quick bulk-selection)
    # ------------------------------------------------------------------
    @route("/api/admin/saved-channels/verify", methods=["POST"])
    def adm_saved_channels_verify():
        """Refresh every saved chat's Telegram title and bot-admin status."""
        out = []
        for ch in G("get_all_channels")():
            chat_id = ch.get("chat_id")
            if not chat_id:
                out.append({"id": ch.get("id"), "name": ch.get("name"), "chat_id": "", "status": "missing chat ID"})
                continue
            try:
                chat = bot.get_chat(chat_id)
                title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or ch.get("name") or str(chat_id)
                G("update_channel")(ch.get("id"), name=title, real_title=title)
                try:
                    me = bot.get_me().id
                    member = bot.get_chat_member(chat_id, me)
                    status = "bot is admin" if member.status in ("administrator", "creator") else "bot is not admin"
                except Exception:
                    status = "cannot verify bot rights"
                out.append({"id": ch.get("id"), "name": title, "chat_id": str(chat_id),
                            "username": getattr(chat, "username", None), "status": status,
                            "type": getattr(chat, "type", None)})
            except Exception as ex:
                out.append({"id": ch.get("id"), "name": ch.get("name"), "chat_id": str(chat_id),
                            "status": "cannot access: " + str(ex)[:180]})
        sync()
        return ok(items=out, verified=len([x for x in out if not x["status"].startswith("cannot")]))

    @route("/api/admin/channels/resolve", methods=["POST"])
    def adm_channel_resolve():
        """Looks up a channel's real title/description/photo from Telegram, so Add New Channel
        can show the actual name instead of whatever the admin types."""
        d = request.get_json(silent=True) or {}
        ident = str(d.get("chat_id", "")).strip()
        if not ident:
            return err("Give a chat ID or @username first")
        try:
            chat = bot.get_chat(ident)
        except Exception as e:
            return err("Could not look this up (is the bot an admin there?): " + str(e))
        members = None
        try:
            members = bot.get_chat_member_count(ident)
        except Exception:
            pass
        return ok(title=chat.title or ident, description=chat.description or "", members=members,
                  has_photo=bool(getattr(chat, "photo", None)))

    @route("/api/admin/channels/<cid>/info")
    def adm_channel_info(cid):
        chans = G("get_all_channels")()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch or not ch.get("chat_id"):
            return err("Channel has no chat ID saved", 404)
        try:
            chat = bot.get_chat(ch["chat_id"])
        except Exception as e:
            return err("Could not reach this channel: " + str(e))
        members = None
        try:
            members = bot.get_chat_member_count(ch["chat_id"])
        except Exception:
            pass
        return ok(title=chat.title, description=chat.description or "", members=members,
                  invite_link=getattr(chat, "invite_link", None))

    @route("/api/admin/channels/<cid>/title", methods=["POST"])
    def adm_channel_title(cid):
        chans = G("get_all_channels")()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch or not ch.get("chat_id"):
            return err("Channel has no chat ID saved", 404)
        title = str((request.get_json(silent=True) or {}).get("title", "")).strip()[:128]
        if not title:
            return err("Title can't be empty")
        try:
            bot.set_chat_title(ch["chat_id"], title)
        except Exception as e:
            return err("Telegram refused this (bot needs 'Change channel info' admin right): " + str(e))
        return ok()

    @route("/api/admin/channels/<cid>/description", methods=["POST"])
    def adm_channel_description(cid):
        chans = G("get_all_channels")()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch or not ch.get("chat_id"):
            return err("Channel has no chat ID saved", 404)
        desc = str((request.get_json(silent=True) or {}).get("description", ""))[:255]
        try:
            bot.set_chat_description(ch["chat_id"], desc)
        except Exception as e:
            return err("Telegram refused this (bot needs 'Change channel info' admin right): " + str(e))
        return ok()

    @route("/api/admin/channels/<cid>/pin", methods=["POST"])
    def adm_channel_pin(cid):
        chans = G("get_all_channels")()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch or not ch.get("chat_id"):
            return err("Channel has no chat ID saved", 404)
        mid = (request.get_json(silent=True) or {}).get("message_id")
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            return err("Give a valid message ID")
        try:
            bot.pin_chat_message(ch["chat_id"], mid)
        except Exception as e:
            return err("Could not pin (bot needs 'Pin messages' admin right): " + str(e))
        return ok()

    @route("/api/admin/channels/<cid>/unpin_all", methods=["POST"])
    def adm_channel_unpin_all(cid):
        chans = G("get_all_channels")()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch or not ch.get("chat_id"):
            return err("Channel has no chat ID saved", 404)
        try:
            bot.unpin_all_chat_messages(ch["chat_id"])
        except Exception as e:
            return err("Could not unpin: " + str(e))
        return ok()

    @route("/api/admin/channels/<cid>/delete_message", methods=["POST"])
    def adm_channel_delete_message(cid):
        chans = G("get_all_channels")()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch or not ch.get("chat_id"):
            return err("Channel has no chat ID saved", 404)
        mid = (request.get_json(silent=True) or {}).get("message_id")
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            return err("Give a valid message ID")
        try:
            bot.delete_message(ch["chat_id"], mid)
        except Exception as e:
            return err("Could not delete (within 48h and bot needs 'Delete messages' right): " + str(e))
        return ok()

    @route("/api/admin/channels/<cid>/test", methods=["POST"])
    def adm_channel_test(cid):
        chans = G("get_all_channels")()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch or not ch.get("chat_id"):
            return err("Channel has no chat ID saved", 404)
        try:
            bot.send_message(ch["chat_id"], "✅ Test message from the admin panel - the bot can post here.")
        except Exception as e:
            return err("Could not send: " + str(e))
        return ok()

    def _get_channel_or_404(cid):
        chans = G("get_all_channels")()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch or not ch.get("chat_id"):
            return None, err("Channel has no chat ID saved", 404)
        return ch, None

    @route("/api/admin/channels/<cid>/admins")
    def adm_channel_admins(cid):
        ch, e = _get_channel_or_404(cid)
        if e: return e
        try:
            admins = bot.get_chat_administrators(ch["chat_id"])
        except Exception as ex:
            return err("Could not fetch admins: " + str(ex))
        out = []
        for a in admins:
            u = a.user
            out.append({
                "id": u.id, "name": (u.first_name or "") + ((" " + u.last_name) if u.last_name else ""),
                "username": u.username, "is_bot": u.is_bot, "status": a.status,
                "custom_title": getattr(a, "custom_title", None),
                "is_owner": a.status == "creator",
            })
        return ok(items=out)

    # ------------------------------------------------------------------
    # Channel Admin Manager (admins created/managed by this bot)
    # ------------------------------------------------------------------
    # Telegram Bot API channel administrator rights.  Keep this list aligned
    # with promoteChatMember / ChatAdministratorRights.  In channels,
    # can_edit_messages also covers pinning; can_pin_messages is group-only.
    ADMIN_RIGHTS = (
        "is_anonymous",
        "can_change_info",
        "can_post_messages",
        "can_edit_messages",
        "can_delete_messages",
        "can_invite_users",
        "can_restrict_members",
        "can_promote_members",
        "can_manage_video_chats",
        "can_post_stories",
        "can_edit_stories",
        "can_delete_stories",
        "can_manage_direct_messages",
        "can_send_welcome_messages",
    )

    def _managed_admins():
        return G("bot_settings").setdefault("channel_managed_admins", {})

    @route("/api/admin/channel-admin/channels")
    def adm_channel_admin_channels():
        """Verify every saved channel and return those where this bot is admin."""
        out = []
        try:
            bot_id = bot.get_me().id
        except Exception as ex:
            return err("Cannot identify bot: " + str(ex), 503)
        for ch in G("get_all_channels")():
            chat_id = ch.get("chat_id")
            if not chat_id:
                continue
            try:
                member = bot.get_chat_member(chat_id, bot_id)
                if member.status not in ("administrator", "creator"):
                    continue
                chat = bot.get_chat(chat_id)
                out.append({"id": ch.get("id"), "chat_id": str(chat_id),
                            "name": chat.title or ch.get("name") or str(chat_id),
                            "username": getattr(chat, "username", None),
                            "bot_can_promote": bool(getattr(member, "can_promote_members", False))})
            except Exception:
                continue
        return ok(items=out, note="Only channels already saved in this panel can be checked. Telegram does not provide a bot API to enumerate every channel where the bot is admin.")

    @route("/api/admin/channel-admin/<cid>/admins")
    def adm_channel_managed_list(cid):
        ch, e = _get_channel_or_404(cid)
        if e: return e
        try:
            admins = bot.get_chat_administrators(ch["chat_id"])
        except Exception as ex:
            return err("Could not fetch channel admins: " + str(ex))
        saved = _managed_admins().get(str(ch["chat_id"]), {})
        items = []
        for a in admins:
            u = a.user
            rec = saved.get(str(u.id))
            items.append({"id": u.id, "name": ((u.first_name or "") + (" " + u.last_name if u.last_name else "")),
                          "username": u.username, "status": a.status, "is_owner": a.status == "creator",
                          "managed_by_bot": bool(rec), "policy": rec or None})
        return ok(items=items, supported_permissions=list(ADMIN_RIGHTS))

    @route("/api/admin/channel-admin/<cid>/admins", methods=["POST"])
    def adm_channel_managed_add(cid):
        ch, e = _get_channel_or_404(cid)
        if e: return e
        d = request.get_json(silent=True) or {}
        try: uid = int(d.get("user_id"))
        except Exception: return err("Enter a valid numeric Telegram User ID")
        if uid <= 0: return err("Invalid Telegram User ID")
        rights_in = d.get("permissions") or {}
        rights = {k: bool(rights_in.get(k, False)) for k in ADMIN_RIGHTS}
        # Telegram requires at least one administrator right.
        if not any(rights.values()): return err("Select at least one administrator permission")
        try:
            bot_member = bot.get_chat_member(ch["chat_id"], bot.get_me().id)
            if not getattr(bot_member, "can_promote_members", False):
                return err("The bot needs 'Add New Admins / Promote Members' permission in this channel")
            bot.promote_chat_member(ch["chat_id"], uid, **rights)
        except Exception as ex:
            return err("Telegram could not promote this user. Check bot rights and the User ID: " + str(ex))
        policy = {
            "permissions": rights,
            "limits": {k: max(0, min(int((d.get("limits") or {}).get(k, 0) or 0), 1000000)) for k in ("files", "photos", "videos", "voice", "text")},
            "cooldown_hours": max(0, min(float(d.get("cooldown_hours", 0) or 0), 8760)),
            "auto_delete_links": bool(d.get("auto_delete_links", False)),
            "created_at": now_iso(), "created_by": "admin_panel"
        }
        _managed_admins().setdefault(str(ch["chat_id"]), {})[str(uid)] = policy
        sync()
        try:
            bot.send_message(uid, "✅ You have been added as a channel administrator. Your channel permissions and usage policy are set by the channel owner.")
        except Exception:
            pass
        try:
            bot.send_message(ADMIN_ID, f"✅ Channel admin added\nChannel: {ch.get('name', ch['chat_id'])}\nUser ID: {uid}")
        except Exception:
            pass
        return ok(user_id=uid, policy=policy)

    @route("/api/admin/channel-admin/<cid>/admins/<int:uid>", methods=["PATCH"])
    def adm_channel_managed_edit(cid, uid):
        ch, e = _get_channel_or_404(cid)
        if e: return e
        bucket = _managed_admins().get(str(ch["chat_id"]), {})
        if str(uid) not in bucket: return err("This admin was not added/registered by this bot", 404)
        d = request.get_json(silent=True) or {}
        old = bucket[str(uid)]
        if "permissions" in d:
            rights_in = d.get("permissions") or {}
            rights = {k: bool(rights_in.get(k, False)) for k in ADMIN_RIGHTS}
            try:
                bot.promote_chat_member(ch["chat_id"], uid, **rights)
            except Exception as ex: return err("Could not update Telegram permissions: " + str(ex))
            old["permissions"] = rights
        if "limits" in d:
            old["limits"] = {k: max(0, min(int((d["limits"] or {}).get(k, 0) or 0), 1000000)) for k in ("files", "photos", "videos", "voice", "text")}
        if "cooldown_hours" in d: old["cooldown_hours"] = max(0, min(float(d["cooldown_hours"] or 0), 8760))
        if "auto_delete_links" in d: old["auto_delete_links"] = bool(d["auto_delete_links"])
        old["updated_at"] = now_iso(); sync()
        return ok(policy=old)

    @route("/api/admin/channel-admin/<cid>/admins/<int:uid>", methods=["DELETE"])
    def adm_channel_managed_delete(cid, uid):
        ch, e = _get_channel_or_404(cid)
        if e: return e
        bucket = _managed_admins().get(str(ch["chat_id"]), {})
        if str(uid) not in bucket: return err("Only admins registered by this bot can be removed here", 404)
        try:
            # Demote by explicitly clearing every channel-admin right supported
            # by this manager, including newer Story / DM / Welcome permissions.
            bot.promote_chat_member(ch["chat_id"], uid, **{k: False for k in ADMIN_RIGHTS})
        except Exception as ex:
            return err("Could not remove admin rights: " + str(ex))
        bucket.pop(str(uid), None)
        if not bucket: _managed_admins().pop(str(ch["chat_id"]), None)
        sync()
        try: bot.send_message(uid, "ℹ️ Your channel administrator permissions have been removed by the channel owner.")
        except Exception: pass
        try: bot.send_message(ADMIN_ID, f"🗑️ Managed channel admin removed\nChannel: {ch.get('name', ch['chat_id'])}\nUser ID: {uid}")
        except Exception: pass
        return ok()

    @route("/api/admin/channels/<cid>/invite_link", methods=["GET"])
    def adm_channel_invite_get(cid):
        ch, e = _get_channel_or_404(cid)
        if e: return e
        try:
            chat = bot.get_chat(ch["chat_id"])
        except Exception as ex:
            return err("Could not reach this channel: " + str(ex))
        return ok(invite_link=getattr(chat, "invite_link", None))

    @route("/api/admin/channels/<cid>/invite_link", methods=["POST"])
    def adm_channel_invite_create(cid):
        """Creates a brand-new invite link (does not touch the channel's primary link)."""
        ch, e = _get_channel_or_404(cid)
        if e: return e
        d = request.get_json(silent=True) or {}
        name = str(d.get("name", "") or "")[:32] or None
        try:
            link = bot.create_chat_invite_link(ch["chat_id"], name=name)
        except Exception as ex:
            return err("Could not create invite link (bot needs 'Invite users' admin right): " + str(ex))
        return ok(invite_link=link.invite_link)

    @route("/api/admin/channels/<cid>/invite_link/revoke", methods=["POST"])
    def adm_channel_invite_revoke(cid):
        ch, e = _get_channel_or_404(cid)
        if e: return e
        link = (request.get_json(silent=True) or {}).get("link", "")
        if not link:
            return err("Give the invite link to revoke")
        try:
            bot.revoke_chat_invite_link(ch["chat_id"], link)
        except Exception as ex:
            return err("Could not revoke: " + str(ex))
        return ok()

    @route("/api/admin/channels/<cid>/photo", methods=["POST"])
    def adm_channel_photo(cid):
        ch, e = _get_channel_or_404(cid)
        if e: return e
        fs = request.files.get("file")
        if not fs:
            return err("No image selected")
        try:
            bot.set_chat_photo(ch["chat_id"], fs)
        except Exception as ex:
            return err("Could not set photo (bot needs 'Change channel info' admin right): " + str(ex))
        return ok()

    @route("/api/admin/channels/<cid>/member", methods=["POST"])
    def adm_channel_member_action(cid):
        """Ban or unban a specific Telegram user ID from this channel."""
        ch, e = _get_channel_or_404(cid)
        if e: return e
        d = request.get_json(silent=True) or {}
        action = d.get("action")
        try:
            uid = int(d.get("user_id"))
        except (TypeError, ValueError):
            return err("Give a valid numeric Telegram user ID")
        try:
            if action == "ban":
                bot.ban_chat_member(ch["chat_id"], uid)
            elif action == "unban":
                bot.unban_chat_member(ch["chat_id"], uid, only_if_banned=True)
            else:
                return err("action must be 'ban' or 'unban'")
        except Exception as ex:
            return err("Telegram refused this (bot needs 'Ban users' admin right): " + str(ex))
        return ok()

    @route("/api/admin/channel-groups")
    def adm_ch_groups():
        return ok(items=G("bot_settings").setdefault("channel_groups", []))

    @route("/api/admin/channel-groups", methods=["POST"])
    def adm_ch_groups_create():
        d = request.get_json(silent=True) or {}
        name = str(d.get("name", "")).strip()[:60]
        ids = [str(x).strip() for x in (d.get("channel_ids") or []) if str(x).strip()]
        if not name or not ids:
            return err("Name and at least one channel are required")
        with lock:
            grp = {"id": "grp_" + uuid.uuid4().hex[:8], "name": name, "channel_ids": ids}
            G("bot_settings").setdefault("channel_groups", []).append(grp)
            sync()
        return ok(item=grp)

    @route("/api/admin/channel-groups/<gid>", methods=["DELETE"])
    def adm_ch_groups_delete(gid):
        with lock:
            gs = G("bot_settings").setdefault("channel_groups", [])
            before = len(gs)
            gs[:] = [g for g in gs if g.get("id") != gid]
            sync()
        return ok() if len(gs) < before else err("Not found", 404)

    # ------------------------------------------------------------------
    # dashboard analytics
    # ------------------------------------------------------------------
    @route("/api/admin/analytics")
    def adm_analytics():
        fd = G("file_database")
        top = sorted(
            ({"name": v.get("file_name") or v.get("panel_name") or k, "downloads": v.get("downloads", 0)}
             for k, v in fd.items() if not v.get("is_text_key")),
            key=lambda x: x["downloads"], reverse=True
        )[:10]
        top = [t for t in top if t["downloads"] > 0]
        sl = G("signup_log") or {}
        signups = dict(sorted(sl.items())[-14:])
        return ok(top_files=top, signups_by_day=signups,
                   signups_note=None if sl else "Signup history starts counting from today - nothing recorded yet.")

    @route("/api/admin/files/<key>/send", methods=["POST"])
    def adm_file_send(key):
        """Deliver a stored file to the admin's Telegram chat (preview/download)."""
        item = G("file_database").get(key)
        if not item or item.get("is_text_key"):
            return err("Not found", 404)
        send_stored(ADMIN_ID, item.get("file_id"), item.get("file_type", "document"))
        return ok()

    def send_stored(chat, fid, kind, caption=None):
        names = {
            "document": "send_document", "photo": "send_photo", "video": "send_video",
            "audio": "send_audio", "voice": "send_voice", "animation": "send_animation",
            "sticker": "send_sticker", "video_note": "send_video_note",
        }
        methods = {k: getattr(bot, v) for k, v in names.items() if hasattr(bot, v)}
        m = methods.get(kind, bot.send_document)
        if kind in ("sticker", "video_note") or not caption:
            return m(chat, fid)
        return m(chat, fid, caption=caption)

    # ------------------------------------------------------------------
    # project files
    # ------------------------------------------------------------------
    @route("/api/admin/projects")
    def adm_projects():
        return ok(items=G("db").get("project_files", []))

    @route("/api/admin/projects", methods=["POST"])
    def adm_project_add():
        name = (request.form.get("name") or "").strip() or "Unnamed project"
        fs = request.files.get("file")
        if not fs:
            return err("No file selected")
        fid, ftype, fname = upload_to_telegram(fs)
        item = {
            "id": uuid.uuid4().hex[:10], "name": name, "file_id": fid,
            "file_type": ftype, "file_name": fname, "uploaded_at": now_iso(),
        }
        G("db").setdefault("project_files", []).append(item)
        sync()
        return ok(item=item)

    @route("/api/admin/projects/<pid>", methods=["DELETE"])
    def adm_project_del(pid):
        db = G("db")
        before = len(db.setdefault("project_files", []))
        db["project_files"] = [x for x in db["project_files"] if str(x.get("id")) != pid]
        if len(db["project_files"]) == before:
            return err("Not found", 404)
        sync()
        return ok()

    @route("/api/admin/projects/<pid>/send", methods=["POST"])
    def adm_project_send(pid):
        item = next((x for x in G("db").get("project_files", []) if str(x.get("id")) == pid), None)
        if not item:
            return err("Not found", 404)
        cap = f"📁 {item.get('name', 'Project')}\n📎 {item.get('file_name', 'file')}"
        send_stored(ADMIN_ID, item.get("file_id"), item.get("file_type", "document"), cap)
        return ok()

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    name_cache = {}

    @route("/api/admin/users")
    def adm_users():
        q = (request.args.get("q") or "").strip()
        page = max(1, int(request.args.get("page", 1)))
        per = 25
        banned = set(G("banned_users"))
        ua = G("user_activity")
        ids = list(G("registered_users"))
        rows = []
        for uid in ids:
            act = ua.get(uid) or ua.get(str(uid)) or {}
            rows.append({
                "id": uid,
                "banned": uid in banned,
                "points": act.get("points", 0),
                "actions": act.get("total_actions", 0),
                "last_active": act.get("last_active"),
                "rank": act.get("rank", "Member"),
                "name": name_cache.get(uid, ""),
            })
        if q:
            ql = q.lower()
            rows = [r for r in rows if ql in str(r["id"]) or ql in r["name"].lower()]
        rows.sort(key=lambda r: r["last_active"] or "", reverse=True)
        total = len(rows)
        rows = rows[(page - 1) * per: page * per]
        return ok(items=rows, total=total, page=page, per=per, banned_total=len(banned))

    @route("/api/admin/users/names", methods=["POST"])
    def adm_user_names():
        ids = (request.get_json(silent=True) or {}).get("ids", [])[:25]
        out = {}
        for uid in ids:
            try:
                uid = int(uid)
            except Exception:
                continue
            if uid not in name_cache:
                try:
                    c = bot.get_chat(uid)
                    nm = (c.first_name or "") + (" " + c.last_name if c.last_name else "")
                    if getattr(c, "username", None):
                        nm += f" (@{c.username})"
                    name_cache[uid] = nm.strip() or str(uid)
                except Exception:
                    name_cache[uid] = "—"
            out[str(uid)] = name_cache[uid]
        return ok(names=out)

    def _uid():
        try:
            return int((request.get_json(silent=True) or {}).get("user_id"))
        except Exception:
            return None

    @route("/api/admin/users/ban", methods=["POST"])
    def adm_ban():
        uid = _uid()
        if uid is None:
            return err("Invalid user ID")
        if uid == ADMIN_ID:
            return err("You cannot ban the main admin")
        G("banned_users").add(uid)
        sync()
        return ok()

    @route("/api/admin/users/unban", methods=["POST"])
    def adm_unban():
        uid = _uid()
        if uid is None:
            return err("Invalid user ID")
        G("banned_users").discard(uid)
        sync()
        return ok()

    @route("/api/admin/users/message", methods=["POST"])
    def adm_user_message():
        d = request.get_json(silent=True) or {}
        uid = _uid()
        text = str(d.get("text", "")).strip()
        if uid is None or not text:
            return err("User ID and text are required")
        try:
            bot.send_message(uid, text)
        except Exception as e:
            return err(f"Telegram error: {e}")
        return ok()

    # ------------------------------------------------------------------
    # broadcast (background job with progress)
    # ------------------------------------------------------------------
    jobs = {}

    @route("/api/admin/broadcast", methods=["POST"])
    def adm_broadcast():
        text = request.form.get("text", "").strip()
        mode = request.form.get("parse_mode", "")
        mode = mode if mode in ("Markdown", "HTML") else None
        fs = request.files.get("file")
        if not text and not fs:
            return err("Write a message or attach a file")
        media = None
        if fs:
            fid, ftype, fname = upload_to_telegram(fs)
            media = (fid, ftype)
        targets = [u for u in list(G("registered_users")) if u != ADMIN_ID]
        job_id = uuid.uuid4().hex[:8]
        jobs[job_id] = {"total": len(targets), "sent": 0, "failed": 0, "done": False}

        def run():
            j = jobs[job_id]
            for u in targets:
                try:
                    if media:
                        send_stored(u, media[0], media[1], text or None)
                    else:
                        bot.send_message(u, f"📢 Broadcast:\n\n{text}" if not mode else text, parse_mode=mode)
                    j["sent"] += 1
                except Exception:
                    j["failed"] += 1
                time.sleep(0.05)
            j["done"] = True

        threading.Thread(target=run, daemon=True).start()
        return ok(job_id=job_id, total=len(targets))

    @route("/api/admin/broadcast/<job_id>")
    def adm_broadcast_status(job_id):
        j = jobs.get(job_id)
        if not j:
            return err("Unknown job", 404)
        return ok(**j)

    # ------------------------------------------------------------------
    # auto reply + custom commands
    # ------------------------------------------------------------------
    @route("/api/admin/autoreply")
    def adm_ar_list():
        ar = G("bot_settings").get("auto_reply", {})
        return ok(items=[{"keyword": k, "response": v} for k, v in ar.items()])

    @route("/api/admin/autoreply", methods=["POST"])
    def adm_ar_add():
        d = request.get_json(silent=True) or {}
        k = str(d.get("keyword", "")).strip()
        r = str(d.get("response", "")).strip()
        if not k or not r:
            return err("Keyword and response are required")
        G("bot_settings").setdefault("auto_reply", {})[k] = r
        sync()
        return ok()

    @route("/api/admin/autoreply", methods=["DELETE"])
    def adm_ar_del():
        k = (request.get_json(silent=True) or {}).get("keyword")
        ar = G("bot_settings").get("auto_reply", {})
        if k not in ar:
            return err("Not found", 404)
        del ar[k]
        sync()
        return ok()

    @route("/api/admin/commands")
    def adm_cmd_list():
        c = G("bot_settings").get("custom_commands", {})
        return ok(items=[{"command": k, "response": v} for k, v in c.items()])

    @route("/api/admin/commands", methods=["POST"])
    def adm_cmd_add():
        d = request.get_json(silent=True) or {}
        k = str(d.get("command", "")).strip().lstrip("/").lower()
        r = str(d.get("response", "")).strip()
        if not re.fullmatch(r"[a-z0-9_]{1,32}", k or ""):
            return err("Command: only a-z, 0-9, _ (max 32)")
        if not r:
            return err("Response is required")
        G("bot_settings").setdefault("custom_commands", {})[k] = r
        sync()
        return ok()

    @route("/api/admin/commands", methods=["DELETE"])
    def adm_cmd_del():
        k = (request.get_json(silent=True) or {}).get("command")
        c = G("bot_settings").get("custom_commands", {})
        if k not in c:
            return err("Not found", 404)
        del c[k]
        sync()
        return ok()

    # ------------------------------------------------------------------
    # channels (force join)
    # ------------------------------------------------------------------
    @route("/api/admin/channels")
    def adm_channels():
        return ok(items=G("get_all_channels")())

    @route("/api/admin/channels", methods=["POST"])
    def adm_channel_add():
        d = request.get_json(silent=True) or {}
        name = str(d.get("name", "")).strip()
        typ = d.get("type", "public")
        if typ not in ("public", "private"):
            typ = "public"
        join_url = str(d.get("join_url", "")).strip()
        chat_id = str(d.get("chat_id", "")).strip()
        if not name:
            return err("Channel name is required")
        if not join_url and not chat_id:
            return err("Provide a join link or a chat ID / @username")
        if chat_id and not chat_id.startswith("@") and not re.fullmatch(r"-?\d+", chat_id):
            chat_id = "@" + chat_id
        if typ == "public" and not join_url and chat_id.startswith("@"):
            join_url = "https://t.me/" + chat_id[1:]
        # Prevent duplicate entries in the shared saved-channel registry.
        existing = next((c for c in G("get_all_channels")()
                         if str(c.get("chat_id", "")).strip().lower() == chat_id.lower()), None)
        if existing:
            return err("This channel is already saved as: " + str(existing.get("name") or existing.get("chat_id")), 409)
        # verify the bot can see the chat (needed for membership checks), and pull its real title
        warn = None
        real_title = ""
        if chat_id:
            try:
                info = bot.get_chat(chat_id)
                real_title = getattr(info, "title", "") or ""
            except Exception as e:
                warn = f"Bot cannot access this chat yet ({e}). Add the bot as admin."
        btn_name = str(d.get("join_btn_name", ""))[:40].strip()
        ch = G("add_channel")(name, typ, join_url, chat_id, btn_name, real_title)
        return ok(item=ch, warning=warn)

    @route("/api/admin/channels/<cid>", methods=["DELETE"])
    def adm_channel_del(cid):
        G("remove_channel_by_id")(cid)
        return ok()

    @route("/api/admin/channels/<cid>", methods=["PATCH"])
    def adm_channel_edit(cid):
        d = request.get_json(silent=True) or {}
        fields = {}
        if "name" in d:
            fields["name"] = str(d["name"]).strip()[:80]
        if "join_btn_name" in d:
            fields["join_btn_name"] = str(d["join_btn_name"]).strip()[:40]
        ch = G("update_channel")(cid, **fields)
        return ok(item=ch) if ch else err("Not found", 404)

    @route("/api/admin/channels/<cid>/refresh", methods=["POST"])
    def adm_channel_refresh(cid):
        """Re-fetches the channel's real Telegram title and member count."""
        chans = G("get_all_channels")()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch or not ch.get("chat_id"):
            return err("Channel has no chat ID saved", 404)
        try:
            info = bot.get_chat(ch["chat_id"])
            members = None
            try:
                members = bot.get_chat_member_count(ch["chat_id"])
            except Exception:
                pass
            updated = G("update_channel")(cid, real_title=getattr(info, "title", "") or "")
            return ok(item=updated, members=members)
        except Exception as e:
            return err("Could not refresh: " + str(e))

    @route("/api/admin/channels/<cid>/leave", methods=["POST"])
    def adm_channel_leave(cid):
        """Removes the bot from the Telegram channel and deletes it from the saved list."""
        chans = G("get_all_channels")()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch:
            return err("Not found", 404)
        if ch.get("chat_id"):
            try:
                bot.leave_chat(ch["chat_id"])
            except Exception as e:
                return err("Could not leave the channel: " + str(e))
        G("remove_channel_by_id")(cid)
        return ok()

    @route("/api/admin/channels/<cid>/message", methods=["POST"])
    def adm_channel_message_action(cid):
        """Pin, unpin, or delete a specific message in the channel by its message ID -
        for direct, immediate control instead of only scheduled actions."""
        chans = G("get_all_channels")()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch or not ch.get("chat_id"):
            return err("Channel has no chat ID saved", 404)
        d = request.get_json(silent=True) or {}
        action = d.get("action")
        try:
            mid = int(d.get("message_id"))
        except (TypeError, ValueError):
            return err("Give a valid message ID")
        try:
            if action == "pin":
                bot.pin_chat_message(ch["chat_id"], mid, disable_notification=bool(d.get("silent", True)))
            elif action == "unpin":
                bot.unpin_chat_message(ch["chat_id"], mid)
            elif action == "delete":
                bot.delete_message(ch["chat_id"], mid)
            else:
                return err("Unknown action")
        except Exception as e:
            return err(str(e))
        return ok()

    # ------------------------------------------------------------------
    # message editors (welcome + join text)
    # ------------------------------------------------------------------
    @route("/api/admin/texts")
    def adm_texts():
        bs = G("bot_settings")
        return ok(
            welcome=bs.get("welcome_message", ""),
            join_title=bs.get("force_join_title", ""),
            join_message=bs.get("force_join_message", ""),
        )

    @route("/api/admin/texts", methods=["POST"])
    def adm_texts_save():
        d = request.get_json(silent=True) or {}
        bs = G("bot_settings")
        if "welcome" in d:
            v = str(d["welcome"]).strip()
            if not v:
                return err("Welcome message cannot be empty")
            bs["welcome_message"] = v
        if "join_title" in d:
            v = str(d["join_title"]).strip()
            if v:
                bs["force_join_title"] = v
        if "join_message" in d:
            v = str(d["join_message"]).strip()
            if v:
                bs["force_join_message"] = v
        sync()
        return ok()

    @route("/api/admin/texts/reset", methods=["POST"])
    def adm_texts_reset():
        bs = G("bot_settings")
        bn = G("BOT_NAME")
        bs["welcome_message"] = f"✨ Welcome to {bn} Premium Bot! ✨\n\n👋 Hello {{name}}!"
        bs["force_join_title"] = "🔒 ACCESS RESTRICTED"
        bs["force_join_message"] = "📌 Join {count} channel(s) to continue"
        sync()
        return ok()

    # ------------------------------------------------------------------
    # giveaway
    # ------------------------------------------------------------------
    @route("/api/admin/giveaway")
    def adm_gw():
        g = G("giveaway")
        return ok(
            active=bool(g.get("active")), prize=g.get("prize", ""),
            participants=len(g.get("participants", [])),
            end_time=g.get("end_time"), winners=g.get("winners", [])[-20:][::-1],
        )

    @route("/api/admin/giveaway/start", methods=["POST"])
    def adm_gw_start():
        prize = str((request.get_json(silent=True) or {}).get("prize", "")).strip()
        hours = int((request.get_json(silent=True) or {}).get("hours", 24) or 24)
        if not prize:
            return err("Prize is required")
        g = G("giveaway")
        g["prize"] = prize
        g["active"] = True
        g["participants"] = []
        g["end_time"] = (datetime.now() + timedelta(hours=max(1, min(hours, 720)))).isoformat()
        sync()
        return ok()

    @route("/api/admin/giveaway/end", methods=["POST"])
    def adm_gw_end():
        import random
        g = G("giveaway")
        if not g.get("active"):
            return err("No active giveaway")
        p = g.get("participants", [])
        winner = None
        if p:
            winner = random.choice(p)
            g.setdefault("winners", []).append(
                {"user_id": winner, "prize": g.get("prize", "Prize"), "time": now_iso()})
            try:
                bot.send_message(ADMIN_ID, f"🎉 Giveaway winner: {winner} - {g.get('prize')}")
            except Exception:
                pass
        g["active"] = False
        g["participants"] = []
        g["end_time"] = None
        sync()
        return ok(winner=winner)

    # ------------------------------------------------------------------
    # sub admins
    # ------------------------------------------------------------------
    @route("/api/admin/admins")
    def adm_admins():
        return ok(main=ADMIN_ID, items=G("bot_settings").get("sub_admins", []))

    @route("/api/admin/admins", methods=["POST"])
    def adm_admin_add():
        try:
            aid = int((request.get_json(silent=True) or {}).get("user_id"))
        except Exception:
            return err("Invalid user ID")
        subs = G("bot_settings").setdefault("sub_admins", [])
        if aid == ADMIN_ID or aid in subs:
            return err("Already an admin")
        subs.append(aid)
        sync()
        return ok()

    @route("/api/admin/admins", methods=["DELETE"])
    def adm_admin_del():
        try:
            aid = int((request.get_json(silent=True) or {}).get("user_id"))
        except Exception:
            return err("Invalid user ID")
        subs = G("bot_settings").setdefault("sub_admins", [])
        if aid not in subs:
            return err("Not found", 404)
        subs.remove(aid)
        sync()
        return ok()

    # ------------------------------------------------------------------
    # connect channel (auto copy)
    # ------------------------------------------------------------------
    @route("/api/admin/connect")
    def adm_conn():
        bs = G("bot_settings")
        return ok(
            dest=bs.get("conn_dest", ""),
            sources=bs.get("conn_sources", []),
            settings=bs.get("conn_settings", {}),
        )

    @route("/api/admin/connect", methods=["POST"])
    def adm_conn_save():
        d = request.get_json(silent=True) or {}
        bs = G("bot_settings")
        if "dest" in d:
            bs["conn_dest"] = str(d["dest"]).strip()
        if "sources" in d:
            src = [str(s).strip() for s in d["sources"] if str(s).strip()]
            bs["conn_sources"] = src
        if "settings" in d and isinstance(d["settings"], dict):
            cs = bs.setdefault("conn_settings", {})
            for k in ("copy_text", "copy_files", "copy_links"):
                if k in d["settings"]:
                    cs[k] = bool(d["settings"][k])
        sync()
        return ok()

    @route("/api/admin/connect", methods=["DELETE"])
    def adm_conn_del():
        bs = G("bot_settings")
        bs["conn_dest"] = ""
        bs["conn_sources"] = []
        sync()
        return ok()

    # ------------------------------------------------------------------
    # scheduled posts / deletions / reaction bots (view + cancel)
    # ------------------------------------------------------------------
    @route("/api/admin/scheduled")
    def adm_sched():
        posts = [x for x in G("scheduled_posts") if not x.get("sent")]
        dels = [x for x in G("scheduled_deletions") if not x.get("deleted")]

        def utc(v):
            try:
                return datetime.fromisoformat(v).astimezone(timezone.utc).isoformat()
            except Exception:
                return None
        posts = [dict(p, when_utc=utc(p.get("scheduled_time"))) for p in posts]
        dels = [dict(x, when_utc=utc(x.get("delete_at"))) for x in dels]
        return ok(posts=posts, deletions=dels)

    @route("/api/admin/scheduled/post/<pid>", methods=["DELETE"])
    def adm_sched_post_del(pid):
        sp = G("scheduled_posts")
        for i, x in enumerate(sp):
            if x.get("id") == pid:
                sp.pop(i)
                sync()
                return ok()
        return err("Not found", 404)

    @route("/api/admin/scheduled/deletion/<did>", methods=["DELETE"])
    def adm_sched_del_del(did):
        sd = G("scheduled_deletions")
        for i, x in enumerate(sd):
            if x.get("id") == did:
                sd.pop(i)
                sync()
                return ok()
        return err("Not found", 404)

    @route("/api/admin/reactionbots")
    def adm_rb():
        rb = G("bot_settings").get("reaction_bots", [])
        return ok(items=[{
            "index": i, "bot_id": b.get("bot_id"), "channel_title": b.get("channel_title"),
            "channel_id": b.get("channel_id"), "emoji": b.get("emoji"),
            "token_hint": "…" + str(b.get("token", ""))[-5:],
        } for i, b in enumerate(rb)])

    @route("/api/admin/reactionbots/<int:i>", methods=["DELETE"])
    def adm_rb_del(i):
        rb = G("bot_settings").get("reaction_bots", [])
        if i < 0 or i >= len(rb):
            return err("Not found", 404)
        rb.pop(i)
        sync()
        return ok()

    # ------------------------------------------------------------------
    # create: scheduled posts / deletions / reaction bots / userbot login
    # ------------------------------------------------------------------
    def parse_when(v):
        """Browser sends an ISO string with offset (UTC). Returns naive server-local datetime."""
        if not v:
            return datetime.now()
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Invalid date/time")
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt

    def norm_chat(v):
        v = str(v).strip()
        if re.fullmatch(r"-?\d+", v):
            return int(v)
        m = re.match(r"https?://t\.me/c/(\d+)", v)
        if m:
            return int("-100" + m.group(1))
        if v.startswith("@"):
            return v
        return "@" + v if v else v

    def clean_buttons(bl):
        out = []
        for b in (bl or [])[:10]:
            n = str((b or {}).get("name", "")).strip()[:60]
            u = str((b or {}).get("url", "")).strip()
            if n and re.match(r"^(https?://|tg://)", u):
                out.append({"name": n, "url": u})
        return out

    @route("/api/admin/scheduled/post", methods=["POST"])
    def adm_sched_post_create():
        try:
            meta = json.loads(request.form.get("meta", "{}"))
        except ValueError:
            return err("Bad request")
        chans = []
        for c in meta.get("channels", []):
            cid = norm_chat(c.get("id", "") if isinstance(c, dict) else c)
            if cid == "" or cid is None:
                continue
            title = (c.get("title") if isinstance(c, dict) else None) or str(cid)
            chans.append({"id": cid, "title": title})
        if not chans:
            return err("Select at least one channel")
        try:
            when = parse_when(meta.get("when"))
        except ValueError as e:
            return err(str(e))
        items = meta.get("items", [])
        quote = meta.get("quote")
        if quote is None:
            quote = bool(G("bot_settings").get("quote_captions", False))

        def q(t):
            t = str(t or "")
            return "<blockquote>%s</blockquote>" % t if (quote and t.strip()) else t
        uploads = request.files.getlist("files")
        files = []
        ptype = "file"
        try:
            for i, fs in enumerate(uploads):
                it = items[i] if i < len(items) else {}
                fid, ftype, fname = upload_to_telegram(fs)
                files.append({"file_id": fid, "file_type": ftype, "file_name": fname,
                              "caption": q(str(it.get("caption", ""))[:1024]),
                              "buttons": clean_buttons(it.get("buttons")),
                              "voice_data": None, "voice_buttons": []})
                ptype = ftype if ftype in ("photo", "video") else "file"
        except Exception as e:
            return err("Upload failed: " + str(e))
        text = str(meta.get("text", "")).strip()
        if not files:
            if not text:
                return err("Add a file or some text")
            files.append({"file_id": 0, "file_type": "text", "file_name": "Text post",
                          "caption": q(text[:4096]), "buttons": clean_buttons(meta.get("buttons")),
                          "voice_data": None, "voice_buttons": []})
        item = {"id": "post_" + uuid.uuid4().hex[:8], "post_type": ptype, "files": files,
                "channel_ids": chans, "scheduled_time": when.isoformat(), "sent": False,
                "created_at": now_iso()}
        with lock:
            G("scheduled_posts").append(item)
            sync()
        return ok(id=item["id"], files=len(files), channels=len(chans))

    @route("/api/admin/scheduled/deletion", methods=["POST"])
    def adm_sched_del_create():
        d = request.get_json(silent=True) or {}
        link = str(d.get("link", "")).strip()
        cid, mid = d.get("channel_id"), d.get("message_id")
        m = re.match(r"https?://t\.me/c/(\d+)/(\d+)", link)
        m2 = re.match(r"https?://t\.me/([A-Za-z0-9_]+)/(\d+)", link)
        if m:
            cid, mid = "-100" + m.group(1), m.group(2)
        elif m2:
            cid, mid = "@" + m2.group(1), m2.group(2)
        if not cid or not mid:
            return err("Give a message link, or channel + message ID")
        try:
            mid = int(mid)
            when = parse_when(d.get("when"))
        except ValueError:
            return err("Invalid message ID or time")
        item = {"id": "del_" + uuid.uuid4().hex[:8], "channel_id": norm_chat(cid),
                "channel_title": str(d.get("channel_title") or cid), "message_id": mid,
                "delete_at": when.isoformat(), "deleted": False, "created_at": now_iso()}
        with lock:
            G("scheduled_deletions").append(item)
            sync()
        return ok(id=item["id"])

    @route("/api/admin/reactionbots", methods=["POST"])
    def adm_rb_add():
        import requests as _rq
        d = request.get_json(silent=True) or {}
        cid = norm_chat(d.get("channel_id", ""))
        if not cid:
            return err("Channel is required")
        added = 0
        bad = []
        for b in d.get("bots", []):
            tok = str(b.get("token", "")).strip()
            emo = str(b.get("emoji", "")).strip()
            if ":" not in tok or len(tok) < 20 or not emo:
                bad.append(tok[:8] or "?")
                continue
            try:
                r = _rq.get("https://api.telegram.org/bot%s/getMe" % tok, timeout=10).json()
                if not r.get("ok"):
                    bad.append(tok.split(":")[0])
                    continue
            except Exception:
                bad.append(tok.split(":")[0])
                continue
            with lock:
                G("bot_settings").setdefault("reaction_bots", []).append({
                    "token": tok, "bot_id": tok.split(":")[0], "channel_id": cid,
                    "channel_title": str(d.get("channel_title") or cid), "emoji": emo})
                sync()
            added += 1
        if not added:
            return err("No valid bot token. Invalid: " + ", ".join(bad))
        return ok(added=added, invalid=bad)

    @route("/api/admin/userbot/start", methods=["POST"])
    def adm_ub_start():
        if not G("USERBOT_AVAILABLE"):
            return err("Userbot module is not available on this server")
        d = request.get_json(silent=True) or {}
        phone = str(d.get("phone", "")).replace(" ", "")
        keys = [k.strip() for k in d.get("keys", []) if len(str(k).strip()) >= 20]
        if not phone.startswith("+") or len(phone) < 10:
            return err("Phone must look like +8801...")
        if not keys:
            return err("Add at least one Gemini API key")
        try:
            res = G("run_async")(G("ub_module").start_userbot(phone, keys, G("ADMIN_ID")))
        except Exception as e:
            return err(str(e))
        return ok(msg=res.get("msg")) if res.get("ok") else err(res.get("msg", "Failed"))

    @route("/api/admin/userbot/verify", methods=["POST"])
    def adm_ub_verify():
        if not G("USERBOT_AVAILABLE"):
            return err("Userbot module is not available on this server")
        d = request.get_json(silent=True) or {}
        phone = str(d.get("phone", "")).replace(" ", "")
        ub, ra = G("ub_module"), G("run_async")
        try:
            if d.get("password"):
                res = ra(ub.verify_2fa(phone, str(d["password"])))
            else:
                res = ra(ub.verify_code(phone, str(d.get("code", "")).replace(" ", "")))
        except Exception as e:
            return err(str(e))
        if res.get("ok"):
            return ok()
        if res.get("msg") == "2FA_PASSWORD_REQUIRED":
            return ok(need_2fa=True)
        return err(res.get("msg", "Failed"))

    # ------------------------------------------------------------------
    # userbot + danger zone
    # ------------------------------------------------------------------
    @route("/api/admin/userbot")
    def adm_userbot():
        if not G("USERBOT_AVAILABLE"):
            return ok(available=False, accounts=[])
        ub = G("ub_module")
        try:
            data = ub.load_ub() or {}
        except Exception:
            data = {}
        active = set(getattr(ub, "ACTIVE_CLIENTS", {}).keys())
        accs = [{"phone": ph, "active": ph in active} for ph in data.keys()]
        return ok(available=True, accounts=accs)

    @route("/api/admin/userbot/reset", methods=["POST"])
    def adm_userbot_reset():
        if not G("USERBOT_AVAILABLE"):
            return err("Userbot module is not available on this server")
        ub = G("ub_module")
        run_async = G("run_async")
        data = ub.load_ub() or {}
        for p, v in data.items():
            try:
                run_async(ub.stop_userbot(p))
            except Exception:
                pass
            sf = (v or {}).get("session_file")
            if sf:
                for ext in ("", ".session", ".session-journal", ".session-wal", ".session-shm"):
                    try:
                        if os.path.exists(sf + ext):
                            os.remove(sf + ext)
                    except Exception:
                        pass
        try:
            ub.ACTIVE_CLIENTS.clear()
        except Exception:
            pass
        try:
            if os.path.exists("userbot_sessions.json"):
                os.remove("userbot_sessions.json")
        except Exception:
            pass
        return ok()

    @route("/api/admin/danger/clear", methods=["POST"])
    def adm_clear_all():
        if (request.get_json(silent=True) or {}).get("confirm") != "CLEAR":
            return err("Type CLEAR to confirm")
        with lock:
            db = G("get_default_db")()
            ctx["file_database"] = db["file_database"]
            ctx["registered_users"] = set(db["registered_users"])
            ctx["banned_users"] = set(db["banned_users"])
            ctx["user_activity"] = db["user_activity"]
            ctx["bot_settings"] = db["bot_settings"]
            ctx["scheduled_messages"] = db["scheduled_messages"]
            ctx["scheduled_posts"] = db["scheduled_posts"]
            ctx["scheduled_deletions"] = db["scheduled_deletions"]
            ctx["giveaway"] = db["giveaway"]
            ctx["referrals"] = db["referrals"]
            ctx["db"] = db
            sync()
        return ok()

    # ------------------------------------------------------------------
    # logs
    # ------------------------------------------------------------------
    @route("/api/admin/logs")
    def adm_logs():
        path = G("LOG_FILE")
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 24000))
                txt = f.read().decode("utf-8", "replace")
            lines = txt.splitlines()[-150:]
        except Exception:
            lines = []
        return ok(lines=lines)

    logger.info("✅ Web Admin Panel mounted at /admin")
