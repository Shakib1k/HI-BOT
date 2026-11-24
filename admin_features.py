# -*- coding: utf-8 -*-
"""
Admin-panel API for the newer features (runtime logic lives in features.py):
  * Join Requests  - private channel / group auto approve
  * Group Connect  - add / view / edit / delete groups + protection settings
  * Target Blocker - silent purge of chosen user IDs
  * Reaction speed - gap between reaction bots
"""
import base64
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime


def register(h):
    route, ok, err, G, sync = h["route"], h["ok"], h["err"], h["G"], h["sync"]
    ctx, bot, request, ADMIN_ID = h["ctx"], h["bot"], h["request"], h["ADMIN_ID"]
    feats = ctx["features"]
    same = feats.same_chat
    photo_cache = {}
    info_cache = {}

    # ------------------------------------------------------------------ helpers
    def photo_data(chat):
        ph = getattr(chat, "photo", None)
        fid = getattr(ph, "small_file_id", None)
        if not fid:
            return None
        if fid in photo_cache:
            return photo_cache[fid]
        try:
            f = bot.get_file(fid)
            raw = bot.download_file(f.file_path)
            uri = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
        except Exception:
            uri = None
        if len(photo_cache) > 200:
            photo_cache.clear()
        photo_cache[fid] = uri
        return uri

    def chat_info(ref, fresh=False):
        ref = str(ref).strip()
        c = info_cache.get(ref)
        if c and not fresh and time.time() - c[0] < 45:
            return dict(c[1])
        chat = bot.get_chat(ref)
        me = bot.get_me().id
        info = {"chat_id": str(chat.id), "title": chat.title or str(chat.id), "username": getattr(chat, "username", None),
                "type": chat.type, "description": getattr(chat, "description", None) or "", "photo": photo_data(chat),
                "members": None, "bot_admin": False}
        try:
            info["members"] = bot.get_chat_member_count(chat.id)
            feats.record_members(chat.id, info["members"])
        except Exception:
            pass
        try:
            m = bot.get_chat_member(chat.id, me)
            creator = m.status == "creator"
            info["bot_admin"] = m.status in ("administrator", "creator")
            for k in ("can_delete_messages", "can_invite_users", "can_restrict_members", "can_change_info", "can_promote_members", "can_pin_messages"):
                info[k] = creator or bool(getattr(m, k, False))
        except Exception:
            pass
        info_cache[ref] = (time.time(), info)
        return dict(info)

    def safe_info(ref, fresh=False):
        try:
            return chat_info(ref, fresh)
        except Exception as ex:
            return {"chat_id": str(ref), "title": None, "error": str(ex)[:140], "bot_admin": False}

    def many(refs, fresh=False):
        with ThreadPoolExecutor(max_workers=8) as ex:
            return list(ex.map(lambda r: safe_info(r, fresh), refs))

    # ------------------------------------------------------------------ JOIN REQUESTS
    JR_KEYS = ("auto", "delay", "welcome", "welcome_text")

    def jr_clean(d, cfg):
        if "auto" in d: cfg["auto"] = bool(d["auto"])
        if "welcome" in d: cfg["welcome"] = bool(d["welcome"])
        if "delay" in d:
            try: cfg["delay"] = max(0, min(float(d["delay"] or 0), 900))
            except (TypeError, ValueError): cfg["delay"] = 0
        if "welcome_text" in d: cfg["welcome_text"] = str(d["welcome_text"] or "")[:700]

    def jr_item(key, cfg, live):
        today = datetime.now().strftime("%Y-%m-%d")
        return {"key": key, "chat_id": key, "title": live.get("title") or cfg.get("title") or key,
                "username": live.get("username"), "photo": live.get("photo"), "members": live.get("members"),
                "description": live.get("description"), "type": live.get("type"),
                "bot_admin": live.get("bot_admin"), "can_invite": live.get("can_invite_users"), "error": live.get("error"),
                "auto": cfg.get("auto", True), "delay": cfg.get("delay", 0), "welcome": cfg.get("welcome", False),
                "welcome_text": cfg.get("welcome_text", ""), "approved": cfg.get("approved", 0), "failed": cfg.get("failed", 0),
                "today": (cfg.get("by_day") or {}).get(today, 0), "by_day": cfg.get("by_day") or {},
                "last_approved_at": cfg.get("last_approved_at"), "last_error": cfg.get("last_error"), "added_at": cfg.get("added_at")}

    @route("/api/admin/join-requests")
    def jr_list():
        cfgs = feats.jr_cfg()
        keys = list(cfgs)
        lives = many(keys, request.args.get("refresh") == "1")
        items = [jr_item(k, cfgs[k], l) for k, l in zip(keys, lives)]
        ub = ctx.get("ub_module")
        has_ub = bool(ub and any(i.get("logged_in") for i in getattr(ub, "ACTIVE_CLIENTS", {}).values()))
        log = [x for x in reversed(G("bot_settings").get("join_request_log", []) or [])][:30]
        return ok(items=items, userbot=has_ub, log=log, totals={"chats": len(items), "approved": sum(i["approved"] for i in items), "today": sum(i["today"] for i in items)})

    @route("/api/admin/chat/resolve", methods=["POST"])
    def chat_resolve():
        ref = str((request.get_json(silent=True) or {}).get("chat_id", "")).strip()
        if not ref:
            return err("Enter the chat ID (example: -1001234567890) or @username")
        try:
            return ok(info=chat_info(ref, True))
        except Exception as ex:
            return err("Telegram could not find this chat. Add the bot as an admin there first, then try again. (" + str(ex)[:120] + ")")

    @route("/api/admin/join-requests", methods=["POST"])
    def jr_add():
        d = request.get_json(silent=True) or {}
        ref = str(d.get("chat_id", "")).strip()
        try:
            info = chat_info(ref, True)
        except Exception as ex:
            return err("Telegram could not find this chat: " + str(ex)[:120])
        if info["type"] not in ("channel", "supergroup", "group"):
            return err("This is not a channel or group")
        if not info["bot_admin"]:
            return err("Make the bot an admin there first (with the 'Invite users via link' right), then save again.")
        key = info["chat_id"]
        if feats.find_in(feats.jr_cfg(), key)[0]:
            return err("This chat is already in the list")
        cfg = {"auto": True, "delay": 0, "welcome": False, "welcome_text": "", "approved": 0, "failed": 0,
               "title": info["title"], "added_at": datetime.now().isoformat(timespec="seconds")}
        jr_clean(d, cfg)
        feats.jr_cfg()[key] = cfg
        feats.jr_log(key, "connected")
        sync()
        return ok(item=jr_item(key, cfg, info), warning=None if info.get("can_invite_users") else "The bot is admin but lacks 'Invite users via link' - Telegram will not send it join requests until you enable it.")

    @route("/api/admin/join-requests/<key>", methods=["PATCH"])
    def jr_edit(key):
        k, cfg = feats.find_in(feats.jr_cfg(), key)
        if not cfg: return err("Not found", 404)
        jr_clean(request.get_json(silent=True) or {}, cfg)
        sync()
        return ok(item=cfg)

    @route("/api/admin/join-requests/<key>", methods=["DELETE"])
    def jr_delete(key):
        k, cfg = feats.find_in(feats.jr_cfg(), key)
        if not cfg: return err("Not found", 404)
        feats.jr_cfg().pop(k, None)
        sync()
        return ok()

    def _ub_call(coro_name, chat_id):
        ub = ctx.get("ub_module")
        if not ub or not hasattr(ub, coro_name):
            return {"ok": False, "msg": "Telethon userbot is not available"}
        return ctx["run_async"](getattr(ub, coro_name)(chat_id))

    @route("/api/admin/join-requests/<key>/pending", methods=["POST"])
    def jr_pending(key):
        k, cfg = feats.find_in(feats.jr_cfg(), key)
        if not cfg: return err("Not found", 404)
        r = _ub_call("join_pending_count", int(k))
        return ok(count=r.get("count")) if r.get("ok") else err(r.get("msg", "Failed"))

    @route("/api/admin/join-requests/<key>/approve-all", methods=["POST"])
    def jr_approve_all(key):
        k, cfg = feats.find_in(feats.jr_cfg(), key)
        if not cfg: return err("Not found", 404)
        r = _ub_call("approve_all_pending", int(k))
        if not r.get("ok"): return err(r.get("msg", "Failed"))
        feats.jr_log(k, "approved all pending requests (userbot)")
        sync()
        return ok(message=r.get("msg"))

    # ------------------------------------------------------------------ GROUPS
    D = feats.GROUP_DEFAULTS

    def gr_clean(d, cfg):
        for k, default in D.items():
            if k not in d or k == "lock":
                continue
            v = d[k]
            if isinstance(default, bool):
                cfg[k] = bool(v)
            elif k == "banned_words":
                words = v if isinstance(v, list) else str(v or "").replace(",", "\n").split("\n")
                cfg[k] = [w.strip()[:40] for w in words if w and w.strip()][:200]
            elif k == "action":
                cfg[k] = v if v in ("mute", "kick", "ban") else "mute"
            elif k == "welcome_text":
                cfg[k] = str(v or "")[:800]
            elif isinstance(default, int):
                try: cfg[k] = max(0, min(int(v), 100000))
                except (TypeError, ValueError): pass
        if cfg.get("warn_limit", 3) < 1: cfg["warn_limit"] = 1
        if cfg.get("flood_limit", 6) < 2: cfg["flood_limit"] = 2

    def gr_item(key, cfg, live):
        full = feats.gr_full(cfg)
        return {"key": key, "chat_id": key, "title": live.get("title") or cfg.get("title") or key, "username": live.get("username"),
                "photo": live.get("photo"), "members": live.get("members"), "description": live.get("description"),
                "type": live.get("type"), "error": live.get("error"),
                "rights": {k: live.get(k) for k in ("bot_admin", "can_delete_messages", "can_restrict_members", "can_change_info", "can_invite_users", "can_pin_messages")},
                "settings": full, "stats": cfg.get("stats") or {}, "added_at": cfg.get("added_at"),
                "growth24": feats.growth(key, 24)}

    @route("/api/admin/groups")
    def gr_list():
        cfgs = feats.gr_cfg()
        keys = list(cfgs)
        lives = many(keys, request.args.get("refresh") == "1")
        items = [gr_item(k, cfgs[k], l) for k, l in zip(keys, lives)]
        return ok(items=items, defaults=D, totals={"groups": len(items), "members": sum(i["members"] or 0 for i in items),
                  "deleted": sum(i["stats"].get("deleted", 0) for i in items), "banned": sum(i["stats"].get("banned", 0) for i in items)})

    @route("/api/admin/groups", methods=["POST"])
    def gr_add():
        d = request.get_json(silent=True) or {}
        try:
            info = chat_info(str(d.get("chat_id", "")).strip(), True)
        except Exception as ex:
            return err("Telegram could not find this group: " + str(ex)[:120])
        if info["type"] not in ("group", "supergroup"):
            return err("This is not a group. For channels use Channel Hub / Join Requests.")
        if not info["bot_admin"]:
            return err("Make the bot an admin in the group first (Delete messages + Ban users), then save again.")
        key = info["chat_id"]
        if feats.find_in(feats.gr_cfg(), key)[0]:
            return err("This group is already connected")
        cfg = {"title": info["title"], "added_at": datetime.now().isoformat(timespec="seconds"), "stats": {}}
        gr_clean(d.get("settings") or {}, cfg)
        feats.gr_cfg()[key] = cfg
        feats.gr_log(key, "-", "connected", info["title"])
        sync()
        return ok(item=gr_item(key, cfg, info))

    @route("/api/admin/groups/<key>", methods=["PATCH"])
    def gr_edit(key):
        k, cfg = feats.find_in(feats.gr_cfg(), key)
        if not cfg: return err("Group not found", 404)
        gr_clean((request.get_json(silent=True) or {}).get("settings") or {}, cfg)
        sync()
        return ok(settings=feats.gr_full(cfg))

    @route("/api/admin/groups/<key>", methods=["DELETE"])
    def gr_delete(key):
        k, cfg = feats.find_in(feats.gr_cfg(), key)
        if not cfg: return err("Group not found", 404)
        feats.gr_cfg().pop(k, None)
        sync()
        return ok()

    def _gr(key):
        k, cfg = feats.find_in(feats.gr_cfg(), key)
        return k, cfg

    @route("/api/admin/groups/<key>/title", methods=["POST"])
    def gr_title(key):
        k, cfg = _gr(key)
        if not cfg: return err("Group not found", 404)
        t = str((request.get_json(silent=True) or {}).get("title", "")).strip()
        if not t or len(t) > 128: return err("Title must be 1-128 characters")
        try: bot.set_chat_title(int(k), t)
        except Exception as ex: return err("Telegram refused (bot needs 'Change group info'): " + str(ex)[:140])
        cfg["title"] = t; info_cache.clear(); sync()
        return ok()

    @route("/api/admin/groups/<key>/description", methods=["POST"])
    def gr_desc(key):
        k, cfg = _gr(key)
        if not cfg: return err("Group not found", 404)
        t = str((request.get_json(silent=True) or {}).get("description", ""))[:255]
        try: bot.set_chat_description(int(k), t)
        except Exception as ex: return err("Telegram refused (bot needs 'Change group info'): " + str(ex)[:140])
        info_cache.clear()
        return ok()

    @route("/api/admin/groups/<key>/photo", methods=["POST"])
    def gr_photo(key):
        k, cfg = _gr(key)
        if not cfg: return err("Group not found", 404)
        fs = request.files.get("file")
        if not fs: return err("Choose an image first")
        try: bot.set_chat_photo(int(k), fs)
        except Exception as ex: return err("Telegram refused (bot needs 'Change group info'): " + str(ex)[:140])
        info_cache.clear(); photo_cache.clear()
        return ok()

    @route("/api/admin/groups/<key>/lock", methods=["POST"])
    def gr_lock(key):
        k, cfg = _gr(key)
        if not cfg: return err("Group not found", 404)
        lock_on = bool((request.get_json(silent=True) or {}).get("lock"))
        try: feats.group_set_lock(int(k), lock_on)
        except Exception as ex: return err("Telegram refused (bot needs 'Ban users' right): " + str(ex)[:140])
        cfg["lock"] = lock_on
        feats.gr_log(k, "-", "locked" if lock_on else "unlocked", "")
        sync()
        return ok(lock=lock_on)

    @route("/api/admin/groups/<key>/log")
    def gr_log_view(key):
        k, cfg = _gr(key)
        if not cfg: return err("Group not found", 404)
        out = [x for x in reversed(G("bot_settings").get("group_log", []) or []) if same(x.get("ch", ""), k)][:40]
        return ok(items=out)

    # ------------------------------------------------------------------ TARGET BLOCKER (silent purge)
    def display_names(uid):
        """Best-effort name for a user ID: direct lookup first, then the admin lists of saved channels."""
        try:
            c = bot.get_chat(int(uid))
            n = ((c.first_name or "") + " " + (c.last_name or "")).strip() or c.title or ""
            return n, getattr(c, "username", None)
        except Exception:
            pass
        for ch in G("get_all_channels")():
            cid = ch.get("chat_id")
            if not cid: continue
            try:
                for a in bot.get_chat_administrators(cid):
                    if a.user.id == int(uid):
                        return ((a.user.first_name or "") + " " + (a.user.last_name or "")).strip(), a.user.username
            except Exception:
                continue
        return "", None

    def pg_item(uid, t):
        return {"user_id": str(uid), "label": t.get("label") or t.get("name") or "", "username": t.get("username"),
                "active": t.get("active", True), "channels": t.get("channels") or [], "deleted": t.get("deleted", 0),
                "last_at": t.get("last_at"), "added_at": t.get("added_at")}

    @route("/api/admin/purge")
    def pg_list():
        targets = feats.purge_cfg()
        chans = [{"id": c["id"], "chat_id": c["chat_id"], "name": c["name"]} for c in G("get_all_channels")() if c.get("chat_id")]
        log = [x for x in reversed(G("bot_settings").get("purge_log", []) or [])][:40]
        return ok(items=[pg_item(u, t) for u, t in targets.items()], channels=chans, log=log,
                  totals={"targets": len(targets), "deleted": sum(int(t.get("deleted", 0) or 0) for t in targets.values())})

    @route("/api/admin/purge", methods=["POST"])
    def pg_add():
        d = request.get_json(silent=True) or {}
        try: uid = int(str(d.get("user_id", "")).strip())
        except Exception: return err("Enter a valid numeric Telegram User ID")
        if uid <= 0: return err("Invalid Telegram User ID")
        if uid == ADMIN_ID: return err("You cannot add the owner account")
        try:
            if uid == bot.get_me().id: return err("You cannot add the bot itself")
        except Exception:
            pass
        if str(uid) in feats.purge_cfg(): return err("This user is already in the list")
        name, uname = display_names(uid)
        t = {"label": str(d.get("label") or name or "")[:60], "name": name, "username": uname, "active": True, "deleted": 0,
             "channels": [str(x) for x in (d.get("channels") or [])][:200], "added_at": datetime.now().isoformat(timespec="seconds")}
        feats.purge_cfg()[str(uid)] = t
        sync()
        return ok(item=pg_item(uid, t))

    @route("/api/admin/purge/<uid>", methods=["PATCH"])
    def pg_edit(uid):
        t = feats.purge_cfg().get(str(uid))
        if not t: return err("Not found", 404)
        d = request.get_json(silent=True) or {}
        if "active" in d: t["active"] = bool(d["active"])
        if "label" in d: t["label"] = str(d["label"] or "")[:60]
        if "channels" in d: t["channels"] = [str(x) for x in (d["channels"] or [])][:200]
        sync()
        return ok(item=pg_item(uid, t))

    @route("/api/admin/purge/<uid>", methods=["DELETE"])
    def pg_delete(uid):
        if feats.purge_cfg().pop(str(uid), None) is None: return err("Not found", 404)
        sync()
        return ok()

    # ------------------------------------------------------------------ REACTION SPEED
    @route("/api/admin/reaction-gap")
    def rg_get():
        try: q = ctx["_reaction_queue"].qsize()
        except Exception: q = 0
        return ok(gap=float(G("bot_settings").get("reaction_gap", 1.0) or 1.0), queued=q)

    @route("/api/admin/reaction-gap", methods=["POST"])
    def rg_set():
        try: g = float((request.get_json(silent=True) or {}).get("gap", 1.0))
        except (TypeError, ValueError): return err("Invalid number")
        G("bot_settings")["reaction_gap"] = max(0.3, min(g, 10.0))
        sync()
        return ok(gap=G("bot_settings")["reaction_gap"])
