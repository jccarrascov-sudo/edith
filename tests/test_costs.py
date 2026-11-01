"""Session cost meter, budget brake and model guard (no network)."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from pantallita.brain.client import BudgetExceeded, CostMeter, check_model, token_cost


class CostTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="pantallita-costs-"))

    def test_prices_match_muse_spark_1_3(self):
        # 1M uncached input + 1M output = $1.25 + $4.25
        self.assertAlmostEqual(token_cost("muse-spark-1.3", 1_000_000, 0, 1_000_000), 5.50)
        # cached tokens are a subset of input tokens, billed at $0.15/M
        self.assertAlmostEqual(token_cost("muse-spark-1.3", 1_000_000, 1_000_000, 0), 0.15)
        self.assertAlmostEqual(token_cost("muse-spark-1.3", 11_281, 0, 910), 0.0179688, places=6)

    def test_brake_stops_calls_over_the_limit(self):
        meter = CostMeter(limit_usd=5.0, idle_hours=3, state_dir=self.dir)
        meter.check()
        meter.record("muse-spark-1.3", "test", input_tokens=4_000_000, output_tokens=0)  # $5.00
        with self.assertRaises(BudgetExceeded):
            meter.check()

    def test_session_persists_and_resets_after_idle(self):
        meter = CostMeter(5.0, idle_hours=3, state_dir=self.dir)
        meter.record("sam-3.1", "seg", images=4)
        self.assertAlmostEqual(CostMeter(5.0, 3, self.dir).session.cost, 0.01)
        state = json.loads((self.dir / "session.json").read_text())
        state["last_call"] = time.time() - 4 * 3600
        (self.dir / "session.json").write_text(json.dumps(state))
        self.assertEqual(CostMeter(5.0, 3, self.dir).session.cost, 0.0)
        ledger = (self.dir / "costs.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(ledger[0])["model"], "sam-3.1")

    def test_contributor_models_are_refused(self):
        with self.assertRaises(ValueError):
            check_model("muse-spark-1.3-contributor")
        check_model("muse-spark-1.3")

    def test_unknown_model_price_is_an_error(self):
        with self.assertRaises(KeyError):
            token_cost("some-other-model", 1, 0, 1)


if __name__ == "__main__":
    unittest.main()
