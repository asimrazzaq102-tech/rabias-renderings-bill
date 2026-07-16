from __future__ import annotations

import io
import os
import sqlite3
import sys
from datetime import date, datetime
from functools import wraps
from pathlib import Path

# PythonAnywhere: pip install --user packages ko web app ke liye available karo
_user_site = (
    Path.home()
    / ".local"
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
)
if _user_site.is_dir() and str(_user_site) not in sys.path:
    sys.path.insert(0, str(_user_site))

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from fpdf import FPDF

APP_NAME = "RABIA'S RENDERINGS MONTHLY BILL"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "bills.db"))
# Change this on online deploy (Render Environment Variable: EDIT_PASSWORD)
EDIT_PASSWORD = os.environ.get("EDIT_PASSWORD", "rabia123")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-online")
app.permanent_session_lifetime = 60 * 60 * 12  # 12 hours unlock after password


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_: object | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 1,
            price REAL NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            month_key TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_bills_month ON bills(month_key, entry_date)"
    )
    db.commit()
    db.close()


def month_key_from_date(value: str) -> str:
    return value[:7]


def format_month_label(month_key: str) -> str:
    try:
        return datetime.strptime(month_key, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return month_key


def current_month_key() -> str:
    return date.today().strftime("%Y-%m")


def parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value.strip().replace(",", ""))
    except (AttributeError, ValueError):
        return default


def can_edit() -> bool:
    return bool(session.get("edit_unlocked"))


def require_edit(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not can_edit():
            flash("Edit ke liye pehle password lagao.", "error")
            return redirect(url_for("index", month=request.args.get("month") or current_month_key()))
        return view(*args, **kwargs)

    return wrapped


def fetch_month_rows(month_key: str) -> tuple[list[dict], float, float]:
    db = get_db()
    items = db.execute(
        """
        SELECT *
        FROM bills
        WHERE month_key = ?
        ORDER BY entry_date ASC, id ASC
        """,
        (month_key,),
    ).fetchall()

    total_amount = 0.0
    total_qty = 0.0
    rows: list[dict] = []
    for item in items:
        amount = float(item["quantity"]) * float(item["price"])
        total_amount += amount
        total_qty += float(item["quantity"])
        rows.append({**dict(item), "amount": amount})
    return rows, total_amount, total_qty


def build_month_pdf(month_key: str) -> bytes:
    rows, total_amount, total_qty = fetch_month_rows(month_key)
    month_label = format_month_label(month_key)

    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "RABIA'S RENDERINGS MONTHLY BILL", ln=True, align="C")
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, f"Month: {month_label}", ln=True, align="C")
    pdf.cell(0, 8, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True, align="C")
    pdf.ln(4)

    headers = ["Date", "Item", "Qty", "Price", "Amount", "Notes"]
    widths = [28, 70, 22, 30, 32, 84]

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(15, 61, 62)
    pdf.set_text_color(255, 255, 255)
    for header, width in zip(headers, widths):
        pdf.cell(width, 9, header, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 9)
    fill = False
    for row in rows:
        pdf.set_fill_color(240, 245, 241)
        values = [
            str(row["entry_date"]),
            str(row["item_name"])[:40],
            f"{float(row['quantity']):.2f}",
            f"{float(row['price']):.2f}",
            f"{float(row['amount']):.2f}",
            str(row["notes"] or "")[:50],
        ]
        for value, width in zip(values, widths):
            pdf.cell(width, 8, value, border=1, fill=fill)
        pdf.ln()
        fill = not fill

    if not rows:
        pdf.cell(sum(widths), 10, "No items in this month.", border=1, align="C")
        pdf.ln()

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, f"Total Quantity: {total_qty:.2f}", ln=True)
    pdf.cell(0, 8, f"Total Paisa: Rs {total_amount:.2f}", ln=True)

    output = pdf.output()
    if isinstance(output, (bytes, bytearray)):
        return bytes(output)
    return str(output).encode("latin-1")


@app.before_request
def ensure_db() -> None:
    if not getattr(app, "_db_ready", False):
        init_db()
        app._db_ready = True


@app.route("/")
def index():
    selected_month = request.args.get("month") or current_month_key()

    db = get_db()
    months = db.execute(
        """
        SELECT DISTINCT month_key
        FROM bills
        ORDER BY month_key DESC
        """
    ).fetchall()
    month_keys = [row["month_key"] for row in months]
    if selected_month not in month_keys:
        month_keys = [selected_month] + month_keys

    rows, total_amount, total_qty = fetch_month_rows(selected_month)
    rows = list(reversed(rows))

    return render_template(
        "index.html",
        app_name=APP_NAME,
        selected_month=selected_month,
        selected_month_label=format_month_label(selected_month),
        month_keys=month_keys,
        format_month_label=format_month_label,
        items=rows,
        total_amount=total_amount,
        total_qty=total_qty,
        today=date.today().isoformat(),
        current_month=current_month_key(),
        can_edit=can_edit(),
    )


@app.route("/unlock", methods=["POST"])
def unlock():
    password = (request.form.get("password") or "").strip()
    next_month = (request.form.get("month") or current_month_key()).strip()
    if password == EDIT_PASSWORD:
        session["edit_unlocked"] = True
        session.permanent = True
        flash("Password OK — ab aap edit / add kar sakte ho.", "success")
    else:
        session.pop("edit_unlocked", None)
        flash("Galat password. Sirf dekh sakte ho, edit nahi.", "error")
    return redirect(url_for("index", month=next_month))


@app.route("/lock", methods=["POST"])
def lock():
    next_month = (request.form.get("month") or current_month_key()).strip()
    session.pop("edit_unlocked", None)
    flash("Edit lock ho gaya. Dobara password lagega.", "success")
    return redirect(url_for("index", month=next_month))


@app.route("/pdf")
def download_pdf():
    selected_month = request.args.get("month") or current_month_key()
    pdf_bytes = build_month_pdf(selected_month)
    filename = f"Rabias_Renderings_{selected_month}.pdf"
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/add", methods=["POST"])
@require_edit
def add_item():
    entry_date = (request.form.get("entry_date") or "").strip()
    item_name = (request.form.get("item_name") or "").strip()
    quantity = parse_float(request.form.get("quantity", "1"), 1.0)
    price = parse_float(request.form.get("price", "0"), 0.0)
    notes = (request.form.get("notes") or "").strip()

    if not entry_date or not item_name:
        flash("Date aur Item name zaroori hain.", "error")
        return redirect(url_for("index"))

    if quantity <= 0:
        flash("Quantity 0 se zyada honi chahiye.", "error")
        return redirect(url_for("index", month=month_key_from_date(entry_date)))

    month_key = month_key_from_date(entry_date)
    db = get_db()
    db.execute(
        """
        INSERT INTO bills (entry_date, item_name, quantity, price, notes, month_key, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_date,
            item_name,
            quantity,
            price,
            notes,
            month_key,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    db.commit()
    flash("Item save ho gaya.", "success")
    return redirect(url_for("index", month=month_key))


@app.route("/edit/<int:item_id>", methods=["GET", "POST"])
def edit_item(item_id: int):
    if not can_edit():
        flash("Edit ke liye pehle password lagao.", "error")
        return redirect(url_for("index"))

    db = get_db()
    item = db.execute("SELECT * FROM bills WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        flash("Item nahi mila.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        entry_date = (request.form.get("entry_date") or "").strip()
        item_name = (request.form.get("item_name") or "").strip()
        quantity = parse_float(request.form.get("quantity", "1"), 1.0)
        price = parse_float(request.form.get("price", "0"), 0.0)
        notes = (request.form.get("notes") or "").strip()

        if not entry_date or not item_name:
            flash("Date aur Item name zaroori hain.", "error")
            return redirect(url_for("edit_item", item_id=item_id))

        if quantity <= 0:
            flash("Quantity 0 se zyada honi chahiye.", "error")
            return redirect(url_for("edit_item", item_id=item_id))

        month_key = month_key_from_date(entry_date)
        db.execute(
            """
            UPDATE bills
            SET entry_date = ?, item_name = ?, quantity = ?, price = ?, notes = ?, month_key = ?
            WHERE id = ?
            """,
            (entry_date, item_name, quantity, price, notes, month_key, item_id),
        )
        db.commit()
        flash("Item update ho gaya.", "success")
        return redirect(url_for("index", month=month_key))

    return render_template(
        "edit.html",
        app_name=APP_NAME,
        item=item,
        amount=float(item["quantity"]) * float(item["price"]),
        can_edit=True,
    )


@app.route("/delete/<int:item_id>", methods=["POST"])
@require_edit
def delete_item(item_id: int):
    db = get_db()
    item = db.execute("SELECT month_key FROM bills WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        flash("Item nahi mila.", "error")
        return redirect(url_for("index"))

    month_key = item["month_key"]
    db.execute("DELETE FROM bills WHERE id = ?", (item_id,))
    db.commit()
    flash("Item delete ho gaya.", "success")
    return redirect(url_for("index", month=month_key))


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
