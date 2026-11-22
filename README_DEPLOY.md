# Render Deploy Guide

1. Is zip ko extract karo, saari files GitHub repo me upload karo (main.py, admin_api.py, admin.html, userbot.py, requirements.txt, .python-version, .gitignore).
2. https://dashboard.render.com -> New + -> Web Service -> apna GitHub repo select karo.
3. Settings:
   - Language: Python 3
   - Build Command: pip install -r requirements.txt
   - Start Command: python main.py
4. Environment Variables (Add Environment Variable):
   - BOT_TOKEN = tumhara bot token
   - ADMIN_PASSWORD = admin panel ka password
   - (optional) TELEGRAM_API_ID, TELEGRAM_API_HASH = userbot ke liye
5. Create Web Service dabao. Deploy hone ke baad:
   https://dca-0752.onrender.com  (seedha admin panel khulega)
   khol ke ADMIN_PASSWORD se login karo.

Note: Render free plan me disk temporary hota hai, redeploy/restart par bot_database.json reset ho sakta hai. Data bachane ke liye paid plan me Disk add karo.

## Admin
Bot ke andar ab koi admin login/command/button nahi hai. Sab kuch admin panel (admin.html) se hota hai:
file upload (phone storage se), key/links, users, broadcast, auto-reply, channels, scheduled posts
(photo/video/file + caption + link buttons + time), message deletion schedule, reaction bots,
AI userbot login, giveaway, connect channel, logs.

## APK Protect
Public bot feature: users send a .apk, it is processed by your own packer tool and sent back —
no vendor name or fake security badge attached. Admin panel's "APK Protect" tile controls:
Settings (enable, trial credits, trial wait time, premium link, low-credit message),
Grant Credits (add/remove credits for a Telegram user ID), History (processed files).
Upload your own `dpt.jar` (and `dpt-shell-v2.21.1.zip` if you have one) into an `apk_engine/`
folder next to main.py on the server - the feature is disabled until that's there.

## This update adds
- Smooth "glide" page transitions across the whole panel, press/hover feedback on buttons and
  cards, swipe-left-to-delete on list items (mobile).
- Dashboard: a 14-day new-signups chart and a "Most downloaded files" top-10 list (file
  downloads are now counted every time a file link is opened).
- Channel Groups (under Join): save a named group of channels (e.g. "Gaming channels") and
  load it with one tap when picking channels for a scheduled post.
- Join → View Join now shows each channel's live member count alongside its admin-access check.

Not included this round (said so up front, can build on request): push notifications, a
calendar view for scheduled posts, and a light/dark theme toggle - these need more infra/UI
work than the rest and I didn't want to rush them.

## This update adds
- APK Protect menu: Trial and Premium buttons now show together whenever the trial is still
  available (not either/or), so users always see both choices. Set "Premium / buy-credits
  link" to your own https://t.me/yourusername so that button opens a chat with you.
- APK Protect Settings: a clear 🟢 Live / 🔴 Not visible banner, plus a one-tap
  "Save & Go Public" / "Take offline" button next to the normal Save.
- Join → View Channels: a 📨 Test button per channel (sends a one-line message to confirm the
  bot can actually post there) and a search box once you have more than 5 channels, for
  managing many channels (10-12+) without endless scrolling.

## This update adds (full channel control)
- Add New Channel: "🔍 Auto-fetch channel name" button - type the @username/ID first, tap it,
  and the real channel title (and member count) is pulled from Telegram and filled in for you.
- Join → View Channels: a new "⚙️ Control" button per channel opens a panel where you can
  rename the channel, edit its description, pin a message, unpin everything, or delete a
  message (by ID or by pasting its t.me/c/.../ID link).
  These need the bot to actually have the matching admin right in that channel
  ("Change channel info", "Pin messages", "Delete messages") - Telegram will refuse
  otherwise, and the panel will show you the real error if so.

## This update adds (post formatting)
- New Post: each file's caption is now built from multiple "text blocks" (➕ Add Text) instead
  of one single box. Each block can be tapped through Plain / Quote / Expandable quote
  (Telegram's native collapsible blockquote - the small chevron that lets people tap to expand
  a long block). Blocks are joined in order, so you can mix a plain line, a quoted feature
  list, and another quoted "setup" block in the same post, matching the layout from the
  reference video.
