"""Local SQLite fixture. One worker, synthetic events, no calendar/network calls."""
import hashlib
import json
import sqlite3
from datetime import datetime


class ConflictError(ValueError):
    status_code = 409  # HTTP equivalent only; this fixture is not an HTTP server.


class Orchestrator:
    TYPES = {"vacancy.created": "schedule", "interview.rescheduled": "schedule",
             "interview.cancelled": "cancel"}

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS vacancies (
                id TEXT PRIMARY KEY, revision INTEGER, desired TEXT, slot TEXT);
            CREATE TABLE IF NOT EXISTS events (
                key TEXT PRIMARY KEY, payload_hash TEXT, result TEXT);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, event_key TEXT UNIQUE, vacancy_id TEXT,
                revision INTEGER, action TEXT, slot TEXT,
                status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY, outbox_id INTEGER, attempt INTEGER,
                outcome TEXT, detail TEXT);
            CREATE TABLE IF NOT EXISTS provider_schedules (
                vacancy_id TEXT PRIMARY KEY, revision INTEGER, state TEXT, slot TEXT);
            CREATE TABLE IF NOT EXISTS provider_receipts (token TEXT PRIMARY KEY);
        """)

    def close(self):
        self.db.close()

    def accept(self, key, payload):
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Nonempty event key required")
        if not isinstance(payload, dict):
            raise ValueError("Payload must be a dictionary")
        if set(payload) - {"type", "vacancy_id", "revision", "slot"}:
            raise ValueError("Unknown payload field")
        kind = payload.get("type")
        action = self.TYPES.get(kind) if isinstance(kind, str) else None
        vacancy = payload.get("vacancy_id")
        revision, slot = payload.get("revision"), payload.get("slot")
        if not action or not isinstance(vacancy, str) or not vacancy.strip():
            raise ValueError("Supported type and synthetic vacancy ID required")
        if type(revision) is not int or not 1 <= revision <= 2**63 - 1:
            raise ValueError("Revision must be a positive signed 64-bit integer")
        if action == "schedule":
            if not isinstance(slot, str) or datetime.fromisoformat(slot).utcoffset() is None:
                raise ValueError("Schedule requires an ISO timestamp with timezone")
        elif slot is not None:
            raise ValueError("Cancel must not supply a slot")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            previous = self.db.execute("SELECT * FROM events WHERE key=?", (key,)).fetchone()
            if previous:
                if previous["payload_hash"] != digest:
                    raise ConflictError("Event key reused with a different payload")
                return {"status_code": 200, "result": "duplicate"}
            current = self.db.execute("SELECT * FROM vacancies WHERE id=?", (vacancy,)).fetchone()
            if current and revision == current["revision"]:
                if (action, slot) != (current["desired"], current["slot"]):
                    raise ConflictError("Conflicting commands at the same revision")
            stale = current is not None and revision <= current["revision"]
            result = "stale" if stale else "accepted"
            self.db.execute("INSERT INTO events VALUES (?,?,?)", (key, digest, result))
            if not stale:
                self.db.execute("INSERT INTO vacancies VALUES (?,?,?,?) ON CONFLICT(id) "
                                "DO UPDATE SET revision=excluded.revision, desired=excluded.desired, "
                                "slot=excluded.slot", (vacancy, revision, action, slot))
                self.db.execute("INSERT INTO outbox(event_key,vacancy_id,revision,action,slot) "
                                "VALUES (?,?,?,?,?)", (key, vacancy, revision, action, slot))
        return {"status_code": 200 if stale else 202, "result": result}

    def _fake_provider(self, job, failure):
        # Deliberately model a provider supporting durable idempotency and revisions.
        if failure == "before_accept":
            raise TimeoutError("Simulated timeout before provider accepts")
        token = json.dumps([job["vacancy_id"], job["revision"]], separators=(",", ":"))
        with self.db:
            seen = self.db.execute("SELECT 1 FROM provider_receipts WHERE token=?", (token,)).fetchone()
            if not seen:
                self.db.execute("INSERT INTO provider_schedules VALUES (?,?,?,?) "
                                "ON CONFLICT(vacancy_id) DO UPDATE SET revision=excluded.revision, "
                                "state=excluded.state, slot=excluded.slot "
                                "WHERE excluded.revision > provider_schedules.revision",
                                (job["vacancy_id"], job["revision"],
                                 "active" if job["action"] == "schedule" else "cancelled", job["slot"]))
                self.db.execute("INSERT INTO provider_receipts VALUES (?)", (token,))
        if failure == "after_accept":
            raise TimeoutError("Simulated lost acknowledgement after provider accepts")

    def tick(self, failure=None):
        if failure not in (None, "before_accept", "after_accept"):
            raise ValueError("Unknown simulation failure mode")
        job = self.db.execute("SELECT * FROM outbox WHERE status IN ('pending','retry') "
                              "ORDER BY id LIMIT 1").fetchone()
        if job is None:
            return "idle"
        current = self.db.execute("SELECT revision FROM vacancies WHERE id=?",
                                  (job["vacancy_id"],)).fetchone()
        if current["revision"] != job["revision"]:
            with self.db:
                self.db.execute("UPDATE outbox SET status='superseded' WHERE id=?", (job["id"],))
                self.db.execute("INSERT INTO attempts(outbox_id,attempt,outcome,detail) VALUES (?,?,?,?)",
                                (job["id"], job["attempts"], "superseded", "No provider call"))
            return "superseded"
        number = job["attempts"] + 1
        try:
            self._fake_provider(job, failure)
            state, detail = "delivered", "Provider acknowledgement received"
        except TimeoutError as error:
            state, detail = "retry", str(error)
        with self.db:
            self.db.execute("UPDATE outbox SET status=?,attempts=? WHERE id=?", (state, number, job["id"]))
            self.db.execute("INSERT INTO attempts(outbox_id,attempt,outcome,detail) VALUES (?,?,?,?)",
                            (job["id"], number, state, detail))
        return state

    def snapshot(self):
        tables = ("vacancies", "events", "outbox", "attempts", "provider_schedules", "provider_receipts")
        return {table: [dict(row) for row in self.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in tables}
