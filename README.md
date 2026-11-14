# multilingual-tts-bot

A Telegram bot for collecting original voice recordings of reading prompts in multiple languages.

## Local setup

Install Python 3.12+, uv, and FFmpeg. The default database is `data/database.sqlite`.
Copy `.env.example` to `.env` to override `DATABASE_PATH`. Relative paths
are resolved from the working directory.

```bash
uv venv .venv
uv pip install --python .venv -r pyproject.toml
.venv/bin/python -m app.main init-db
.venv/bin/python -m unittest discover -s tests -v
```

`init-db` creates missing tables and preserves existing data. Changes to
existing tables require migrations. Tests use temporary SQLite databases
and run without a Telegram token.

## Architecture

Telegram handlers call application services. The services use SQLite for
metadata and `StorageBackend` for audio. `LocalStorage` writes original
bytes to `LOCAL_STORAGE_PATH` and publishes each file atomically. Saving
another file under an existing key raises `FileExistsError`. Callers close
streams returned by `get`.

- `participants` stores consent and a separate speaker UUID for exports.
  `telegram_identities` maps Telegram IDs to participant UUIDs.
- `languages`, `participant_languages`, and `texts` store language settings and prompts.
- `assignments` links participants to prompts. Each participant and prompt
  pair is unique. `recordings` stores each take under a unique file key,
  with at most one `current` or `accepted` take per assignment.
- `participant_restrictions` and `abuse_events` store restrictions and violations.

SQLite cascades deletion of participant rows to related rows. The deletion CLI
removes audio through `StorageBackend`.

## Roadmap

1. Application structure, configuration, and SQLite.
2. Storage interface and local storage.
3. Registration, consent, and language and dialect selection.
4. Corpus import and assignment selection.
5. Original audio uploads and take history.
6. Upload limits and administrative controls for restrictions.
7. CLI for statistics, moderation, and participant data deletion.
8. JSONL and CSV export with audio.
9. Docker, deployment, backups, and recovery.

## Telegram onboarding

Set `TELEGRAM_BOT_TOKEN` in `.env` to the token from BotFather. Prepare a
UTF-8 consent file covering the purpose of the collection, collected data,
retention, voice identification risk, licensing, intended uses, and how
participants can withdraw and request deletion. Describe the process currently
in use for your project.

```bash
.venv/bin/python -m app.main run-bot --consent-file ./data/consent.txt
```

In a private chat, send `/start` to review the terms and select
**I agree to participate**. Use **Settings** (`/settings`) to select an
active language from SQLite. Language buttons display the names supplied
when the languages are added. Enter a dialect, select **Done** (`/done`),
or select **Clear dialect** (`/clear_dialect`). The language and dialect
persist across restarts. Reopen **Settings** to resume an unfinished edit.
**Stop participation** (`/stop`) pauses participation and keeps the profile.
Use **Start participation** or `/start` to resume.
Select **Help** or send `/help` for a quick guide.

## Corpus import and assignments

Declare languages in `corpora.toml` under `[languages]`. Use lowercase
ISO 639-3 codes and display names.

```toml
[languages]
rus = "Russian"
eng = "English"
```

The bot applies this configuration at startup. `init-db` and `import-texts`
also apply it before working with the database. Updating a name under the
same code preserves its ID and activity status. Omitting a code leaves
the existing database entry and corpus intact. With Docker Compose, run
`docker compose up -d --force-recreate bot` with the same Compose files used
for deployment to reload the mounted configuration.


Use UTF-8 JSONL with one JSON object per line. `text` is required.
`dialect`, `difficulty`, and `category` are optional strings or `null`.
The import command specifies the language, and SQLite assigns prompt IDs.

```jsonl
{"text":"Сегодня хорошая погода.","difficulty":"easy","category":"daily life"}
{"text":"Поезд прибывает на вторую платформу."}
```

Import skips blank lines, trims surrounding whitespace from `text`, and
keeps the first entry and its metadata when a prompt repeats within a language.
Invalid entries or prompts exceeding the Telegram message limit abort the import.

```bash
.venv/bin/python -m app.main import-texts rus ./data/rus.jsonl
```

**Next text** (`/next`) returns the open assignment for the selected language
or picks a random active prompt the participant has never been assigned.
**Skip task** (`/skip`) closes the open assignment. Other participants may
receive the same prompt.

## Voice recordings

After receiving a prompt, send a voice message in the private chat.
The bot closes the assignment after saving the original bytes under a random
filename in `LOCAL_STORAGE_PATH`. New files use keys such as
`rus/2026/11/<recording_id>.ogg` grouped by language code and UTC month.
Languages without a three-letter code use `language-<id>`. Existing files
keep their original keys. For the first voice test, use a separate
database with the test corpus and consent terms that describe voice collection.

`MAX_VOICE_SECONDS` and `MAX_VOICE_BYTES` default to 120 seconds and
5,000,000 bytes. The bot checks the duration reported by Telegram, the
download size, and the Ogg Opus header. Telegram redelivery of a saved
update returns the existing confirmation.

**Record another take** (`/rerecord`)
reopens the last recorded assignment for a new submission. Finish or
skip any other open assignment first. **Skip task** cancels a pending
retake and keeps the previous take.

Each take gets its own file. Once the new file is saved, the older take
becomes `superseded` and the new take becomes `current`. If saving fails,
the previous take stays current and the participant can retry.

## Upload limits and restrictions

The bot checks restrictions and voice metadata before downloading. By default,
it allows five upload attempts per 60 seconds. Three violations within 600
seconds trigger a one-hour ban. A file reported as 20,000,000 bytes or larger
triggers an immediate one-hour ban. See `.env.example` for the thresholds.
Exempt participants still face upload limits. Applying an exemption to a
banned participant leaves the ban in place until it expires or an admin lifts it.

Set `ADMIN_TELEGRAM_IDS` to comma-separated numeric Telegram user IDs to
enable admin commands in private chat. Commands target the participant UUID
from the local `participants` table.

- `/abuse_stats` shows counts by violation type.
- `/user_info <participant UUID>` shows restrictions and recent violations.
- `/ban <participant UUID> <reason>` applies a manual ban.
- `/unban <participant UUID> <reason>` lifts a ban.
- `/exempt <participant UUID> <reason>` and `/unexempt <participant UUID> <reason>`
  manage the auto-ban exemption. Restriction changes are audited in SQLite.

## Local administration

Use **Settings → Stop participation** to pause participation. The `/stop`
command also remains available.

Run the CLI with the same `DATABASE_PATH` and `LOCAL_STORAGE_PATH` as the bot.

```bash
.venv/bin/python -m app.main stats
.venv/bin/python -m app.main list-recordings --limit 20
.venv/bin/python -m app.main moderate-recording RECORDING_UUID accepted
.venv/bin/python -m app.main moderate-recording RECORDING_UUID rejected
.venv/bin/python -m app.main delete-participant PARTICIPANT_UUID --confirm
```

The recording list includes file keys and prompts for local review. Moderation
targets the current take. Deletion removes all takes, including superseded files,
and cascades through the participant's database records. If deleting a file fails,
run the same command again. The participant remains paused until deletion succeeds.
Previously created backup copies require separate cleanup.

## Dataset export

Use the same `DATABASE_PATH` and `LOCAL_STORAGE_PATH` as the bot. Choose a new
output directory for each export.

```bash
.venv/bin/python -m app.main export-dataset ./data/exports/accepted
.venv/bin/python -m app.main export-dataset ./data/exports/russian-history --format csv --language rus --all-takes
```

The default export includes accepted takes across all languages. Use
`--language CODE` to select one language or `--all-takes` to include current,
rejected, and superseded takes. Each export contains `metadata.jsonl` or
`metadata.csv` and the original files in `audio/`. Metadata includes a stable
speaker UUID and relative audio paths. Telegram IDs, internal participant
IDs, storage keys, and timestamps are excluded. Audio remains identifiable
by voice. Previously created exports require separate cleanup when a
participant requests deletion.

An export with missing audio fails before publishing the output directory.
The command holds a SQLite write lock while copying files, so run large
exports with the bot stopped to avoid delaying bot writes.

## Docker deployment

Install Docker Engine and Docker Compose. On a Linux server with systemd, enable
Docker at boot with `sudo systemctl enable --now docker`. The bot uses
`restart: unless-stopped` and long polling, so it needs outbound access to
Telegram. Run one bot process per token.

Create `data/`, put the consent file at `data/consent.txt`, and copy your
JSONL corpus to `data/rus.jsonl`. Copy `.env.example` to `.env` and set the
token and optional admin IDs. Keep `.env` and `data/` local. Compose runs with UID and GID 1000 by default.
If the data directory belongs to another regular user, set `BOT_UID` and
`BOT_GID` in `.env` to that user's IDs. Make the directory writable by
that user. Compose stores SQLite and audio under the mounted `data/` directory.

```bash
docker compose build
docker compose run --rm bot import-texts rus /app/data/rus.jsonl
docker compose up -d
docker compose logs --tail 50 bot
```

`MIN_AUDIO_FREE_BYTES` defaults to 268435456 (256 MiB). When free space on
the audio filesystem drops below this threshold, the bot logs a warning and
rejects the write. The assignment stays open for retry. Set the value to zero
to disable the check.

## Backup and recovery

Stop the bot and pause administrative writes before backing up. This keeps
SQLite and the audio files consistent across the copy. Each backup contains
`database.sqlite`, the referenced originals in `audio/`, and `checksums.json`.
Backup and restore validate SQLite and require a new output directory.

```bash
docker compose stop bot
docker compose run --rm bot backup /app/data/backups/snapshot
docker compose start bot
docker compose run --rm bot restore /app/data/backups/snapshot /app/data/restored
```

Keep a protected copy on separate storage and apply your participant data
retention and deletion policy to all backup copies. Backups contain voice
recordings and the Telegram identity mapping. Store configuration and consent
terms separately. The same `backup` and `restore` commands can be run locally
with `.venv/bin/python -m app.main` and the configured paths.

Before switching to restored data, inspect it with `stats` and `list-recordings`.
For a Compose deployment, stop the bot and preserve the existing
`data/database.sqlite` (including any SQLite sidecar files) and `data/audio/`
outside the active paths. Place the restored database and audio at those paths,
then start the bot and check its logs.

Use `/stats` in a private chat to see your task and take counts. Use
`/delete_data` and confirm to clear your profile, tasks, and original takes.
Contact the bot owner about copies in backups and exports.

Voice validation checks the declared MIME type when present, Ogg stream
structure, and decoded Opus audio with FFmpeg. Decoded duration enforces
the recording limit. The stored file stays byte-for-byte identical to the
upload. Submitting the same Telegram file again after a successful save
counts as a spam violation for that participant. File IDs from earlier
recordings are unavailable, so duplicate detection starts with new uploads.
