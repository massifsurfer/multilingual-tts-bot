from dataclasses import dataclass
from pathlib import Path
import os
import re
import tomllib

from dotenv import load_dotenv


def load_corpora(path: Path) -> tuple[tuple[str, str], ...]:
    try:
        with path.open("rb") as source:
            config = tomllib.load(source)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read corpus configuration {path}: {exc}") from exc

    languages = config.get("languages")
    if set(config) != {"languages"} or not isinstance(languages, dict):
        raise ValueError(f"{path} must contain a [languages] table")

    entries = []
    names = set()
    for code, name in languages.items():
        if not re.fullmatch("[a-z]{3}", code):
            raise ValueError(f"{path}: language codes must contain three lowercase ASCII letters")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path}: language names must be nonempty strings")
        name = name.strip()
        if name in names:
            raise ValueError(f"{path}: language names must be unique")
        entries.append((code, name))
        names.add(name)
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path = Path("data/database.sqlite")
    storage_backend: str = "local"
    local_storage_path: Path = Path("data/audio")
    min_audio_free_bytes: int = 268_435_456
    max_voice_bytes: int = 5_000_000
    max_voice_seconds: int = 120

    upload_limit: int = 5
    upload_window_seconds: int = 60
    violation_limit: int = 3
    violation_window_seconds: int = 600
    auto_ban_seconds: int = 3600
    severe_voice_bytes: int = 20000000
    languages: tuple[tuple[str, str], ...] = ()
    admin_telegram_ids: frozenset[int] = frozenset()

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(dotenv_path=".env")
        backend = os.getenv("STORAGE_BACKEND", "local")
        if backend != "local":
            raise ValueError("STORAGE_BACKEND должен быть local")
        max_voice_bytes = int(os.getenv("MAX_VOICE_BYTES", "5000000"))
        max_voice_seconds = int(os.getenv("MAX_VOICE_SECONDS", "120"))
        limits = {
            "upload_limit": int(os.getenv("UPLOAD_LIMIT", "5")),
            "upload_window_seconds": int(os.getenv("UPLOAD_WINDOW_SECONDS", "60")),
            "violation_limit": int(os.getenv("VIOLATION_LIMIT", "3")),
            "violation_window_seconds": int(os.getenv("VIOLATION_WINDOW_SECONDS", "600")),
            "auto_ban_seconds": int(os.getenv("AUTO_BAN_SECONDS", "3600")),
            "severe_voice_bytes": int(os.getenv("SEVERE_VOICE_BYTES", "20000000")),
        }
        admins = frozenset(int(value) for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if value.strip())
        if limits["upload_window_seconds"] > 86400:
            raise ValueError("UPLOAD_WINDOW_SECONDS must be at most 86400")
        if min(max_voice_bytes, max_voice_seconds, *limits.values(), *admins) <= 0:
            raise ValueError("Лимиты голосовых сообщений должны быть положительными")
        min_audio_free_bytes = int(os.getenv("MIN_AUDIO_FREE_BYTES", "268435456"))
        if min_audio_free_bytes < 0:
            raise ValueError("MIN_AUDIO_FREE_BYTES cannot be negative")
        return cls(
            database_path=Path(os.getenv("DATABASE_PATH") or "data/database.sqlite"),
            storage_backend=backend,
            local_storage_path=Path(os.getenv("LOCAL_STORAGE_PATH") or "data/audio"),
            min_audio_free_bytes=min_audio_free_bytes,
            max_voice_bytes=max_voice_bytes,
            max_voice_seconds=max_voice_seconds,
            admin_telegram_ids=admins,
            languages=load_corpora(Path("corpora.toml")),
            **limits,
        )
