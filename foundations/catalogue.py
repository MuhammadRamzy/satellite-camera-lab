"""Single-owner durable catalogue with crash recovery and explicit delivery.

SQLite WAL + FULL synchronous and fsynced same-filesystem rename. No callbacks
or bus imports. Fault hooks exist only for deterministic durability tests.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class Catalogue:
    def __init__(self, directory, quota_bytes=256 * 1024 * 1024):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.quota = quota_bytes
        self.db = sqlite3.connect(self.root / "catalogue.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS images (
            id TEXT PRIMARY KEY, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
            status TEXT NOT NULL, downlink_status TEXT NOT NULL,
            metadata TEXT NOT NULL)""")
        self.db.commit()
        self.recover()

    def close(self):
        self.db.close()

    def _sync_dir(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _path(self, image_id, suffix=".jpg"):
        # Reject traversal and non-canonical IDs even if a client bypasses ICD.
        if str(uuid.UUID(image_id)) != image_id:
            raise ValueError("noncanonical image id")
        return self.root / (image_id + suffix)

    def list(self):
        return [dict(row) | {"metadata": json.loads(row["metadata"])}
                for row in self.db.execute("SELECT * FROM images ORDER BY id")]

    def store(self, jpeg, metadata, fault=None, *, capture_id=None):
        if not isinstance(jpeg, bytes) or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("JPEG framing invalid; full image decode belongs to processing node")
        required = {"width", "height", "quality", "spacecraft_time_us", "processing_duration_ms"}
        if required - metadata.keys():
            raise ValueError("required capture metadata absent")
        if not (1 <= metadata["width"] <= 1920 and 1 <= metadata["height"] <= 1080
                and 1 <= metadata["quality"] <= 100
                and metadata["spacecraft_time_us"] >= 0
                and metadata["processing_duration_ms"] >= 0):
            raise ValueError("capture metadata out of bounds")
        image_id = capture_id or str(uuid.uuid4())
        self._path(image_id)
        digest = hashlib.sha256(jpeg).hexdigest()
        meta = dict(metadata, utc=metadata.get("utc", datetime.now(timezone.utc).isoformat()), encoding="jpeg")
        # Enforce quota inside transaction, including all reserved bytes. Quota
        # controls image data only; database/filesystem overhead needs headroom.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            used = self.db.execute("SELECT coalesce(sum(size),0) FROM images WHERE status != 'MISSING'").fetchone()[0]
            if used + len(jpeg) > self.quota:
                raise OSError(28, "image quota exceeded")
            self.db.execute("INSERT INTO images VALUES(?,?,?,?,?,?)",
                            (image_id, len(jpeg), digest, "WRITING", "PENDING", json.dumps(meta, allow_nan=False)))
        started = time.monotonic()
        # Exceptions deliberately leave the durable WRITING intent for recovery.
        with self._path(image_id, ".part").open("xb") as handle:
            handle.write(jpeg)
            handle.flush()
            os.fsync(handle.fileno())
        if fault:
            fault("after_temp_fsync")
        os.replace(self._path(image_id, ".part"), self._path(image_id))
        self._sync_dir()
        if fault:
            fault("after_rename")
        meta["storage_duration_ms"] = (time.monotonic() - started) * 1000
        with self.db:
            self.db.execute("UPDATE images SET status='COMPLETE', metadata=? WHERE id=?",
                            (json.dumps(meta), image_id))
        return image_id

    def recover(self):
        for row in self.db.execute("SELECT * FROM images").fetchall():
            path = self._path(row["id"])
            part = self._path(row["id"], ".part")
            if row["status"] == "DELETING":
                path.unlink(missing_ok=True)
                part.unlink(missing_ok=True)
                self._sync_dir()
                with self.db:
                    self.db.execute("DELETE FROM images WHERE id=?", (row["id"],))
                continue
            # A complete fsynced temporary file can be recovered after a crash.
            candidate = path if path.exists() else part
            if candidate.exists() and candidate.stat().st_size == row["size"] and sha256_file(candidate) == row["sha256"]:
                if candidate == part:
                    os.replace(part, path)
                    self._sync_dir()
                state = "COMPLETE"
            else:
                state = "CORRUPT" if candidate.exists() else "MISSING"
            with self.db:
                self.db.execute("UPDATE images SET status=? WHERE id=?", (state, row["id"]))

    def mark_delivered(self, image_id, ground_sha256):
        self._path(image_id)
        row = self.db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if not row or row["status"] != "COMPLETE" or ground_sha256 != row["sha256"]:
            raise ValueError("ground confirmation does not match complete capture")
        if sha256_file(self._path(image_id)) != ground_sha256:
            raise ValueError("stored image checksum mismatch")
        with self.db:
            self.db.execute("UPDATE images SET downlink_status='DELIVERED' WHERE id=?", (image_id,))

    def delete(self, image_id):
        path = self._path(image_id)
        with self.db:
            row = self.db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
            if not row or row["downlink_status"] != "DELIVERED":
                raise ValueError("cannot delete without confirmed ground delivery")
            self.db.execute("UPDATE images SET status='DELETING' WHERE id=?", (image_id,))
        path.unlink(missing_ok=True)
        self._path(image_id, ".part").unlink(missing_ok=True)
        self._sync_dir()
        with self.db:
            self.db.execute("DELETE FROM images WHERE id=?", (image_id,))
