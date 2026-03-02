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

    # Create table if it doesn't exist
    conn.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            title          TEXT NOT NULL,
            description    TEXT,
            event_date     TEXT NOT NULL,   -- YYYY-MM-DD
            event_time     TEXT,            -- HH:MM (optional)
            event_datetime TEXT,            -- YYYY-MM-DD HH:MM (for time-range queries)
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Migration: add event_datetime column to existing DBs that don't have it
    try:
        conn.execute("ALTER TABLE events ADD COLUMN event_datetime TEXT")
    except Exception:
        pass  # Column already exists — safe to ignore

    # Backfill event_datetime for any existing rows that are missing it
    conn.execute('''
        UPDATE events
        SET event_datetime = event_date || ' ' || COALESCE(event_time, '00:00')
        WHERE event_datetime IS NULL
    ''')

    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def build_event_datetime(event_date: str, event_time: str | None) -> str:
    """Combine date and time into a single sortable string."""
    return f"{event_date} {event_time or '00:00'}"


# ─────────────────────────────────────────────
# DATEPARSER SETTINGS
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


# ─────────────────────────────────────────────
# NATURAL LANGUAGE QUERY INTERPRETER
# ─────────────────────────────────────────────

def interpret_query(text: str) -> dict:
    """
    Two-stage parser that converts a natural language question into a
    structured query descriptor.

    Stage 1 — Regex catches time-window patterns dateparser can't handle:
                "next 2 hours", "next 30 minutes", "in an hour"
    Stage 2 — Named range keywords: "this week", "next week", "weekend", etc.
    Stage 3 — dateparser handles everything else: "tomorrow", "next Friday"
    Stage 4 — Keyword fallback for unrecognised queries
    Stage 5 — Final fallback: all upcoming events

    Returns a dict with keys:
      type  : "datetime_range" | "date_range" | "date" | "keyword" | "upcoming"
      start : datetime string (for datetime_range)
      end   : datetime string (for datetime_range)
      start_date / end_date : ISO date strings (for date_range)
      date  : ISO date string (for date)
      keywords : list of strings (for keyword)
      label : human-readable description of what was searched
    """
    now   = datetime.now()
    today = date.today()
    t     = text.lower().strip()

    # ── STAGE 1: Time-window patterns ──────────────────────────────────────

    # "next N hours" / "next N hour"
    m = re.search(r"next\s+(\d+)\s+hours?", t)
    if m:
        n     = int(m.group(1))
        start = now
        end   = now + timedelta(hours=n)
        return {
            "type":  "datetime_range",
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "end":   end.strftime("%Y-%m-%d %H:%M"),
            "label": f"Events in the next {n} hour{'s' if n != 1 else ''} "
                     f"({start.strftime('%H:%M')} – {end.strftime('%H:%M')})"
        }

    # "next N minutes" / "next N mins"
    m = re.search(r"next\s+(\d+)\s+(?:minutes?|mins?)", t)
    if m:
        n     = int(m.group(1))
        start = now
        end   = now + timedelta(minutes=n)
        return {
            "type":  "datetime_range",
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "end":   end.strftime("%Y-%m-%d %H:%M"),
            "label": f"Events in the next {n} minute{'s' if n != 1 else ''} "
                     f"({start.strftime('%H:%M')} – {end.strftime('%H:%M')})"
        }

    # "in an hour" / "in 1 hour"
    m = re.search(r"in\s+(?:an?|(\d+))\s+hours?", t)
    if m:
        n     = int(m.group(1)) if m.group(1) else 1
        start = now
        end   = now + timedelta(hours=n)
        return {
            "type":  "datetime_range",
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "end":   end.strftime("%Y-%m-%d %H:%M"),
            "label": f"Events in the next {n} hour{'s' if n != 1 else ''} "
                     f"({start.strftime('%H:%M')} – {end.strftime('%H:%M')})"
        }

    # "in N minutes"
    m = re.search(r"in\s+(\d+)\s+(?:minutes?|mins?)", t)
    if m:
        n     = int(m.group(1))
        start = now
        end   = now + timedelta(minutes=n)
        return {
            "type":  "datetime_range",
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "end":   end.strftime("%Y-%m-%d %H:%M"),
            "label": f"Events in the next {n} minute{'s' if n != 1 else ''} "
                     f"({start.strftime('%H:%M')} – {end.strftime('%H:%M')})"
        }

    # "next N days"
    m = re.search(r"next\s+(\d+)\s+days?", t)
    if m:
        n   = int(m.group(1))
        end = today + timedelta(days=n)
        return {
            "type":       "date_range",
            "start_date": today.isoformat(),
            "end_date":   end.isoformat(),
            "label":      f"Events in the next {n} day{'s' if n != 1 else ''} "
                          f"({today.isoformat()} – {end.isoformat()})"
        }

    # ── STAGE 2: Named range keywords ──────────────────────────────────────

    if any(w in t for w in ["this week", "this wk"]):
        start = today - timedelta(days=today.weekday())
        end   = start + timedelta(days=6)
        return {
            "type":       "date_range",
            "start_date": start.isoformat(),
            "end_date":   end.isoformat(),
            "label":      f"Events this week ({start.isoformat()} – {end.isoformat()})"
        }

    if any(w in t for w in ["next week", "next wk"]):
        start = today + timedelta(days=7 - today.weekday())
        end   = start + timedelta(days=6)
        return {
            "type":       "date_range",
            "start_date": start.isoformat(),
            "end_date":   end.isoformat(),
            "label":      f"Events next week ({start.isoformat()} – {end.isoformat()})"
        }

    if "weekend" in t:
        # Find the coming Saturday
        days_to_sat = (5 - today.weekday()) % 7 or 7
        sat = today + timedelta(days=days_to_sat)
        sun = sat + timedelta(days=1)
        return {
            "type":       "date_range",
            "start_date": sat.isoformat(),
            "end_date":   sun.isoformat(),
            "label":      f"Events this weekend ({sat.isoformat()} – {sun.isoformat()})"
        }

    if "this month" in t:
        start = today.replace(day=1)
        end   = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return {
            "type":       "date_range",
            "start_date": start.isoformat(),
            "end_date":   end.isoformat(),
            "label":      f"Events this month ({start.strftime('%B %Y')})"
        }

    if "next month" in t:
        first = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        end   = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return {
            "type":       "date_range",
            "start_date": first.isoformat(),
            "end_date":   end.isoformat(),
            "label":      f"Events next month ({first.strftime('%B %Y')})"
        }

    if any(w in t for w in ["today", "right now", "now"]):
        return {
            "type":  "date",
            "date":  today.isoformat(),
            "label": f"Events today ({today.strftime('%A, %d %B %Y')})"
        }

    if "tomorrow" in t:
        tomorrow = today + timedelta(days=1)
        return {
            "type":  "date",
            "date":  tomorrow.isoformat(),
            "label": f"Events tomorrow ({tomorrow.strftime('%A, %d %B %Y')})"
        }

    if "yesterday" in t:
        yesterday = today - timedelta(days=1)
        return {
            "type":  "date",
            "date":  yesterday.isoformat(),
            "label": f"Events yesterday ({yesterday.strftime('%A, %d %B %Y')})"
        }

    if any(w in t for w in ["upcoming", "coming up", "soon", "what do i have",
                             "what have i got", "schedule", "agenda"]):
        return {
            "type":  "upcoming",
            "label": "All upcoming events"
        }

    # ── STAGE 3: dateparser for specific dates ──────────────────────────────

    parsed = dateparser.parse(text, settings=SETTINGS)
    if parsed:
        d = parsed.strftime("%Y-%m-%d")
        return {
            "type":  "date",
            "date":  d,
            "label": f"Events on {parsed.strftime('%A, %d %B %Y')}"
        }

    # ── STAGE 4: Keyword search ─────────────────────────────────────────────

    # Strip common filler words before extracting keywords
    stop_words = {"what", "do", "have", "i", "the", "on", "at", "in", "is",
                  "are", "any", "me", "my", "show", "list", "get", "for",
                  "events", "next", "this", "when", "a", "an", "and", "or"}
    words = [
        w for w in re.sub(r"[^\w\s]", "", text.lower()).split()
        if len(w) > 2 and w not in stop_words
    ]
    if words:
        return {
            "type":     "keyword",
            "keywords": words,
            "label":    f'Events matching "{", ".join(words)}"'
        }

    # ── STAGE 5: Fallback ───────────────────────────────────────────────────
    return {
        "type":  "upcoming",
        "label": "All upcoming events"
    }


def execute_query(descriptor: dict) -> list:
    """
    Takes an interpreted query descriptor and runs the appropriate
    SQLite query, returning a list of event dicts.
    """
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = date.today().isoformat()
    conn  = get_db()

    qtype = descriptor["type"]

    if qtype == "datetime_range":
        rows = conn.execute(
            '''SELECT * FROM events
               WHERE event_datetime BETWEEN ? AND ?
               ORDER BY event_datetime''',
            (descriptor["start"], descriptor["end"])
        ).fetchall()

    elif qtype == "date_range":
        rows = conn.execute(
            '''SELECT * FROM events
               WHERE event_date BETWEEN ? AND ?
               ORDER BY event_date, event_time''',
            (descriptor["start_date"], descriptor["end_date"])
        ).fetchall()

    elif qtype == "date":
        rows = conn.execute(
            '''SELECT * FROM events
               WHERE event_date = ?
               ORDER BY event_time''',
            (descriptor["date"],)
        ).fetchall()

    elif qtype == "keyword":
        conditions = " OR ".join(
            ["title LIKE ? OR description LIKE ?" for _ in descriptor["keywords"]]
        )
        params = [p for w in descriptor["keywords"] for p in (f"%{w}%", f"%{w}%")]
        rows = conn.execute(
            f'''SELECT * FROM events
                WHERE ({conditions}) AND event_date >= ?
                ORDER BY event_date, event_time''',
            params + [today]
        ).fetchall()

    else:  # upcoming
        rows = conn.execute(
            '''SELECT * FROM events
               WHERE event_datetime >= ?
               ORDER BY event_datetime''',
            (now,)
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def parse_date_range(text: str):
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
        end   = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return start.isoformat(), end.isoformat()
    if "next month" in t:
        first = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        end   = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return first.isoformat(), end.isoformat()
    m = re.search(r"next\s+(\d+)\s+days?", t)
    if m:
        n = int(m.group(1))
        return today.isoformat(), (today + timedelta(days=n)).isoformat()
    return None, None


def search_events(query: str):
    today = date.today().isoformat()
    conn  = get_db()
    start, end = parse_date_range(query)
    if start and end:
        rows = conn.execute(
            "SELECT * FROM events WHERE event_date BETWEEN ? AND ? ORDER BY event_date, event_time",
            (start, end)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], f"Events from {start} to {end}"
    parsed_date = parse_natural_date(query)
    if parsed_date:
        rows = conn.execute(
            "SELECT * FROM events WHERE event_date = ? ORDER BY event_time",
            (parsed_date,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], f"Events on {parsed_date}"
    words = [w for w in re.sub(r"[^\w\s]", "", query).split() if len(w) > 2]
    if words:
        conditions = " OR ".join(["title LIKE ? OR description LIKE ?" for _ in words])
        params     = [p for w in words for p in (f"%{w}%", f"%{w}%")]
        rows = conn.execute(
            f"SELECT * FROM events WHERE ({conditions}) AND event_date >= ? ORDER BY event_date, event_time",
            params + [today]
        ).fetchall()
        conn.close()
        if rows:
            return [dict(r) for r in rows], f'Events matching "{query}"'
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
    now  = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM events WHERE event_datetime >= ? ORDER BY event_datetime", (now,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/events", methods=["POST"])
def add_event():
    data        = request.json or {}
    title       = (data.get("title") or "").strip()
    raw_date    = (data.get("event_date") or "").strip()
    event_time  = (data.get("event_time") or "").strip() or None
    description = (data.get("description") or "").strip() or None

    if not title:
        return jsonify({"error": "Title is required"}), 400
    if not raw_date:
        return jsonify({"error": "Date is required"}), 400

    if re.match(r"\d{4}-\d{2}-\d{2}", raw_date):
        event_date = raw_date
    else:
        event_date = parse_natural_date(raw_date)
        if not event_date:
            return jsonify({"error": f"Could not understand date: '{raw_date}'"}), 400

    event_datetime = build_event_datetime(event_date, event_time)

    conn = get_db()
    cur  = conn.execute(
        "INSERT INTO events (title, description, event_date, event_time, event_datetime) VALUES (?,?,?,?,?)",
        (title, description, event_date, event_time, event_datetime)
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
    text   = (request.json or {}).get("text", "")
    parsed = parse_natural_date(text)
    return jsonify({"date": parsed})


@app.route("/api/search", methods=["POST"])
def search():
    query = (request.json or {}).get("query", "").strip()
    if not query:
        return jsonify({"error": "Empty query"}), 400
    events, label = search_events(query)
    return jsonify({"events": events, "label": label})


@app.route("/api/ask", methods=["POST"])
def ask():
    """
    Natural language question endpoint.
    Accepts a free-text question and returns matching events + a label
    explaining what was searched.

    Examples:
      "what do i have tomorrow"
      "next 2 hours"
      "anything this weekend"
      "show me next week"
      "do i have a meeting"
    """
    question = (request.json or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "Empty question"}), 400

    descriptor = interpret_query(question)
    events     = execute_query(descriptor)

    return jsonify({
        "events":     events,
        "label":      descriptor["label"],
        "query_type": descriptor["type"],
        "count":      len(events)
    })


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("✓  Schedule AI is running → http://localhost:5000")
    app.run(debug=True, port=5000)
