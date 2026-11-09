from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from shorts.config import Settings, binary
from shorts.database import Database
from shorts.models import JobConfig
from shorts.render import render_video
from shorts.service import publish_existing, submit_job
from shorts.studio import store_video_upload
from shorts.voice import Word, write_captions


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = replace(Settings.from_env(), work_dir=self.root,
                                database=self.root / "jobs.sqlite3")
        self.db = Database(self.settings.database)

    def test_uploaded_video_is_validated_and_content_addressed(self):
        fake = {"format": {"duration": "8"},
                "streams": [{"codec_type": "video", "width": 640, "height": 360}]}
        data = b"a" * 2000
        with patch("shorts.studio.probe", return_value=fake):
            first = store_video_upload(io.BytesIO(data), "source.MP4", self.settings)
            second = store_video_upload(io.BytesIO(data), "source.MP4", self.settings)
        self.assertEqual(first, second)
        self.assertEqual(first.read_bytes(), data)
        self.assertFalse(list((self.root / "uploads").glob("*.partial")))
        with self.assertRaisesRegex(ValueError, "MP4"):
            store_video_upload(io.BytesIO(data), "archive.zip", self.settings)

    def test_footage_start_requires_local_video(self):
        with self.assertRaisesRegex(ValueError, "requires a local"):
            JobConfig(topic="Test", script_text="Words for the narration here.",
                      footage_start_seconds=4).validate()

    def test_studio_and_cli_share_idempotent_submission(self):
        config = JobConfig(topic="Test", script_text="Words for the narration here.")
        with patch("shorts.service.run_job", side_effect=lambda job_id, db, settings: db.job(job_id)) as runner:
            first, created = submit_job(config, self.db, self.settings)
            second, repeated = submit_job(config, self.db, self.settings)
            fresh, new = submit_job(config, self.db, self.settings, new=True)
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertTrue(new)
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["id"], fresh["id"])
        self.assertEqual(runner.call_count, 2)

    def test_manual_publish_rejects_short_facebook_video_and_releases_lease(self):
        config = JobConfig(topic="Test", script_text="Words for the narration here.")
        job_id, _ = self.db.create_job(config, config.fingerprint())
        for status in ("generating_script", "generating_audio", "generating_captions",
                       "retrieving_footage", "rendering", "validating", "ready_to_publish"):
            self.db.transition(job_id, status)
        self.db.save_artifacts(job_id, self.root / "video.mp4",
                               {"title": "Test", "description": "Test",
                                "validation": {"duration_seconds": 3.5}})
        with self.assertRaisesRegex(ValueError, "4-60"):
            publish_existing(job_id, self.db, self.settings, platforms=("facebook",))
        self.assertIsNone(self.db.job(job_id)["lease_owner"])
        self.assertEqual(self.db.job(job_id)["publications"], [])

    def test_missing_publish_credentials_leave_reviewed_job_ready(self):
        config = JobConfig(topic="Test", script_text="Words for the narration here.")
        job_id, _ = self.db.create_job(config, config.fingerprint())
        for status in ("generating_script", "generating_audio", "generating_captions",
                       "retrieving_footage", "rendering", "validating", "ready_to_publish"):
            self.db.transition(job_id, status)
        self.db.save_artifacts(job_id, self.root / "video.mp4",
                               {"title": "Test", "description": "Test",
                                "validation": {"duration_seconds": 10}})
        self.db.transition(job_id, "completed")
        with self.assertRaisesRegex(RuntimeError, "YouTube token"):
            publish_existing(job_id, self.db,
                             replace(self.settings, youtube_token=self.root / "missing-youtube-token.json"),
                             platforms=("youtube",))
        self.assertEqual(self.db.job(job_id)["status"], "completed")
        self.assertEqual(self.db.job(job_id)["publications"], [])

    def test_actual_render_starts_at_selected_footage_time(self):
        try:
            ffmpeg = binary("ffmpeg", "FFMPEG_PATH")
            binary("ffprobe", "FFPROBE_PATH")
        except RuntimeError:
            self.skipTest("FFmpeg is not installed")
        settings = replace(self.settings, width=360, height=640, preset="ultrafast")
        red = self.root / "red.mp4"
        blue = self.root / "blue.mp4"
        for color, path in (("red", red), ("blue", blue)):
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", f"color=c={color}:s=360x640:r=24:d=2", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
        concat = self.root / "source.txt"
        concat.write_text(f"file '{red.name}'\nfile '{blue.name}'\n", encoding="utf-8")
        source = self.root / "source.mp4"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(concat), "-c", "copy", "-y", str(source)], check=True)
        audio = self.root / "voice.wav"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=3", "-y", str(audio)], check=True)
        srt, ass = self.root / "captions.srt", self.root / "captions.ass"
        write_captions((Word("Hello", 0.1, 0.4),), 3, srt, ass, 360, 640)
        output = render_video([source], audio, ass, 3, self.root, settings,
                              footage_start_seconds=2.1)
        frame = self.root / "frame.png"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.5",
                        "-i", str(output), "-frames:v", "1", "-y", str(frame)], check=True)
        pixel = Image.open(frame).convert("RGB").getpixel((180, 100))
        self.assertGreater(pixel[2], pixel[0] * 2, f"expected blue footage, got {pixel}")


if __name__ == "__main__":
    unittest.main()
