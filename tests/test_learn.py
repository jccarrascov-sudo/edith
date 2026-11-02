"""Learning backwards: demo segment, source row, transforms, plan checking (no network)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("PANTALLITA_HOME", tempfile.mkdtemp(prefix="pantallita-test-"))

from pantallita import config  # noqa: E402
from pantallita.learn import plan as plan_mod  # noqa: E402
from pantallita.learn import segment as seg_mod  # noqa: E402
from pantallita.learn import source as src_mod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo"))
import make_invoices  # noqa: E402

T0 = 1_800_000_000.0
PORTAL = ("New invoice - Invoice Portal - Google Chrome", "chrome.exe")
EXCEL = ("invoices - Excel", "EXCEL.EXE")
URL = "http://127.0.0.1:8765/"


def click(ts, name, kind="button", where=PORTAL):
    e = {"type": "click", "ts": T0 + ts, "x": 1, "y": 1, "button": "left", "window": where[0],
         "process": where[1], "element": {"kind": kind, "name": name, "rect": [0, 0, 1, 1]}}
    if where is PORTAL:
        e["url"] = URL
    return e


def field(ts, name, value, where=PORTAL):
    e = {"type": "field", "ts": T0 + ts, "value": value, "window": where[0], "process": where[1],
         "element": {"kind": "edit", "name": name, "automation_id": "", "rect": [0, 0, 1, 1]}}
    if where is PORTAL:
        e["url"] = URL
    return e


def demo_events(row: list, offset: float = 0.0) -> list[dict]:
    client, tax_id, date, amount, desc = row
    typed = [("Client", client), ("Tax ID (RUC)", tax_id), ("Invoice date", date.strftime("%d/%m/%Y")),
             ("Amount (PEN)", f"{amount:,.2f}"), ("Description", desc)]
    events, t = [], offset
    for name, value in typed:
        events.append(click(t, "B2", kind="cell", where=EXCEL))
        events.append(click(t + 2, name, kind="edit"))
        events.append(field(t + 4, name, value))
        t += 6
    events.append(click(t + 1, "Save invoice"))
    return events


class SegmentTests(unittest.TestCase):
    def setUp(self):
        self.rows = make_invoices.generate()

    def test_demo_is_found_and_ends_with_the_submit_click(self):
        events = demo_events(self.rows[0])
        events += [click(200, "Home", kind="link"), click(260, "Reply", where=("Mail", "olk.exe"))]
        [demo] = seg_mod.find_demos(events)
        self.assertEqual(demo.process, "chrome.exe")
        self.assertEqual(demo.submit["element"]["name"], "Save invoice")
        self.assertEqual(list(demo.final_values), ["Client", "Tax ID (RUC)", "Invoice date",
                                                   "Amount (PEN)", "Description"])
        self.assertEqual(demo.url, URL)
        self.assertLess(demo.end, T0 + 40)  # what happened 2 min later is not part of it

    def test_typing_in_the_address_bar_afterwards_is_not_part_of_the_form(self):
        events = demo_events(self.rows[0])
        addr = field(38, "Address and search bar", "youtube.com")
        addr["url"] = "https://www.bing.com/search?q=kernel"
        [demo] = seg_mod.find_demos(events + [addr])
        self.assertNotIn("Address and search bar", demo.final_values)

    def test_back_to_back_submissions_are_separate_episodes(self):
        events = demo_events(self.rows[0]) + demo_events(self.rows[1], offset=40)  # 9 s after the save
        demos = seg_mod.find_demos(events)
        self.assertEqual(len(demos), 2)
        self.assertEqual(demos[0].final_values["Client"], self.rows[1][0])
        self.assertEqual(demos[1].final_values["Client"], self.rows[0][0])
        self.assertLess(demos[1].end, demos[0].start)  # no overlap

    def test_terminal_where_the_user_asks_is_ignored(self):
        events = demo_events(self.rows[0])
        events += [field(100, "Command", "pantallita do", where=("Terminal", "WindowsTerminal.exe")),
                   field(102, "Other", "x", where=("Terminal", "WindowsTerminal.exe"))]
        self.assertEqual(len(seg_mod.find_demos(events)), 1)

    def test_minutes_ago_picks_the_closest_episode(self):
        events = demo_events(self.rows[0]) + demo_events(self.rows[1], offset=400)
        demos = seg_mod.find_demos(events)
        self.assertEqual(len(demos), 2)
        now = T0 + 460
        self.assertEqual(seg_mod.pick(demos, "do the rest", now), demos[0])           # latest
        self.assertEqual(seg_mod.pick(demos, "eso que hice hace 7 minutos", now).start, demos[1].start)


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.path = make_invoices.write(cls.dir / "invoices.xlsx")
        cls.sheet = src_mod.load_sheet(cls.path)

    def test_sheet_has_30_rows_and_excel_formats(self):
        self.assertEqual(len(self.sheet.rows), 30)
        self.assertEqual(self.sheet.headers, ["Client", "Tax ID", "Date", "Amount", "Description"])
        self.assertEqual(self.sheet.formats["Date"].upper(), "DD/MM/YYYY")

    def test_typed_values_find_their_row_and_columns(self):
        for index in (2, 5, 6, 12):  # plain, text amount "1,787.26", ISO text date, accents
            row = self.sheet.rows[index]
            typed = {"Client": row["Client"], "Tax ID (RUC)": row["Tax ID"],
                     "Invoice date": self.sheet.display(row, "Date"), "Amount (PEN)": self.sheet.display(row, "Amount"),
                     "Description": row["Description"]}
            match = src_mod.match_row(self.sheet, typed)
            self.assertEqual(match.index, index)
            self.assertEqual(match.matched, 5)
        self.assertEqual(self.sheet.rows[5]["Amount"], "1,787.26")
        self.assertIsNone(self.sheet.rows[17]["Amount"])
        self.assertEqual(match.columns, {"Client": "Client", "Tax ID (RUC)": "Tax ID", "Invoice date": "Date",
                                         "Amount (PEN)": "Amount", "Description": "Description"})

    def test_no_row_matches_made_up_values(self):
        self.assertIsNone(src_mod.match_row(self.sheet, {"a": "nothing", "b": "nope"}))

    def test_workbook_found_from_excel_title_without_extension(self):
        with mock.patch.object(src_mod, "open_workbooks", lambda: []):
            self.assertEqual(src_mod.find_workbook(["invoices - Excel"], [self.dir]), self.dir / "invoices.xlsx")
            self.assertEqual(src_mod.find_workbook(["invoices.xlsx - Saved - Excel"], [self.dir]),
                             self.dir / "invoices.xlsx")
            self.assertIsNone(src_mod.find_workbook(["Notes - Notepad"], [self.dir]))
            # A real Excel title (Spanish Windows), bidi marks and double spaces included.
            title = "‪invoices‬  -  Guardado en Este PC  -  Excel"
            self.assertEqual(src_mod.find_workbook([title], [self.dir]), self.dir / "invoices.xlsx")


def good_plan() -> dict:
    fills = [("Client", "Client", {"kind": "text"}), ("Tax ID (RUC)", "Tax ID", {"kind": "text"}),
             ("Invoice date", "Date", {"kind": "date", "format": "%d/%m/%Y"}),
             ("Amount (PEN)", "Amount", {"kind": "number", "decimals": 2, "thousands": True}),
             ("Description", "Description", {"kind": "text"})]
    blocks = [{"id": "b1", "type": "read_row", "title": "Next invoice"}]
    for i, (label, col, tr) in enumerate(fills, 2):
        blocks.append({"id": f"b{i}", "type": "fill_field", "title": label,
                       "field": {"label": label, "kind": "edit"}, "column": col, "transform": tr})
    blocks += [{"id": "b7", "type": "save", "title": "Save", "target": {"role": "button", "name": "Save invoice"}},
               {"id": "b8", "type": "wait", "title": "Wait", "until": {"kind": "text", "value": "saved"}},
               {"id": "b9", "type": "verify", "title": "Check", "expect": {"kind": "text", "value": "saved"}}]
    return {"name": "Invoice Hopper", "intent": "Copy each invoice row into the portal form.", "blocks": blocks}


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.path = make_invoices.write(cls.dir / "invoices.xlsx")
        cls.sheet = src_mod.load_sheet(cls.path)
        cls.row = cls.sheet.rows[0]
        r = cls.row
        cls.typed = {"Client": r["Client"], "Tax ID (RUC)": r["Tax ID"], "Invoice date": r["Date"].strftime("%d/%m/%Y"),
                     "Amount (PEN)": f'{r["Amount"]:,.2f}', "Description": r["Description"]}

    def test_every_block_gets_a_meaning_level_contract(self):
        plan = plan_mod.ensure_contracts(good_plan())
        self.assertTrue(all(b["contract"] for b in plan["blocks"]))
        amount = next(b for b in plan["blocks"] if b.get("column") == "Amount")
        self.assertIn('"Amount" column, as a number with 2 decimals', amount["contract"])
        self.assertIn('field that means "Amount (PEN)"', amount["contract"])
        plan["blocks"][1]["contract"] = "Put the client name where the client goes."
        self.assertEqual(plan_mod.ensure_contracts(plan)["blocks"][1]["contract"],
                         "Put the client name where the client goes.")  # the model's own is kept

    def test_transforms(self):
        self.assertEqual(plan_mod.apply_transform(datetime(2026, 9, 10), {"kind": "date", "format": "%d/%m/%Y"}), "10/09/2026")
        self.assertEqual(plan_mod.apply_transform(4216.96, {"kind": "number", "decimals": 2, "thousands": True}), "4,216.96")
        self.assertEqual(plan_mod.apply_transform(2697, {"kind": "number", "decimals": 2}), "2697.00")
        self.assertEqual(plan_mod.apply_transform(2697.0, {"kind": "text"}), "2697")
        self.assertEqual(plan_mod.apply_transform(4216.96, {"kind": "display"}, "#,##0.00"), "4,216.96")

    def test_good_plan_passes(self):
        self.assertEqual(plan_mod.check_plan(good_plan(), self.typed, self.sheet, self.row, "Save invoice"), [])

    def test_wrong_transform_and_column_are_reported(self):
        plan = good_plan()
        plan["blocks"][3]["transform"] = {"kind": "date", "format": "%Y-%m-%d"}
        plan["blocks"][4]["column"] = "Total"
        errors = plan_mod.check_plan(plan, self.typed, self.sheet, self.row, "Save invoice")
        self.assertEqual(len(errors), 2)
        self.assertIn("the user typed", errors[0])
        self.assertIn('column "Total" does not exist', errors[1])

    def test_missing_field_and_wrong_button_are_reported(self):
        plan = good_plan()
        del plan["blocks"][5]
        plan["blocks"][-3]["target"]["name"] = "Submit"
        errors = plan_mod.check_plan(plan, self.typed, self.sheet, self.row, "Save invoice")
        self.assertTrue(any('no fill_field for the field "Description"' in e for e in errors))
        self.assertTrue(any('must press "Save invoice"' in e for e in errors))

    def test_make_plan_retries_with_the_errors_until_it_reproduces_the_demo(self):
        events = []
        r = make_invoices.generate()[0]
        events = demo_events(r)
        demo = seg_mod.find_demos(events)[0]
        bad = good_plan()
        bad["blocks"][4]["transform"] = {"kind": "number", "decimals": 2, "thousands": False}
        replies = iter([f"```json\n{json.dumps(bad)}\n```", json.dumps(good_plan())])
        sent = []

        def chat(purpose, messages, **kwargs):
            sent.append(json.dumps(messages[-1]["content"]) if isinstance(messages[-1]["content"], str) else "")
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(replies)))],
                                   pantallita_cost=0.0)

        cfg = config.load_config(Path(config.ROOT) / "missing.toml")
        with mock.patch.object(src_mod, "find_workbook", lambda titles: self.path):
            plan = plan_mod.make_plan(cfg, demo, "do the rest", events, client=SimpleNamespace(chat=chat),
                                      log_fn=lambda *_: None)
        self.assertIn("the user typed", sent[-1])  # the second call carried the error back
        self.assertEqual(plan["demo"]["row_index"], 0)
        self.assertEqual(plan["target"]["url"], URL)
        self.assertEqual(plan["source"]["rows"], 30)


if __name__ == "__main__":
    unittest.main()


class ChooseTests(unittest.TestCase):
    def setUp(self):
        rows = make_invoices.generate()
        mail = ("New message - Outlook", "olk.exe")
        events = demo_events(rows[0]) + [field(303, "To", "ana@example.com", where=mail),
                                         field(310, "Subject", "Monday", where=mail)]
        self.demos = seg_mod.find_demos(events)

    @staticmethod
    def client(reply):
        def chat(purpose, messages, **kwargs):
            chat.prompt = messages[-1]["content"]
            if isinstance(reply, Exception):
                raise reply
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])
        return SimpleNamespace(chat=chat)

    def test_single_episode_needs_no_model(self):
        seg, sure = seg_mod.choose(self.demos[1:], "whatever, 40 minutes ago", T0 + 400,
                                   client=self.client(AssertionError("no call expected")))
        self.assertTrue(sure)

    def test_model_choice_and_spreadsheet_hint(self):
        c = self.client('{"episode": 2, "confident": true}')
        seg, sure = seg_mod.choose(self.demos, "do it with the rest", T0 + 400, c,
                                   sources={id(self.demos[1]): "invoices.xlsx"})
        self.assertEqual((seg.process, sure), ("chrome.exe", True))
        self.assertIn("Values found in a row of invoices.xlsx", c.chat.prompt)

    def test_model_failure_falls_back_to_time_and_is_not_sure(self):
        seg, sure = seg_mod.choose(self.demos, "what I did 6 minutes ago", T0 + 400,
                                   self.client(RuntimeError("network down")))
        self.assertEqual((seg.process, sure), ("chrome.exe", False))
