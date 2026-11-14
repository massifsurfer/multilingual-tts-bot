import asyncio
import shutil
from unittest import IsolatedAsyncioTestCase, skipUnless

from app.services.audio import decoded_duration


@skipUnless(shutil.which("ffmpeg"), "FFmpeg required for audio integration test")
class AudioTest(IsolatedAsyncioTestCase):
    async def test_complete_audio_decodes_and_truncation_fails(self):
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "2", "-c:a", "libopus", "-f", "ogg", "pipe:1",
            stdout=asyncio.subprocess.PIPE,
        )
        audio, _ = await process.communicate()
        self.assertEqual(process.returncode, 0)
        self.assertAlmostEqual(await decoded_duration(audio, 10), 2, places=2)
        self.assertIsNone(await decoded_duration(audio[:-1], 10))
        self.assertIsNone(await decoded_duration(audio + b"extra", 10))
        corrupt = bytearray(audio)
        corrupt[-10] ^= 1
        self.assertIsNone(await decoded_duration(bytes(corrupt), 10))
