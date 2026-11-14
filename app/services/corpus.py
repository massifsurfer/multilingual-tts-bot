import json
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import CorpusText, Language


_TEXT_FIELDS = {"text", "dialect", "difficulty", "category"}


def read_corpus(source: Path) -> list[dict[str, str | None]]:
    entries = []
    with source.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Строка {number}: некорректный JSON.") from exc
            if not isinstance(entry, dict):
                raise ValueError(f"Строка {number}: ожидается JSON-объект.")
            unknown = entry.keys() - _TEXT_FIELDS
            if unknown:
                fields = ", ".join(sorted(unknown))
                raise ValueError(f"Строка {number}: неизвестные поля ({fields}).")
            prompt = entry.get("text")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f"Строка {number}: поле text должно быть непустой строкой.")
            for field, value in entry.items():
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise ValueError(f"Строка {number}: поле {field} должно быть строкой или null.")
                try:
                    value.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise ValueError(f"Строка {number}: поле {field} содержит недопустимый Unicode.") from exc
            entry["text"] = prompt.strip()
            if len(entry["text"].encode("utf-16-le")) // 2 > 4096:
                raise ValueError(f"Строка {number} превышает лимит сообщения Telegram.")
            entries.append(entry)
    if not entries:
        raise ValueError("Файл не содержит текстов для импорта.")
    return entries


class Corpus:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def import_texts(self, code: str, source: Path) -> tuple[int, int]:
        entries = read_corpus(source)

        async with self._sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            language = await session.scalar(
                select(Language).where(Language.code == code.strip().lower())
            )
            if language is None:
                raise ValueError("Language not found. Add its code and name to corpora.toml.")

            existing = set(await session.scalars(
                select(CorpusText.text).where(CorpusText.language_id == language.language_id)
            ))
            added = []
            for entry in entries:
                prompt = entry["text"]
                if prompt not in existing:
                    added.append(CorpusText(language_id=language.language_id, **entry))
                    existing.add(prompt)
            session.add_all(added)
            return len(added), len(entries) - len(added)
