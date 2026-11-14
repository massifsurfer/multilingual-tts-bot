# multilingual-tts-bot

A Telegram bot for collecting voice recordings of reading prompts in multiple languages. It preserves original audio and retake history and exports pseudonymized datasets.

## Run with Docker Compose

1. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN`.
2. Create `data/consent.txt` with your participation terms and put your corpus at `data/rus.jsonl`.
3. Make `data/` writable by the container user. Set `BOT_UID` and `BOT_GID` in `.env` if your IDs differ from 1000.

```bash
docker compose build
docker compose run --rm bot import-texts rus /app/data/rus.jsonl
docker compose up -d
docker compose logs --tail 50 bot
```

The bot needs outbound access to Telegram. Run one instance per token. Keep `.env` and `data/` out of Git.

## Corpora and participation

Set active languages in `corpora.toml` using ISO 639-3 codes:

```toml
[languages]
rus = "Russian"
che = "Chechen"
```

Each corpus is UTF-8 JSONL with one prompt per line. Only `text` is required.

```jsonl
{"text":"Сегодня хорошая погода."}
```

Import each corpus with its language code. Reimporting skips duplicate prompts. After changing `corpora.toml`, run `docker compose up -d --force-recreate bot` with your deployment's Compose files. Removing a language hides it while preserving its data.

In a private chat, send `/start`, accept the terms, choose a language, and use **Next text**. **Help** lists the controls. In **Settings → Language & dialect**, choose **Keep current dialect** to reuse your saved dialect. To pause, open **Settings → Participation** or send `/stop`, then confirm. In **Settings**, **Stats** (`/stats`) shows personal counts and **Delete data** (`/delete_data`) deletes the profile and recordings after confirmation. Existing backups and exports require separate cleanup.

## Administration

Run these commands against the same Compose service and data directory as the bot:

```bash
docker compose exec bot python -m app.main stats
docker compose exec bot python -m app.main list-recordings
docker compose exec bot python -m app.main moderate-recording RECORDING_UUID accepted
docker compose exec bot python -m app.main export-dataset /app/data/exports/accepted
```

Exports include accepted takes by default. Use `--format csv`, `--language CODE`, or `--all-takes` as needed. See `.env.example` for upload limits. Set `ADMIN_TELEGRAM_IDS` to enable `/abuse_stats`, `/user_info`, `/ban`, `/unban`, `/exempt`, and `/unexempt` in private chat.

## Backup and restore

Pause administrative writes and stop the bot before backing up. Use fresh output directories.

```bash
docker compose stop bot
docker compose run --rm bot backup /app/data/backups/snapshot
docker compose start bot
docker compose run --rm bot restore /app/data/backups/snapshot /app/data/restored
```

To switch to restored data, stop the bot, move the existing SQLite database and its sidecar files and audio directory aside, then place the restored `database.sqlite` and `audio/` under `data/`. Start the bot and check its logs.

## Development

With Python 3.12+, uv, and FFmpeg installed:

```bash
uv venv .venv
uv pip install --python .venv -r pyproject.toml
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m app.main --help
```
