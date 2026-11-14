from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config.settings import Settings
from app.database.connection import create_database_engine, initialize_database
from app.database.models import AbuseEvent, RestrictionAudit, TelegramIdentity
from app.services.abuse import AbuseProtection
from app.services.assignments import AssignmentOutcome, Assignments
from app.services.participants import Participants


class AbuseTest(IsolatedAsyncioTestCase):
    async def test_rate_limit_bans_exemptions_and_replayed_updates(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "test.sqlite"
        await initialize_database(path)
        engine = create_database_engine(path)
        self.addAsyncCleanup(engine.dispose)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        abuse = AbuseProtection(sessions, replace(Settings(), upload_limit=1, violation_limit=2))
        await Participants(sessions).consent(1)
        async with sessions() as session:
            participant_id = (await session.get(TelegramIdentity, 1)).participant_id

        self.assertIsNone(await abuse.check_voice(1, 10, 2, 100))
        rate_error = await abuse.check_voice(1, 11, 2, 100)
        self.assertIn("Too many uploads", rate_error)
        self.assertEqual(await abuse.check_voice(1, 11, 2, 100), rate_error)
        self.assertIsNone(await abuse.ban_reason(1))
        self.assertIn("Temporarily blocked", await abuse.check_voice(1, 12, 2, 100))
        self.assertEqual(await Assignments(sessions).next(1), AssignmentOutcome.BLOCKED)

        self.assertTrue(await abuse.change(participant_id, 100, "exempt", "Verified participant"))
        self.assertIsNotNone(await abuse.ban_reason(1))
        self.assertTrue(await abuse.change(participant_id, 100, "unban", "Reviewed"))
        self.assertIsNone(await abuse.ban_reason(1))
        self.assertIn("too large", await abuse.check_voice(1, 13, 2, 20_000_000))
        self.assertIsNone(await abuse.ban_reason(1))
        self.assertTrue(await abuse.change(participant_id, 100, "unexempt", "Test complete"))
        self.assertIn("Temporarily blocked", await abuse.check_voice(1, 14, 2, 20_000_000))

        async with sessions() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(AbuseEvent)), 4)
            self.assertEqual(await session.scalar(select(func.count()).select_from(RestrictionAudit)), 3)
