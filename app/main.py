import argparse
import asyncio
import logging
import os
from pathlib import Path

from aiogram.utils.token import TokenValidationError, validate_token

from app.config.settings import Settings
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.connection import create_database_engine, initialize_database
from app.services.corpus import Corpus
from app import management_cli


async def manage_corpus(settings: Settings, args: argparse.Namespace) -> str:
    path = settings.database_path
    await initialize_database(path, settings.languages)
    engine = create_database_engine(path)
    try:
        corpus = Corpus(async_sessionmaker(engine))
        added, duplicates = await corpus.import_texts(args.code, args.source)
        return f"Добавлено текстов: {added}. Пропущено повторов: {duplicates}."
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Сбор мультиязычного речевого датасета")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Создать таблицы SQLite")
    run = commands.add_parser("run-bot", help="Запустить Telegram-бота")
    run.add_argument("--consent-file", required=True, type=Path, help="Условия участия в UTF-8")
    import_texts = commands.add_parser("import-texts", help="Импортировать тексты из JSONL (UTF-8)")
    import_texts.add_argument("code")
    import_texts.add_argument("source", type=Path)
    management_cli.add_commands(commands)
    args = parser.parse_args()

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        parser.error(str(exc))

    if args.command == "run-bot":
        from app.bot.polling import run_bot

        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        try:
            validate_token(token)
        except TokenValidationError:
            parser.error("Укажите действительный TELEGRAM_BOT_TOKEN в .env")
        try:
            consent_text = args.consent_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            parser.error(f"Не удалось прочитать условия участия: {exc}")
        if not 1 <= len(consent_text.encode("utf-16-le")) // 2 <= 4096:
            parser.error("Условия участия должны помещаться в одно сообщение Telegram")
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("aiogram.event").setLevel(logging.WARNING)
        try:
            asyncio.run(run_bot(settings, token, consent_text))
        except ValueError as exc:
            parser.error(str(exc))
        return

    if args.command in management_cli.COMMANDS:
        try:
            print(asyncio.run(management_cli.run_command(settings, args)))
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        return

    if args.command == "import-texts":
        try:
            message = asyncio.run(manage_corpus(settings, args))
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(message)
        return

    try:
        asyncio.run(initialize_database(settings.database_path, settings.languages))
    except ValueError as exc:
        parser.error(str(exc))
    print(f"База SQLite готова: {settings.database_path}")


if __name__ == "__main__":
    main()
