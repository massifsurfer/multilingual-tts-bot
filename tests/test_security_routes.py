from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User, Voice
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.admin import admin_router
from app.bot.privacy import privacy_router
from app.bot.voice import voice_router
from app.config.settings import Settings
from app.database.connection import create_database_engine, initialize_database
from app.database.models import AbuseEvent, AbuseEventType, RestrictionAudit, TelegramIdentity
from app.services.abuse import AbuseProtection
from app.services.maintenance import Maintenance
from app.services.participants import Participants
from app.services.recordings import Recordings
from app.storage.local import LocalStorage


class SecurityRoutesTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        path = self.root / "test.sqlite"
        await initialize_database(path)
        engine = create_database_engine(path)
        self.addAsyncCleanup(engine.dispose)
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.settings = Settings()
        self.abuse = AbuseProtection(self.sessions, self.settings)
        self.participants = Participants(self.sessions)
        await self.participants.consent(7)
        async with self.sessions() as session:
            identity = await session.get(TelegramIdentity, 7)
            self.participant_id = identity.participant_id
        self.bot = Bot("123:test")
        self.addAsyncCleanup(self.bot.session.close)
        self.bot.session.make_request = AsyncMock()
        self.bot.download = AsyncMock()
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(admin_router(self.abuse, frozenset({42})))
        recordings = Recordings(
            self.sessions, LocalStorage(self.root / "audio"),
            max_bytes=self.settings.max_voice_bytes, max_seconds=self.settings.max_voice_seconds,
        )
        self.dispatcher.include_router(privacy_router(
            Maintenance(self.sessions, LocalStorage(self.root / "audio"))
        ))
        self.dispatcher.include_router(voice_router(recordings, self.abuse))
        self.update_id = 0

    async def feed(self, sender, *, chat_type="private", **payload):
        self.update_id += 1
        self.bot.session.make_request.reset_mock()
        message = Message(
            message_id=self.update_id,
            date=datetime.now(UTC),
            chat=Chat(id=sender if chat_type == "private" else -100, type=chat_type),
            from_user=User(id=sender, is_bot=False, first_name="Test"),
            **payload,
        )
        await self.dispatcher.feed_update(self.bot, Update(update_id=self.update_id, message=message))
        if self.bot.session.make_request.await_count:
            request = self.bot.session.make_request.await_args.args[1]
            self.assertIsInstance(request, SendMessage)
            return request.text
        return None

    async def test_admin_commands_require_allowlisted_user_and_private_chat(self):
        target = self.participant_id
        commands = [
            "/abuse_stats", f"/user_info {target}",
            *(f"/{action} {target} Test reason" for action in ("ban", "unban", "exempt", "unexempt")),
        ]
        for command in commands:
            with self.subTest(command=command.split()[0]):
                self.assertIsNone(await self.feed(7, text=command))
                self.assertIsNone(await self.feed(42, chat_type="group", text=command))

        self.assertIsNone(await self.abuse.ban_reason(7))
        self.assertIn("Usage:", await self.feed(42, text=f"/ban {target}"))
        self.assertEqual(await self.feed(42, text=f"/ban {target} Manual review"), "Restriction updated.")
        self.assertEqual(await self.abuse.ban_reason(7), "Manual review")
        self.assertEqual(await self.feed(42, text=f"/unban {target} Review complete"), "Restriction updated.")
        self.assertIsNone(await self.abuse.ban_reason(7))
        async with self.sessions() as session:
            entries = (await session.scalars(select(RestrictionAudit).order_by(RestrictionAudit.audit_id))).all()
            self.assertEqual(
                [(row.admin_telegram_id, row.action, row.reason) for row in entries],
                [(42, "ban", "Manual review"), (42, "unban", "Review complete")],
            )

    async def test_voice_rejections_do_not_download(self):
        for sender, duration, size, expected in (
            (8, 1, 100, "participation terms"),
            (7, self.settings.max_voice_seconds + 1, 100, "too long"),
            (7, 1, self.settings.max_voice_bytes + 1, "too large"),
        ):
            voice = Voice(file_id="voice", file_unique_id="voice", duration=duration, file_size=size)
            self.assertIn(expected, await self.feed(sender, voice=voice))

        await self.abuse.change(self.participant_id, 42, "ban", "Manual review")
        valid = Voice(file_id="voice", file_unique_id="voice", duration=1, file_size=100)
        self.assertEqual(await self.feed(7, voice=valid), "Manual review")
        self.bot.download.assert_not_awaited()
        self.assertFalse((self.root / "audio").exists())
        async with self.sessions() as session:
            events = await session.scalars(select(AbuseEvent.event_type).order_by(AbuseEvent.event_id))
            self.assertEqual(list(events), [AbuseEventType.AUDIO_TOO_LONG, AbuseEventType.AUDIO_TOO_LARGE])

    async def test_auto_ban_expires_at_configured_deadline(self):
        now = datetime(2026, 1, 1)
        with patch("app.services.abuse.utc_now", return_value=now) as clock:
            rejection = await self.abuse.check_voice(7, 100, 1, self.settings.severe_voice_bytes)
            self.assertIn("Temporarily blocked", rejection)
            clock.return_value = now + timedelta(seconds=self.settings.auto_ban_seconds - 1)
            self.assertEqual(await self.abuse.check_voice(7, 101, 1, 100), rejection)
            clock.return_value += timedelta(seconds=1)
            self.assertIsNone(await self.abuse.ban_reason(7))
            self.assertIsNone(await self.abuse.check_voice(7, 102, 1, 100))

    async def test_deletion_requires_confirmation_from_profile_owner(self):
        self.assertIn("Completed tasks: 0", await self.feed(7, text="/stats"))
        self.assertIn("Delete your profile", await self.feed(7, text="/delete_data"))
        confirmation = self.bot.session.make_request.await_args.args[1].reply_markup
        callback_data = confirmation.inline_keyboard[0][0].callback_data
        self.assertTrue(await self.participants.is_active(7))

        for sender in (8, 7):
            user = User(id=sender, is_bot=False, first_name="Test")
            message = Message(
                message_id=2, date=datetime.now(UTC), chat=Chat(id=sender, type="private"),
            )
            query = CallbackQuery(
                id=str(sender), from_user=user, chat_instance="test", message=message,
                data=callback_data,
            )
            await self.dispatcher.feed_update(
                self.bot, Update(update_id=sender + 10, callback_query=query)
            )
            self.assertEqual(await self.participants.is_active(7), sender == 8)

        async with self.sessions() as session:
            self.assertIsNone(await session.get(TelegramIdentity, 7))

    async def test_unsupported_mime_is_rejected_before_download(self):
        voice = Voice(
            file_id="voice", file_unique_id="voice", duration=1, file_size=100, mime_type="audio/mpeg"
        )
        self.assertEqual(await self.feed(7, voice=voice), "Send a valid Telegram voice message.")
        self.bot.download.assert_not_awaited()
