# HTML Admin Panel

The Telegram admin feature buttons have been removed from the Telegram admin panel. Management is now provided through the web dashboard.

## Environment variables

- `BOT_TOKEN` — existing Telegram bot token.
- `ADMIN_PASSWORD` — strong password for HTML Admin login.
- `WEB_ADMIN_SECRET` — optional. You do not need to add it in Render; the app derives a stable session secret automatically from `BOT_TOKEN` and `ADMIN_PASSWORD`.
- `ADMIN_WEB_URL` — optional public URL to the `/admin` page. Example: `https://your-app.example.com/admin`
- `PORT` — hosting platform port; the app already defaults to 5000.

## Open the panel

Visit `/admin` on the deployed service and log in with `ADMIN_PASSWORD`.

## Dashboard sections

- Overview / Stats
- Force Join
- Maintenance
- User Limit
- Force Join Text Editor
- Welcome Message Editor
- Channel Manager
- Connect Channel
- Reaction Bot
- Project Files
- Files / Keys
- Users / Ban / Unban
- Auto Reply
- Auto Post / Delete scheduler status
- Giveaway
- Admins
- Broadcast
- Clear All Data

The dashboard uses the existing `bot_database.json`, so settings changed in the HTML panel are shared with the Telegram bot.
