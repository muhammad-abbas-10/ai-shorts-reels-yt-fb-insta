from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shorts.voice import make_voiceover


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell"),
                     "installed Windows speech is required")
class WindowsVoiceFallbackTests(unittest.TestCase):
    def test_edge_failure_uses_local_voice_with_word_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "voice.mp3"
            with patch("shorts.voice._synthesize", side_effect=ConnectionError("Edge unavailable")):
                result = make_voiceover("A calm morning begins with one small step.",
                                        "en-US-AriaNeural", path, 3)
            self.assertEqual(result.provider, "windows_sapi")
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 1_000)
            self.assertGreaterEqual(len(result.words), 7)
            self.assertLess(result.words[0].start, result.words[-1].start)

    def test_fallback_can_adjust_tempo_and_word_positions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "voice.mp3"
            with patch("shorts.voice._synthesize", side_effect=ConnectionError("Edge unavailable")):
                result = make_voiceover("A calm morning begins with one small step.",
                                        "en-US-AriaNeural", path, 4)
            self.assertEqual(result.provider, "windows_sapi")
            self.assertLess(abs(result.duration_seconds - 4), 0.8)
            self.assertLessEqual(result.words[-1].end, result.duration_seconds)
