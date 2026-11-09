# Render deployment

This project runs the Telegram bot and the HTML Admin Panel in the same Render Web Service.

## Required Render Environment Variables

- `BOT_TOKEN` — your Telegram bot token
- `ADMIN_PASSWORD` — password used by the HTML Admin login
- `WEB_ADMIN_SECRET` is optional; the app derives a stable session secret from `BOT_TOKEN` and `ADMIN_PASSWORD` if it is not supplied.
- `WEB_ADMIN_URL` — optional public Render URL, e.g. `https://your-service.onrender.com`

Do not put the real bot token or admin password inside `main.py`, `render.yaml`, or the ZIP. Add them in Render's Environment settings.

## Start command

`python main.py`

## Admin URL

Open `https://YOUR-SERVICE.onrender.com/admin` after deployment.

## Health check

`/health` returns `OK`.

## Important

The Telegram bot no longer shows the old admin control-panel buttons. Admin management is provided by the HTML Admin Panel.

The project currently uses JSON file storage. Render's normal ephemeral filesystem can reset files after a restart/redeploy. For persistent production data, attach a Render persistent disk (or migrate the database to an external database) and set `DB_FILE`/`LOG_FILE` to paths on that persistent storage.
