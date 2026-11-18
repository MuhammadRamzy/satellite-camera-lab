import hashlib
import sqlite3
import uuid
from pathlib import Path
import tempfile
import unittest
from foundations.catalogue import Catalogue

# Deliberately a JPEG framing fixture, not a decoded photograph. These tests
# verify persistence only; no camera/image-processing acceptance is implied.
JPEG = b"\xff\xd8" + bytes(range(256)) * 8 + b"\xff\xd9"
META = dict(width=640, height=480, quality=75, spacecraft_time_us=123,
            processing_duration_ms=2.5)


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cat = Catalogue(self.temp.name)
        self.addCleanup(lambda: self.cat.close())

    def restart(self):
        self.cat.close()
        self.cat = Catalogue(self.temp.name)

    def test_persist_restart_and_ground_confirmation(self):
        image = self.cat.store(JPEG, META)
        self.restart()
        row = self.cat.list()[0]
        self.assertEqual(row["id"], image)
        self.assertEqual(row["status"], "COMPLETE")
        self.assertEqual(row["downlink_status"], "PENDING")
        self.assertIn("storage_duration_ms", row["metadata"])
        with self.assertRaises(ValueError):
            self.cat.delete(image)
        with self.assertRaises(ValueError):
            self.cat.mark_delivered(image, "0" * 64)
        self.cat.mark_delivered(image, hashlib.sha256(JPEG).hexdigest())
        self.cat.delete(image)
        self.restart()
        self.assertEqual(self.cat.list(), [])
        self.assertFalse(list(Path(self.temp.name).glob("*.jpg")))

    def test_crash_windows(self):
        for stage in ("after_temp_fsync", "after_rename"):
            def fault(current):
                if stage == current:
                    raise RuntimeError("simulated process interruption")
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                self.cat.store(JPEG, META, fault)
            self.restart()
            for row in self.cat.list():
                self.assertEqual(row["status"], "COMPLETE")
                self.assertEqual((Path(self.temp.name) / (row["id"] + ".jpg")).read_bytes(), JPEG)

    def test_quota_exhaustion(self):
        self.cat.quota = len(JPEG) - 1
        with self.assertRaises(OSError) as caught:
            self.cat.store(JPEG, META)
        self.assertEqual(caught.exception.errno, 28)
        self.assertEqual(self.cat.list(), [])

    def test_corrupted_image_retained_and_flagged(self):
        image = self.cat.store(JPEG, META)
        path = Path(self.temp.name) / (image + ".jpg")
        path.write_bytes(b"corrupt")
        self.restart()
        self.assertEqual(self.cat.list()[0]["status"], "CORRUPT")
        self.assertTrue(path.exists())
        with self.assertRaises(ValueError):
            self.cat.mark_delivered(image, hashlib.sha256(JPEG).hexdigest())

    def test_missing_file_flagged(self):
        image = self.cat.store(JPEG, META)
        (Path(self.temp.name) / (image + ".jpg")).unlink()
        self.restart()
        self.assertEqual(self.cat.list()[0]["status"], "MISSING")

    def test_invalid_input_does_not_create_rows(self):
        for data, meta in [(b"bad", META), (JPEG, {}), (JPEG, dict(META, width=0))]:
            with self.assertRaises(ValueError):
                self.cat.store(data, meta)
        with self.assertRaises(ValueError):
            self.cat.delete("../outside")
        self.assertEqual(self.cat.list(), [])

    def test_capture_identity_and_timestamp_preserved(self):
        capture_id = str(uuid.uuid4())
        utc = '2026-11-18T07:00:00.123456+00:00'
        self.assertEqual(self.cat.store(JPEG, dict(META, utc=utc), capture_id=capture_id), capture_id)
        self.assertEqual(self.cat.list()[0]['metadata']['utc'], utc)
        with self.assertRaises(sqlite3.IntegrityError):
            self.cat.store(JPEG, META, capture_id=capture_id)
        with self.assertRaises(ValueError):
            self.cat.store(JPEG, META, capture_id='../outside')
        self.assertEqual(len(self.cat.list()), 1)
        self.assertEqual((Path(self.temp.name) / (capture_id + '.jpg')).read_bytes(), JPEG)
