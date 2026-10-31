"""Invoice Portal: a small local web app used as Pantallita's controlled demo environment.

v1: form with client, tax ID, date, amount, description (in that order).
v2: the same portal, but the amount field is moved to the top and renamed
    ("Total due", name="total_due") — used for the repair scene.
Submissions are stored in SQLite. The stored columns are the same in both versions, so a
tool's result can be compared field by field against the user's demonstration.

Run:  .venv\\Scripts\\python demo\\portal\\app.py --version v1 [--db PATH] [--port 8765]
"""

from __future__ import annotations

import argparse
import html
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_DB = DATA_DIR / "portal.db"
COLUMNS = ("client", "tax_id", "date", "amount", "description")

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client TEXT NOT NULL,
    tax_id TEXT NOT NULL,
    date TEXT NOT NULL,          -- ISO yyyy-mm-dd
    amount REAL NOT NULL,
    description TEXT NOT NULL,
    portal_version TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

# (form field name, label, column, placeholder, input type) in on-screen order.
FORMS = {
    "v1": [
        ("client", "Client", "client", "Company name", "text"),
        ("tax_id", "Tax ID (RUC)", "tax_id", "11 digits", "text"),
        ("date", "Invoice date", "date", "dd/mm/yyyy", "text"),
        ("amount", "Amount (PEN)", "amount", "0.00", "text"),
        ("description", "Description", "description", "What was sold", "textarea"),
    ],
    "v2": [
        ("total_due", "Total due (PEN)", "amount", "0.00", "text"),
        ("client", "Client", "client", "Company name", "text"),
        ("tax_id", "Tax ID (RUC)", "tax_id", "11 digits", "text"),
        ("date", "Invoice date", "date", "dd/mm/yyyy", "text"),
        ("description", "Description", "description", "What was sold", "textarea"),
    ],
}

CSS = """
:root { --ink:#1f2a37; --muted:#6b7280; --line:#d6dbe1; --bg:#f4f6f8; --accent:#2f6fde; --ok:#1e8a4c; --bad:#c2410c; }
* { box-sizing: border-box; }
body { margin:0; font-family: Segoe UI, Arial, sans-serif; color:var(--ink); background:var(--bg); }
header { background:#fff; border-bottom:1px solid var(--line); padding:14px 28px; display:flex; gap:24px; align-items:center; }
header h1 { font-size:20px; margin:0; } header nav a { color:var(--accent); text-decoration:none; margin-right:16px; }
main { max-width:720px; margin:28px auto; padding:0 16px; }
.card { background:#fff; border:1px solid var(--line); border-radius:10px; padding:24px 28px; }
.row { display:flex; flex-direction:column; margin-bottom:16px; }
label { font-weight:600; margin-bottom:6px; }
input, textarea { font:inherit; padding:9px 11px; border:1px solid var(--line); border-radius:7px; }
textarea { min-height:70px; resize:vertical; }
button { font:inherit; background:var(--accent); color:#fff; border:0; border-radius:7px; padding:10px 22px; cursor:pointer; }
.msg { padding:10px 14px; border-radius:7px; margin-bottom:18px; }
.ok { background:#e7f6ee; color:var(--ok); } .bad { background:#fdeee6; color:var(--bad); }
.v2 .card { border-top:4px solid var(--accent); } .v2 .row:first-child input { font-weight:600; }
table { width:100%; border-collapse:collapse; background:#fff; } th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }
footer { color:var(--muted); font-size:12px; text-align:center; margin:24px; }
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def reset_db(db_path: Path) -> None:
    """Empty the invoices table (works even while the portal is running)."""
    with connect(db_path) as conn:
        conn.execute("DELETE FROM invoices")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'invoices'")


def parse_date(text: str) -> str:
    text = text.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError("Invoice date must look like dd/mm/yyyy")


def parse_amount(text: str) -> float:
    cleaned = text.strip().replace("S/", "").replace("PEN", "").replace(",", "").strip()
    try:
        value = round(float(cleaned), 2)
    except ValueError:
        raise ValueError("Amount must be a number, e.g. 1250.50") from None
    if value <= 0:
        raise ValueError("Amount must be greater than zero")
    return value


def validate(values: dict[str, str]) -> dict:
    row = {c: (values.get(c) or "").strip() for c in COLUMNS}
    missing = [c for c in COLUMNS if not row[c]]
    if missing:
        raise ValueError("Please fill in: " + ", ".join(missing).replace("_", " "))
    if not (row["tax_id"].isdigit() and len(row["tax_id"]) == 11):
        raise ValueError("Tax ID (RUC) must have exactly 11 digits")
    row["date"] = parse_date(row["date"])
    row["amount"] = parse_amount(row["amount"])
    return row


def page(version: str, title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title} - Invoice Portal</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>{CSS}</style></head>
<body class="{version}"><header><h1>Invoice Portal</h1>
<nav><a href="/">New invoice</a><a href="/invoices">Submitted invoices</a></nav></header>
<main>{body}</main><footer>Local demo portal with fictitious data &middot; {version}</footer></body></html>""")


def form_html(version: str, message: str = "", ok: bool = True, values: dict | None = None) -> str:
    values = values or {}
    rows = []
    for name, label, _col, placeholder, kind in FORMS[version]:
        value = html.escape(values.get(name, ""))
        if kind == "textarea":
            control = f'<textarea id="{name}" name="{name}" placeholder="{placeholder}">{value}</textarea>'
        else:
            control = (f'<input id="{name}" name="{name}" type="text" placeholder="{placeholder}" '
                       f'value="{value}" autocomplete="off">')
        rows.append(f'<div class="row"><label for="{name}">{label}</label>{control}</div>')
    note = f'<div class="msg {"ok" if ok else "bad"}" role="status">{html.escape(message)}</div>' if message else ""
    return (f'<div class="card"><h2>New invoice</h2>{note}'
            f'<form method="post" action="/invoices">{"".join(rows)}'
            f'<button type="submit" id="save">Save invoice</button></form></div>')


def create_app(version: str = "v1", db_path: Path = DEFAULT_DB) -> FastAPI:
    if version not in FORMS:
        raise ValueError(f"Unknown portal version {version!r}")
    app = FastAPI(title="Invoice Portal", docs_url=None, redoc_url=None)
    connect(db_path).close()

    @app.get("/", response_class=HTMLResponse)
    def new_invoice(saved: int | None = None):
        message = f"Invoice #{saved} saved." if saved else ""
        return page(version, "New invoice", form_html(version, message))

    @app.post("/invoices")
    async def submit(request: Request):
        form = await request.form()
        raw = {k: str(v) for k, v in form.items()}
        mapped = {col: raw.get(name, "") for name, _l, col, _p, _k in FORMS[version]}
        try:
            row = validate(mapped)
        except ValueError as exc:
            return page(version, "New invoice", form_html(version, str(exc), ok=False, values=raw))
        with connect(db_path) as conn:
            cur = conn.execute(
                "INSERT INTO invoices (client, tax_id, date, amount, description, portal_version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*[row[c] for c in COLUMNS], version, datetime.now().isoformat(timespec="seconds")))
            new_id = cur.lastrowid
        return RedirectResponse(f"/?saved={new_id}", status_code=303)

    @app.get("/invoices", response_class=HTMLResponse)
    def list_invoices():
        with connect(db_path) as conn:
            rows = conn.execute("SELECT * FROM invoices ORDER BY id").fetchall()
        cells = "".join(
            f"<tr><td>{r['id']}</td><td>{html.escape(r['client'])}</td><td>{r['tax_id']}</td>"
            f"<td>{r['date']}</td><td>{r['amount']:.2f}</td><td>{html.escape(r['description'])}</td></tr>"
            for r in rows) or '<tr><td colspan="6">No invoices yet.</td></tr>'
        return page(version, "Submitted invoices",
                    f"<h2>Submitted invoices ({len(rows)})</h2><table><tr><th>#</th><th>Client</th>"
                    f"<th>Tax ID</th><th>Date</th><th>Amount</th><th>Description</th></tr>{cells}</table>")

    @app.get("/api/invoices")
    def api_invoices():
        with connect(db_path) as conn:
            return JSONResponse([dict(r) for r in conn.execute("SELECT * FROM invoices ORDER BY id")])

    @app.get("/health")
    def health():
        return {"status": "ok", "version": version, "db": str(db_path)}

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Invoice Portal (local demo)")
    parser.add_argument("--version", choices=sorted(FORMS), default=os.environ.get("PORTAL_VERSION", "v1"))
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("PORTAL_DB", DEFAULT_DB)))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORTAL_PORT", 8765)))
    args = parser.parse_args()
    print(f"Invoice Portal {args.version} on http://127.0.0.1:{args.port}  (db: {args.db})")
    uvicorn.run(create_app(args.version, args.db), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
