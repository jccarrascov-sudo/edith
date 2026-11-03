"""Phase 5: compile down to the network level, and repair by intent when the portal changes.

Real portal processes (v1, v2, v3) and a real headless browser; no model calls."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))

from edith.workshop import build, codegen, network, repair, sandbox  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workshop import OTHER_ROWS, ROW, USER_RECORD, plan_and_code  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def start_portal(version: str) -> tuple[subprocess.Popen, str]:
    port = sandbox._free_port()
    db = Path(tempfile.mkdtemp()) / "base.db"
    proc = subprocess.Popen([sys.executable, str(REPO / "demo" / "portal" / "app.py"), "--version", version,
                             "--db", str(db), "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(150):
        try:
            sandbox.portal_info(url)
            break
        except sandbox.NoSandbox:
            time.sleep(0.1)
    return proc, url


class Portals:
    procs: dict = {}
    urls: dict = {}

    @classmethod
    def up(cls, version: str) -> str:
        if version not in cls.urls:
            cls.procs[version], cls.urls[version] = start_portal(version)
        return cls.urls[version]

    @classmethod
    def down(cls) -> None:
        for p in cls.procs.values():
            p.terminate()
            p.wait(timeout=5)
        cls.procs.clear()
        cls.urls.clear()


def tearDownModule():
    Portals.down()


def cases(plan):
    return build.cases_for(plan, [ROW] + OTHER_ROWS, ROW, USER_RECORD, extra=2)


class RelocateTests(unittest.TestCase):
    """Which field a block moves to, from the fields each portal version really shows."""

    def fields(self, version):
        return repair.form_fields(Portals.up(version))

    def test_nothing_to_do_on_the_same_portal(self):
        plan, _ = plan_and_code()
        self.assertEqual(repair.relocate(plan, self.fields("v1"), log_fn=lambda *_: None), [])

    def test_v2_moved_and_renamed_amount_is_relocated_alone(self):
        plan, _ = plan_and_code()
        [r] = repair.relocate(plan, self.fields("v2"), log_fn=lambda *_: None)
        self.assertEqual((r.block_id, r.old_label, r.new_label), ("b5", "Amount (PEN)", "Total due (PEN)"))

    def test_v3_two_amount_fields_is_ambiguous_and_never_guessed(self):
        plan, _ = plan_and_code()
        [r] = repair.relocate(plan, self.fields("v3"), client=object(), log_fn=lambda *_: None)
        self.assertIsNone(r.new_label)  # not even the model is asked: two fields share "amount"
        self.assertEqual(sorted(r.candidates), ["Amount - subtotal (PEN)", "Amount - total (PEN)"])

    def test_apply_changes_only_the_relocated_block(self):
        plan, code = plan_and_code()
        r = repair.Relocation("b5", "Amount", "Amount (PEN)", "Total due (PEN)")
        new_plan, new_code = repair.apply(plan, code, [r])
        self.assertIn('"Total due (PEN)"', new_code["b5"])
        self.assertEqual({k: v for k, v in new_code.items() if k != "b5"}, {k: v for k, v in code.items() if k != "b5"})
        self.assertIn("Total due (PEN)", new_plan["demo"]["values"])
        self.assertNotIn("Amount (PEN)", new_plan["demo"]["values"])
        self.assertEqual(plan["blocks"][4]["field"]["label"], "Amount (PEN)")  # the original is untouched

    def test_words_ignore_units(self):
        self.assertEqual(repair.words("Total due (PEN)"), {"total", "due"})
        self.assertEqual(repair.words("Amount - subtotal (PEN)"), {"amount", "subtotal"})

    def test_repaired_machine_passes_verification_on_v2(self):
        plan, code = plan_and_code()
        plan["target"] = {"url": Portals.up("v2")}
        rel = repair.relocate(plan, self.fields("v2"), log_fn=lambda *_: None)
        plan, code = repair.apply(plan, code, rel)
        problems, stamp = build.verify(plan, code, cases(plan))
        self.assertEqual(problems, [])
        self.assertEqual(build.stamp_counts(stamp), (33, 33))


class NetworkTests(unittest.TestCase):
    def test_allow_list(self):
        self.assertTrue(network.allowed("http://127.0.0.1:8765/", ["127.0.0.1"]))
        self.assertTrue(network.allowed("https://app.example.com/x", ["example.com"]))
        self.assertFalse(network.allowed("https://evil-example.com/", ["example.com"]))
        self.assertFalse(network.allowed("http://127.0.0.1/", []))

    def test_hidden_inputs_are_read(self):
        h = network._Hidden()
        h.feed('<form><input type="hidden" name="csrf" value="t0k"><input name="client"></form>')
        self.assertEqual(h.values, {"csrf": "t0k"})

    def test_capture_compiles_the_form_request_and_it_verifies(self):
        plan, code = plan_and_code()
        plan["target"] = {"url": Portals.up("v1")}
        with sandbox.PortalSandbox(plan["target"]["url"]) as sb:
            spec = network.capture(plan, code, ROW, sb.url)
        self.assertEqual((spec["method"], spec["action"], spec["success"]),
                         ("POST", "/invoices", {"status": 303, "location_path": "/"}))
        self.assertEqual({f["name"]: f.get("block") for f in spec["fields"]},
                         {"client": "b2", "tax_id": "b3", "date": "b4", "amount": "b5", "description": "b6"})
        texts, numbers = codegen.data_values([ROW] + OTHER_ROWS)
        network.check_spec(spec, texts, numbers)
        problems, stamp = build.verify(plan, code, cases(plan), spec=spec)
        self.assertEqual(problems, [])
        self.assertEqual(build.stamp_counts(stamp), (33, 33))

    def test_a_data_value_as_a_constant_is_rejected(self):
        texts, numbers = codegen.data_values([ROW])
        spec = {"fields": [{"name": "client", "const": "Paper Moth Bakery"}]}
        with self.assertRaises(codegen.UnsafeCode):
            network.check_spec(spec, texts, numbers)

    def test_old_request_fails_its_proof_when_the_portal_changes(self):
        plan, code = plan_and_code()
        plan["target"] = {"url": Portals.up("v1")}
        with sandbox.PortalSandbox(plan["target"]["url"]) as sb:
            spec = network.capture(plan, code, ROW, sb.url)
        plan["target"] = {"url": Portals.up("v2")}  # v2 expects total_due, not amount
        problems, _ = build.verify(plan, code, cases(plan)[:1], spec=spec)
        self.assertTrue(problems)
        self.assertIn("did not confirm the save", problems[0][1])


if __name__ == "__main__":
    unittest.main()
