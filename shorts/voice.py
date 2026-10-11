from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import binary
from .media import duration


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class VoiceResult:
    path: Path
    duration_seconds: float
    words: tuple[Word, ...]
    provider: str = "edge_tts"


async def _synthesize(text: str, voice: str, path: Path, rate: str) -> tuple[Word, ...]:
    import edge_tts

    words: list[Word] = []
    with path.open("wb") as output:
        async for chunk in edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary").stream():
            if chunk["type"] == "audio":
                output.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000_000
                end = start + chunk["duration"] / 10_000_000
                words.append(Word(chunk["text"], start, end))
    return tuple(words)


def _windows_voiceover(text: str, voice: str, path: Path,
                       target_seconds: float | None) -> VoiceResult:
    """Use installed Windows speech when Edge's WebSocket service is unavailable."""
    powershell = shutil.which("powershell") if os.name == "nt" else None
    if not powershell:
        raise RuntimeError("Windows speech fallback is unavailable")
    stem = path.with_suffix("")
    text_file = stem.with_suffix(".sapi.txt")
    wave_file = stem.with_suffix(".sapi.wav")
    timing_file = stem.with_suffix(".sapi.json")
    partial = path.with_suffix(".partial.mp3")
    male_names = ("guy", "david", "christopher", "eric", "roger", "ryan", "tony")
    system_voice = "Microsoft David Desktop" if any(name in voice.casefold() for name in male_names) else "Microsoft Zira Desktop"
    try:
        text_file.write_text(text, encoding="utf-8")
        subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-File", str(Path(__file__).with_name("sapi_voice.ps1")),
                        "-TextFile", str(text_file), "-AudioFile", str(wave_file),
                        "-TimingFile", str(timing_file), "-VoiceName", system_voice],
                       check=True, capture_output=True, timeout=120)
        source_seconds = duration(wave_file)
        if source_seconds < 1:
            raise RuntimeError("Windows speech returned empty audio")
        tempo = 1.0
        if target_seconds and abs(source_seconds - target_seconds) / target_seconds > 0.15:
            tempo = source_seconds / target_seconds
            if not 0.65 <= tempo <= 1.35:
                raise ValueError(f"Narration is {source_seconds:.1f}s but target is {target_seconds:.1f}s. Adjust the script length.")
        subprocess.run([binary("ffmpeg", "FFMPEG_PATH"), "-hide_banner", "-loglevel", "error",
                        "-i", str(wave_file), "-filter:a", f"atempo={tempo:.5f}",
                        "-codec:a", "libmp3lame", "-q:a", "3", "-y", str(partial)],
                       check=True, capture_output=True, timeout=120)
        seconds = duration(partial)
        raw = json.loads(timing_file.read_text(encoding="utf-8-sig"))
        events = raw if isinstance(raw, list) else [raw]
        starts = [max(0.0, float(item["start"]) / tempo) for item in events]
        words = tuple(Word(str(item["text"]), start,
                           min(seconds, max(start + 0.12,
                                            starts[index + 1] if index + 1 < len(starts) else seconds)))
                      for index, (item, start) in enumerate(zip(events, starts)))
        if not words or (target_seconds and abs(seconds - target_seconds) / target_seconds > 0.2):
            raise RuntimeError("Windows speech did not produce usable timing or duration")
        partial.replace(path)
        return VoiceResult(path, seconds, words, "windows_sapi")
    finally:
        for file in (text_file, wave_file, timing_file, partial):
            file.unlink(missing_ok=True)


def make_voiceover(text: str, voice: str, path: Path, target_seconds: float | None = None) -> VoiceResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial.mp3")
    temporary.unlink(missing_ok=True)
    try:
        words = asyncio.run(_synthesize(text, voice, temporary, "+0%"))
        seconds = duration(temporary)
        if seconds < 1 or not words:
            raise RuntimeError("edge-tts returned empty audio or no word timing")
        if target_seconds and abs(seconds - target_seconds) / target_seconds > 0.15:
            rate_percent = round((seconds / target_seconds - 1) * 100)
            if not -35 <= rate_percent <= 35:
                raise ValueError(f"Narration is {seconds:.1f}s but target is {target_seconds:.1f}s. Adjust the script length.")
            temporary.unlink(missing_ok=True)
            words = asyncio.run(_synthesize(text, voice, temporary, f"{rate_percent:+d}%"))
            seconds = duration(temporary)
            if not words or abs(seconds - target_seconds) / target_seconds > 0.2:
                raise ValueError(f"Narration remains {seconds:.1f}s after rate adjustment; adjust the script length.")
        temporary.replace(path)
        return VoiceResult(path, seconds, words)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, ValueError) or (isinstance(exc, RuntimeError) and "edge-tts returned" in str(exc)):
            raise
        if os.name == "nt" and voice.lower().startswith("en-"):
            try:
                result = _windows_voiceover(text, voice, path, target_seconds)
                log.warning("Edge TTS failed (%s); used installed Windows speech voice", type(exc).__name__)
                return result
            except ValueError as fallback_exc:
                raise fallback_exc from exc
            except Exception as fallback_exc:
                log.warning("Windows speech fallback failed: %s", type(fallback_exc).__name__)
        raise RuntimeError(f"Voice synthesis failed for {voice}. Check network access or run 'python -m edge_tts --list-voices': {type(exc).__name__}") from exc


def _timecode(seconds: float, separator: str, precision: int) -> str:
    scale = 10 ** precision
    ticks = max(0, round(seconds * scale))
    hour, ticks = divmod(ticks, 3600 * scale)
    minute, ticks = divmod(ticks, 60 * scale)
    second, fraction = divmod(ticks, scale)
    return f"{hour:02}:{minute:02}:{second:02}{separator}{fraction:0{precision}d}"


def write_captions(words: tuple[Word, ...], audio_seconds: float, srt_path: Path,
                   ass_path: Path, width: int, height: int, narration: str = "") -> int:
    if not words:
        raise ValueError("cannot create captions without word timing")
    if narration:
        tokens = narration.split()
        matched: list[Word] = []
        cursor = 0
        for word in words:
            normalized = re.sub(r"\W", "", word.text, flags=re.UNICODE).casefold()
            replacement = word.text
            for index in range(cursor, min(len(tokens), cursor + 4)):
                candidate = re.sub(r"\W", "", tokens[index], flags=re.UNICODE).casefold()
                if candidate == normalized:
                    replacement = tokens[index]
                    cursor = index + 1
                    break
            matched.append(Word(replacement, word.start, word.end))
        words = tuple(matched)
    groups: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if current and (len(current) >= 6 or word.end - current[0].start > 2.5 or word.start - current[-1].end > 0.6):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    srt: list[str] = []
    ass: list[str] = []
    for index, group in enumerate(groups, 1):
        start = max(0, group[0].start)
        end = min(audio_seconds, max(group[-1].end, start + 0.15))
        if end <= start:
            continue
        line = " ".join(word.text.strip() for word in group).strip()
        srt.append(f"{index}\n{_timecode(start, ',', 3)} --> {_timecode(end, ',', 3)}\n{line}\n")
        safe = line.replace("{", "(").replace("}", ")").replace("\\", "")
        ass.append(f"Dialogue: 0,{_timecode(start, '.', 2)},{_timecode(end, '.', 2)},Default,,0,0,0,,{safe}")
    if not srt:
        raise ValueError("word timing did not produce any captions")
    srt_path.write_text("\n".join(srt), encoding="utf-8")
    font_size = max(24, round(height * 0.045))
    margin = max(40, round(height * 0.16))
    header = ("[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
              f"PlayResX: {width}\nPlayResY: {height}\n\n"
              "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
              f"Style: Default,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,40,40,{margin},1\n\n"
              "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ass_path.write_text(header + "\n".join(ass) + "\n", encoding="utf-8-sig")
    return len(ass)
