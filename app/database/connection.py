from pathlib import Path

from sqlalchemy import URL, event, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.database.models import Base, Language


def create_database_engine(path: Path) -> AsyncEngine:
    engine = create_async_engine(URL.create("sqlite+aiosqlite", database=str(path)))

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


async def initialize_database(path: Path, languages: tuple[tuple[str, str], ...] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(path)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            await connection.run_sync(Base.metadata.create_all)
            if languages is None:
                return
            rows = (await connection.execute(select(Language.code, Language.name))).all()
            by_code = {code: name for code, name in rows}
            by_name = {name: code for code, name in rows}
            configured_codes = [code for code, _ in languages]
            await connection.execute(
                update(Language)
                .where(Language.is_active, Language.code.is_(None) | Language.code.not_in(configured_codes))
                .values(is_active=False)
            )
            for code, name in languages:
                if name in by_name and by_name[name] != code:
                    raise ValueError(f"Language name {name!r} is already assigned to another code.")
                if code not in by_code:
                    await connection.execute(insert(Language).values(code=code, name=name))
                else:
                    await connection.execute(
                        update(Language).where(Language.code == code).values(name=name, is_active=True)
                    )
    finally:
        await engine.dispose()
