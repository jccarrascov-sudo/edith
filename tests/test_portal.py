"""Invoice Portal v1/v2/v3 over real HTTP (uvicorn in a thread, temporary databases)."""

from __future__ import annotations

import json
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo" / "portal"))
sys.path.insert(0, str(ROOT / "demo"))

import uvicorn  # noqa: E402

import make_invoices  # noqa: E402
from app import create_app, reset_db  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Portal:
    def __init__(self, version: str, db: Path):
        self.port = free_port()
        self.db = db
        config = uvicorn.Config(create_app(version, db), host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return
            time.sleep(0.05)
        raise RuntimeError("portal did not start")

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str) -> str:
        with urllib.request.urlopen(self.url(path)) as r:
            return r.read().decode()

    def post(self, fields: dict) -> tuple[int, str, str]:
        data = urllib.parse.urlencode(fields).encode()
        with urllib.request.urlopen(urllib.request.Request(self.url("/invoices"), data=data)) as r:
            return r.status, r.url, r.read().decode()

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


class PortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="portal-test-"))
        cls.v1 = Portal("v1", cls.tmp / "v1.db")
        cls.v2 = Portal("v2", cls.tmp / "v2.db")
        cls.v3 = Portal("v3", cls.tmp / "v3.db")

    @classmethod
    def tearDownClass(cls):
        cls.v1.stop()
        cls.v2.stop()
        cls.v3.stop()

    def rows(self, db: Path) -> list[dict]:
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT client, tax_id, date, amount, description FROM invoices")]

    def setUp(self):
        reset_db(self.v1.db)
        reset_db(self.v2.db)
        reset_db(self.v3.db)

    def test_v1_form_order_and_save(self):
        page = self.v1.get("/")
        order = [page.index(f'id="{n}"') for n in ("client", "tax_id", "date", "amount", "description")]
        self.assertEqual(order, sorted(order))
        status, url, body = self.v1.post({"client": "Blue Heron Bakery", "tax_id": "99123456789",
                                          "date": "05/10/2026", "amount": "1,250.50", "description": "Bread"})
        self.assertIn("saved=1", url)
        self.assertIn("Invoice #1 saved.", body)
        self.assertEqual(self.rows(self.v1.db), [{"client": "Blue Heron Bakery", "tax_id": "99123456789",
                                                  "date": "2026-10-05", "amount": 1250.5, "description": "Bread"}])
        api = json.loads(self.v1.get("/api/invoices"))
        self.assertEqual(api[0]["portal_version"], "v1")

    def test_v1_validation_errors_keep_values(self):
        _, url, body = self.v1.post({"client": "X", "tax_id": "123", "date": "05/10/2026",
                                     "amount": "10", "description": "d"})
        self.assertNotIn("saved=", url)
        self.assertIn("exactly 11 digits", body)
        self.assertIn('value="X"', body)
        self.assertEqual(self.rows(self.v1.db), [])

    def test_v2_moves_and_renames_amount(self):
        page = self.v2.get("/")
        self.assertNotIn('id="amount"', page)
        self.assertLess(page.index('id="total_due"'), page.index('id="client"'))
        self.assertIn("Total due (PEN)", page)
        # A v1-style submission (old field name) is rejected in v2...
        _, url, body = self.v2.post({"client": "A", "tax_id": "99123456789", "date": "05/10/2026",
                                     "amount": "10", "description": "d"})
        self.assertIn("Please fill in: amount", body)
        # ...while the v2 field name stores the same columns as v1.
        _, url, _ = self.v2.post({"total_due": "10", "client": "A", "tax_id": "99123456789",
                                  "date": "05/10/2026", "description": "d"})
        self.assertIn("saved=1", url)
        self.assertEqual(self.rows(self.v2.db)[0]["amount"], 10.0)

    def test_v3_has_two_amount_fields_and_total_is_the_amount(self):
        page = self.v3.get("/")
        self.assertIn("Amount - subtotal (PEN)", page)
        self.assertIn("Amount - total (PEN)", page)
        _, url, _ = self.v3.post({"client": "A", "tax_id": "99123456789", "date": "05/10/2026",
                                  "amount_total": "118.00", "description": "d"})
        self.assertIn("saved=1", url)  # the subtotal is optional
        _, url, _ = self.v3.post({"client": "B", "tax_id": "99123456789", "date": "05/10/2026",
                                  "subtotal": "100", "amount_total": "118", "description": "d"})
        api = json.loads(self.v3.get("/api/invoices"))
        self.assertEqual([(r["amount"], r["subtotal"]) for r in api], [(118.0, None), (118.0, 100.0)])

    def test_excel_has_hard_cases(self):
        rows = make_invoices.generate()
        self.assertEqual(rows[0], ["Paper Moth Bakery", "99815188447", make_invoices.date(2026, 9, 10),
                                   4216.96, "Notebooks and pens"])  # the usual demonstration row
        self.assertTrue(any("ñ" in r[0].lower() for r in rows))
        self.assertEqual(sum(isinstance(r[3], str) and "," in r[3] for r in rows), 4)
        self.assertEqual(sum(isinstance(r[2], str) for r in rows), 4)
        self.assertEqual(sum(r[3] is None for r in rows), 1)

    def test_excel_has_30_fictitious_invoices(self):
        rows = make_invoices.generate()
        self.assertEqual(len(rows), 30)
        self.assertEqual(len({r[0] for r in rows}), 30)
        self.assertTrue(all(r[1].startswith("99") and len(r[1]) == 11 for r in rows))
        self.assertEqual(rows, make_invoices.generate())  # deterministic


if __name__ == "__main__":
    unittest.main()
