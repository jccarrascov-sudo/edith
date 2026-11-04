"""Level 3: a web-form recipe around a verified machine, run on the real demo portal (sandbox);
tokens in the Windows Credential Manager; CAPTCHA / two-step pages are left to the user."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from edith.config import load_config  # noqa: E402
from edith.recipes import run, web  # noqa: E402
from edith.recipes.store import RecipeStore  # noqa: E402
from edith.workshop import build, sandbox  # noqa: E402
from test_workshop import USER_RECORD, SandboxRunTests, plan_and_code  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


class WebRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SandboxRunTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        SandboxRunTests.tearDownClass.__func__(cls)

    def setUp(self):
        d = Path(tempfile.mkdtemp())
        sheet = shutil.copy2(REPO / "demo" / "data" / "invoices.xlsx", d / "invoices.xlsx")
        plan, code = plan_and_code()
        now = time.time()
        plan["name"] = f"Invoice Portal {int(now * 1000) % 100000}"
        plan["target"] = {"url": self.url, "page_title": "Invoice Portal"}
        plan["source"] = {"path": str(sheet), "sheet": "Invoices", "header_row": 1, "rows": 30}
        plan["demo"].update({"start": now - 60, "end": now})
        self.tool = build.save_tool(plan, code, build.Verification(ok=True, attempts=1, user_record=dict(USER_RECORD)))
        cfg = load_config()
        cfg.root = Path(tempfile.mkdtemp())
        self.store = RecipeStore(cfg)
        self.allow = ["127.0.0.1", "localhost"]
        self.r = web.recipe_from_tool(self.store, self.tool, self.allow, 60)

    def test_a_verified_machine_becomes_a_level_3_recipe(self):
        self.assertEqual((self.r.kind, self.r.level, self.r.key_column), ("excel_to_web", 3, "Client"))
        self.assertIn(self.r.extra["sublevel"], ("page", "network"))

    def test_preview_sends_nothing_and_enter_sends_the_row(self):
        runner = run.Runner(self.store)
        runner.allow = self.allow
        before = len(sandbox.records(self.url))
        p = runner.prepare(run.match("para Panadería Ñandú", self.store.all()))
        self.assertIn("Panadería Ñandú", p.new_text)
        self.assertEqual(len(sandbox.records(self.url)), before)        # the preview sent nothing
        self.assertIsNone(runner.apply(p))                               # no undo for a site
        recs = sandbox.records(self.url)
        self.assertEqual(len(recs), before + 1)
        self.assertEqual(recs[-1]["client"], "Panadería Ñandú")

    def test_tokens_live_in_the_credential_manager(self):
        name = f"edith-test-{int(time.time())}.local"
        web.save_token(name, "s3cr3t")
        try:
            self.assertEqual(web.token_for(name), "s3cr3t")
        finally:
            self.assertTrue(web.delete_token(name))
        self.assertIsNone(web.token_for(name))

    def test_captcha_and_two_step_pages_are_left_to_the_user(self):
        for text in ("Please complete the reCAPTCHA", "Ingresa el código de verificación", "Two-step verification"):
            self.assertTrue(web.needs_user(text), text)
        self.assertFalse(web.needs_user("Invoice saved."))


if __name__ == "__main__":
    unittest.main()
