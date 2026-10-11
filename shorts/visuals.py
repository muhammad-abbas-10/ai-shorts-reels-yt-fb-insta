from __future__ import annotations

import hashlib
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import Settings


_STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in",
         "into", "is", "it", "of", "on", "or", "our", "the", "their", "these", "this",
         "to", "under", "using", "with", "your", "video", "clip", "footage", "stock",
         "vertical", "background", "scene", "visual", "beautiful", "cinematic", "animated",
         "animation"}
_WEAK = {"ai", "artificial", "intelligence", "data", "digital", "technology", "tech",
         "process", "abstract", "generated", "nature", "people", "person"}
_ALIASES = {
    "chat": "chatbot", "chatbot": "chatbot", "chatgpt": "chatbot",
    "computer": "computer", "laptop": "computer", "pc": "computer",
    "dashboard": "screen", "display": "screen", "interface": "screen", "monitor": "screen",
    "screen": "screen", "ui": "screen",
    "document": "document", "file": "document", "paper": "document", "pdf": "document",
    "retrieve": "search", "retrieval": "search", "search": "search",
    "database": "database", "server": "database",
    "dawn": "morning", "morning": "morning", "sunrise": "morning",
    "night": "night", "nighttime": "night",
    "cosmos": "space", "galaxy": "space", "space": "space", "star": "space",
    "ocean": "ocean", "sea": "ocean",
    "read": "study", "student": "study", "study": "study",
    "chart": "chart", "graph": "chart", "visualization": "chart",
}


def _root(word: str) -> str:
    word = word.casefold()
    if len(word) > 5 and word.endswith("ying"):
        return word[:-4] + "y"
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def visual_terms(text: str) -> set[str]:
    terms = set()
    for raw in re.findall(r"\w+", text.casefold(), flags=re.UNICODE):
        word = _root(raw)
        if word not in _STOP:
            terms.add(_ALIASES.get(word, word))
    return terms


def query_fits_story(query: str, topic: str, narration: str) -> bool:
    subject = visual_terms(query) - _WEAK
    return bool(subject & visual_terms(f"{topic} {narration}"))


def stock_relevance(query: str, description: str) -> int:
    if "green screen" in description.casefold() or "chroma key" in description.casefold():
        return 0
    requested = visual_terms(query)
    found = visual_terms(description)
    strong_matches = (requested - _WEAK) & found
    needed = min(2, len(requested - _WEAK))
    if len(strong_matches) < needed or not needed:
        return 0
    return 3 * len(strong_matches) + len((requested & _WEAK) & found)


def _font(size: int, *, bold: bool = False):
    names = ("arialbd.ttf" if bold else "arial.ttf",
             "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    paths = (Path("C:/Windows/Fonts") / names[0],
             Path("/usr/share/fonts/truetype/dejavu") / names[1])
    for path in paths:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int,
          max_lines: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textbbox((0, 0), lines[-1] + "...", font=font)[2] > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "..."
    return lines


def story_beats(narration: str, count: int, topic: str) -> list[str]:
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", narration)
                 if item.strip()]
    if len(sentences) < count:
        words = narration.split()
        if not words:
            return [topic] * count
        return [" ".join(words[round(i * len(words) / count):
                               round((i + 1) * len(words) / count)]) or topic
                for i in range(count)]
    return [" ".join(sentences[round(i * len(sentences) / count):
                               round((i + 1) * len(sentences) / count)])
            for i in range(count)]


def make_concept_cards(topic: str, narration: str, queries: tuple[str, ...],
                       job_dir: Path, settings: Settings) -> list[Path]:
    count = max(1, len(queries))
    beats = story_beats(narration, count, topic)
    width, height = settings.width, settings.height
    scale = width / 720
    px = lambda value: round(value * scale)
    palette = ("#74d6dc", "#b3dd89", "#dda7ef", "#e8bb86")
    accent = palette[hashlib.sha256(topic.encode("utf-8")).digest()[0] % len(palette)]
    result: list[Path] = []
    for index, beat in enumerate(beats):
        image = Image.new("RGB", (width, height), "#0b1724")
        draw = ImageDraw.Draw(image)
        for y in range(height):
            depth = y / height
            draw.line((0, y, width, y), fill=(round(11 + 8 * depth),
                                              round(23 + 10 * depth),
                                              round(36 + 13 * depth)))
        for x in range(px(30), width, px(48)):
            draw.line((x, px(315), x, px(915)), fill="#1e3544")
        for y in range(px(320), px(915), px(48)):
            draw.line((px(28), y, width - px(28), y), fill="#1e3544")
        draw.rounded_rectangle((px(55), px(62), px(249), px(106)), radius=px(20),
                               fill="#23404d")
        draw.text((px(73), px(73)), "TOPIC VISUAL", font=_font(px(17), bold=True),
                  fill=accent)
        title_font = _font(px(45), bold=True)
        for line_number, line in enumerate(_wrap(draw, topic, title_font, px(610), 2)):
            draw.text((px(55), px(147 + line_number * 57)), line, font=title_font,
                      fill="#f1f8f8")
        draw.text((px(57), px(278)), f"IDEA {index + 1:02} / {count:02}",
                  font=_font(px(21), bold=True), fill=accent)
        draw.rounded_rectangle((px(52), px(350), px(668), px(820)), radius=px(28),
                               fill="#17303e", outline=accent, width=max(2, px(3)))
        draw.ellipse((px(567), px(378), px(624), px(435)), fill=accent)
        draw.text((px(90), px(405)), "FROM THE NARRATION", font=_font(px(19), bold=True),
                  fill="#a9c4cd")
        main_font = _font(px(39), bold=True)
        lines = _wrap(draw, beat, main_font, px(530), 6)
        for line_number, line in enumerate(lines):
            draw.text((px(90), px(470 + line_number * 54)), line, font=main_font,
                      fill="#f1f8f8")
        for marker in range(count):
            draw.rounded_rectangle((px(55 + marker * 58), px(879),
                                    px(99 + marker * 58), px(886)), radius=px(3),
                                   fill=accent if marker == index else "#3b5866")
        path = job_dir / f"concept_{index + 1:02}.png"
        image.save(path, optimize=True)
        result.append(path)
    return result
