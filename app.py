from flask import Flask, render_template, request, jsonify
import sqlite3
import os
import re
from datetime import datetime, date, timedelta
import dateparser

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "events.db")


# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT,
            event_date  TEXT NOT NULL,   -- YYYY-MM-DD
            event_time  TEXT,            -- HH:MM (optional)
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────
# DATEPARSER HELPERS
# ─────────────────────────────────────────────

SETTINGS = {
    "PREFER_DATES_FROM": "future",
    "RETURN_AS_TIMEZONE_AWARE": False,
    "DATE_ORDER": "YMD",
}


def parse_natural_date(text: str) -> str | None:
    """Convert any natural-language date string to YYYY-MM-DD, or None."""
    if not text:
        return None
    parsed = dateparser.parse(text.strip(), settings=SETTINGS)
    return parsed.strftime("%Y-%m-%d") if parsed else None


def parse_date_range(text: str):
    """
    Detect range keywords like 'this week', 'next month', 'next 7 days'.
    Returns (start_iso, end_iso) or (None, None).
    """
    today = date.today()
    t = text.lower()

    if "this week" in t:
        start = today - timedelta(days=today.weekday())
        return start.isoformat(), (start + timedelta(days=6)).isoformat()

    if "next week" in t:
        start = today + timedelta(days=7 - today.weekday())
        return start.isoformat(), (start + timedelta(days=6)).isoformat()

    if "this month" in t:
        start = today.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return start.isoformat(), end.isoformat()

    if "next month" in t:
        first = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return first.isoformat(), end.isoformat()

    m = re.search(r"next\s+(\d+)\s+days?", t)
    if m:
        n = int(m.group(1))
        return today.isoformat(), (today + timedelta(days=n)).isoformat()

    return None, None


def search_events(query: str):
    """
    Parse a free-text query and return matching events from the DB.
    Supports: specific dates, date ranges, keyword search, and 'upcoming'.
    """
    today = date.today().isoformat()
    conn = get_db()

    # 1. Date range?
    start, end = parse_date_range(query)
    if start and end:
        rows = conn.execute(
            "SELECT * FROM events WHERE event_date BETWEEN ? AND ? ORDER BY event_date, event_time",
            (start, end)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], f"Events from {start} to {end}"

    # 2. Specific date?
    parsed_date = parse_natural_date(query)
    if parsed_date:
        rows = conn.execute(
            "SELECT * FROM events WHERE event_date = ? ORDER BY event_time",
            (parsed_date,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], f"Events on {parsed_date}"

    # 3. Keyword in title/description?
    words = [w for w in re.sub(r"[^\w\s]", "", query).split() if len(w) > 2]
    if words:
        conditions = " OR ".join(["title LIKE ? OR description LIKE ?" for _ in words])
        params = [p for w in words for p in (f"%{w}%", f"%{w}%")]
        rows = conn.execute(
            f"SELECT * FROM events WHERE ({conditions}) AND event_date >= ? ORDER BY event_date, event_time",
            params + [today]
        ).fetchall()
        conn.close()
        if rows:
            return [dict(r) for r in rows], f'Events matching "{query}"'

    # 4. Fallback: all upcoming
    rows = conn.execute(
        "SELECT * FROM events WHERE event_date >= ? ORDER BY event_date, event_time",
        (today,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], "All upcoming events"


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/events", methods=["GET"])
def get_all_events():
    conn = get_db()
    rows = conn.execute("SELECT * FROM events ORDER BY event_date, event_time").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/events/date/<date_str>", methods=["GET"])
def get_by_date(date_str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM events WHERE event_date = ? ORDER BY event_time", (date_str,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/events/upcoming", methods=["GET"])
def get_upcoming():
    today = date.today().isoformat()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM events WHERE event_date >= ? ORDER BY event_date, event_time", (today,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/events", methods=["POST"])
def add_event():
    data = request.json or {}
    title      = (data.get("title") or "").strip()
    raw_date   = (data.get("event_date") or "").strip()
    event_time = (data.get("event_time") or "").strip() or None
    description = (data.get("description") or "").strip() or None

    if not title:
        return jsonify({"error": "Title is required"}), 400
    if not raw_date:
        return jsonify({"error": "Date is required"}), 400

    # Resolve natural date if not already ISO
    if re.match(r"\d{4}-\d{2}-\d{2}", raw_date):
        event_date = raw_date
    else:
        event_date = parse_natural_date(raw_date)
        if not event_date:
            return jsonify({"error": f"Could not understand date: '{raw_date}'"}), 400

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO events (title, description, event_date, event_time) VALUES (?,?,?,?)",
        (title, description, event_date, event_time)
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return jsonify({"success": True, "id": new_id, "event_date": event_date})


@app.route("/api/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    conn = get_db()
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/parse-date", methods=["POST"])
def parse_date_endpoint():
    """Frontend uses this to preview a natural-language date before submitting."""
    text = (request.json or {}).get("text", "")
    parsed = parse_natural_date(text)
    return jsonify({"date": parsed})


@app.route("/api/search", methods=["POST"])
def search():
    """
    Natural-language search — no AI needed.
    Powered entirely by dateparser + SQLite.
    """
    query = (request.json or {}).get("query", "").strip()
    if not query:
        return jsonify({"error": "Empty query"}), 400

    events, label = search_events(query)
    return jsonify({"events": events, "label": label})


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("✓  Schedule AI is running → http://localhost:5000")
    app.run(debug=True, port=5000)
