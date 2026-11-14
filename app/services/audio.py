import asyncio


def _complete_ogg(data: bytes) -> bool:
    offset = 0
    serial = data[14:18]
    sequence = 0
    while offset + 27 <= len(data):
        header = data[offset:offset + 27]
        flags = header[5]
        if (
            header[:5] != b"OggS\x00" or header[14:18] != serial
            or int.from_bytes(header[18:22], "little") != sequence
            or bool(flags & 2) != (sequence == 0)
        ):
            return False
        end = offset + 27 + header[26]
        if end > len(data):
            return False
        segments = data[offset + 27:end]
        if sequence == 0 and (not segments or segments[0] < 19 or data[end:end + 8] != b"OpusHead"):
            return False
        offset = end + sum(segments)
        if flags & 4:
            return offset == len(data) and sequence > 0
        sequence += 1
    return False


async def decoded_duration(audio: bytes, max_seconds: int) -> float | None:
    if not _complete_ogg(audio):
        return None
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-xerror", "-err_detect", "explode",
        "-f", "ogg", "-c:a", "opus", "-i", "pipe:0", "-map", "0:a:0",
        "-t", str(max_seconds + 1), "-ac", "1", "-ar", "8000",
        "-f", "s16le", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(15):
            samples, _ = await process.communicate(audio)
    except BaseException:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        raise
    if process.returncode != 0 or not samples:
        return None
    return len(samples) / 16000
