from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import JobConfig, STAGES, utc_now


_ALLOWED: dict[str, set[str]] = {
    "queued": {"generating_script", "cancelled", "failed"},
    "generating_script": {"generating_audio", "failed"},
    "generating_audio": {"generating_captions", "failed"},
    "generating_captions": {"retrieving_footage", "failed"},
    "retrieving_footage": {"rendering", "failed"},
    "rendering": {"validating", "failed"},
    "validating": {"ready_to_publish", "failed"},
    "ready_to_publish": {"publishing", "completed", "cancelled", "failed"},
    "publishing": {"completed", "partially_completed", "failed", "ready_to_publish"},
    "partially_completed": {"publishing", "completed", "cancelled"},
    "completed": {"publishing"},
    "failed": {"generating_script", "publishing", "cancelled"},
    "cancelled": set(),
}
for _stage in ("generating_audio", "generating_captions", "retrieving_footage", "rendering", "validating"):
    _ALLOWED[_stage].add("generating_script")


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    topic TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    publish_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    scheduled_at TEXT,
                    output_path TEXT,
                    metadata_json TEXT,
                    error TEXT,
                    lease_owner TEXT,
                    lease_expires TEXT
                );
                CREATE TABLE IF NOT EXISTS publications (
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    external_id TEXT,
                    url TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    error TEXT,
                    remote_state_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (job_id, platform)
                );
                CREATE INDEX IF NOT EXISTS jobs_schedule ON jobs(status, scheduled_at);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "publish_mode" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN publish_mode TEXT NOT NULL DEFAULT 'generate_only'")
                for row in db.execute("SELECT id,config_json FROM jobs"):
                    mode = json.loads(row["config_json"]).get("publish_mode", "generate_only")
                    db.execute("UPDATE jobs SET publish_mode=? WHERE id=?", (mode, row["id"]))

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_job(self, config: JobConfig, fingerprint: str) -> tuple[str, bool]:
        job_id = uuid.uuid4().hex
        now = utc_now()
        scheduled = config.scheduled_at
        if scheduled:
            scheduled = datetime.fromisoformat(scheduled.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id,config_json FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()
            if existing:
                if json.loads(existing["config_json"]) != json.loads(json.dumps(config.to_dict(), ensure_ascii=False)):
                    raise ValueError("idempotency key was already used with different job settings")
                return existing["id"], False
            db.execute("INSERT INTO jobs (id,fingerprint,topic,config_json,publish_mode,status,created_at,updated_at,scheduled_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (job_id, fingerprint, config.topic, json.dumps(config.to_dict(), ensure_ascii=False), config.publish_mode,
                        "queued", now, now, scheduled))
            db.executemany("INSERT INTO publications (job_id,platform) VALUES (?,?)", [(job_id, p) for p in config.platforms])
        return job_id, True

    def job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"job not found: {job_id}")
            result = dict(row)
            result["config"] = json.loads(result.pop("config_json"))
            result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
            result["publications"] = [self._publication(row) for row in db.execute("SELECT * FROM publications WHERE job_id=? ORDER BY platform", (job_id,))]
            return result

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,topic,status,created_at,scheduled_at,output_path,error FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))]

    @staticmethod
    def _publication(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["remote_state"] = json.loads(result.pop("remote_state_json") or "{}")
        return result

    def publication(self, job_id: str, platform: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM publications WHERE job_id=? AND platform=?", (job_id, platform)).fetchone()
            if row is None:
                raise KeyError(f"platform {platform} is not selected for job {job_id}")
            return self._publication(row)

    def transition(self, job_id: str, status: str, error: str | None = None) -> None:
        if status not in STAGES:
            raise ValueError(f"unknown job status: {status}")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"job not found: {job_id}")
            if row["status"] != status and status not in _ALLOWED[row["status"]]:
                raise ValueError(f"invalid job transition: {row['status']} -> {status}")
            db.execute("UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?", (status, error, utc_now(), job_id))

    def save_artifacts(self, job_id: str, path: Path, metadata: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("UPDATE jobs SET output_path=?,metadata_json=?,updated_at=? WHERE id=?",
                       (str(path), json.dumps(metadata, ensure_ascii=False), utc_now(), job_id))

    def lease(self, job_id: str, owner: str, minutes: int = 120) -> bool:
        now = datetime.now(timezone.utc)
        expiry = (now + timedelta(minutes=minutes)).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT lease_owner,lease_expires,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] == "cancelled":
                return False
            if row["lease_owner"] and row["lease_expires"] and row["lease_expires"] > now.isoformat():
                return False
            db.execute("UPDATE jobs SET lease_owner=?,lease_expires=? WHERE id=?", (owner, expiry, job_id))
            return True

    def release(self, job_id: str, owner: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE jobs SET lease_owner=NULL,lease_expires=NULL WHERE id=? AND lease_owner=?", (job_id, owner))

    def due_jobs(self, limit: int = 20) -> list[str]:
        now = utc_now()
        with self.connect() as db:
            rows = db.execute("""
                SELECT id FROM jobs
                WHERE (lease_expires IS NULL OR lease_expires < ?)
                AND (status='queued' OR status IN ('generating_script','generating_audio','generating_captions','retrieving_footage','rendering','validating')
                    OR (status IN ('ready_to_publish','partially_completed','publishing') AND scheduled_at IS NOT NULL AND scheduled_at <= ?)
                    OR (status IN ('ready_to_publish','publishing') AND publish_mode='immediate')
                    OR (status IN ('ready_to_publish','partially_completed','publishing','completed') AND EXISTS
                        (SELECT 1 FROM publications p WHERE p.job_id=jobs.id AND p.status='processing')))
                ORDER BY created_at LIMIT ?
            """, (now, now, limit)).fetchall()
            return [row["id"] for row in rows]

    def claim_publication(self, job_id: str, platform: str, force_unknown: bool = False) -> dict[str, Any] | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM publications WHERE job_id=? AND platform=?", (job_id, platform)).fetchone()
            if row is None:
                raise KeyError(f"platform {platform} is not selected for job {job_id}")
            current = self._publication(row)
            if current["status"] == "succeeded":
                return None
            if current["status"] == "needs_review" and not force_unknown:
                return None
            if current["status"] == "attempting" and not current["external_id"] and not force_unknown:
                db.execute("UPDATE publications SET status='needs_review',error=? WHERE job_id=? AND platform=?",
                           ("Previous upload outcome is unknown. Check the platform before retrying; then use --force-unknown.", job_id, platform))
                return None
            db.execute("UPDATE publications SET status='attempting',attempts=attempts+?,last_attempt_at=?,error=NULL WHERE job_id=? AND platform=?",
                       (0 if current["status"] == "processing" else 1, utc_now(), job_id, platform))
            if current["status"] != "processing":
                current["attempts"] += 1
            current["status"] = "attempting"
            return current

    def add_platforms(self, job_id: str, platforms: tuple[str, ...]) -> None:
        from .models import PLATFORMS
        if any(platform not in PLATFORMS for platform in platforms):
            raise ValueError("unknown publishing platform")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT config_json,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"job not found: {job_id}")
            if row["status"] not in {"ready_to_publish", "completed", "partially_completed", "failed"}:
                raise ValueError("platforms can only be added after generation")
            db.executemany("INSERT OR IGNORE INTO publications (job_id,platform) VALUES (?,?)",
                           [(job_id, platform) for platform in platforms])
            db.execute("UPDATE jobs SET updated_at=? WHERE id=?", (utc_now(), job_id))

    def update_publication(self, job_id: str, platform: str, *, status: str, external_id: str | None = None,
                           url: str | None = None, error: str | None = None, remote_state: dict[str, Any] | None = None) -> None:
        if status not in {"pending", "attempting", "processing", "succeeded", "failed", "needs_review"}:
            raise ValueError(f"unknown publication status: {status}")
        with self.connect() as db:
            row = db.execute("SELECT * FROM publications WHERE job_id=? AND platform=?", (job_id, platform)).fetchone()
            if row is None:
                raise KeyError(f"platform {platform} is not selected for job {job_id}")
            previous = self._publication(row)
            db.execute("""UPDATE publications SET status=?,external_id=?,url=?,error=?,remote_state_json=?
                          WHERE job_id=? AND platform=?""",
                       (status, external_id if external_id is not None else previous["external_id"],
                        url if url is not None else previous["url"], error,
                        json.dumps(remote_state if remote_state is not None else previous["remote_state"]), job_id, platform))

    def cancel(self, job_id: str) -> None:
        job = self.job(job_id)
        if job["status"] not in {"queued", "ready_to_publish", "failed"}:
            raise ValueError(f"cannot cancel job in state {job['status']}")
        if any(p["status"] == "succeeded" for p in job["publications"]):
            raise ValueError("cannot cancel a job that has already published to a platform")
        self.transition(job_id, "cancelled")
