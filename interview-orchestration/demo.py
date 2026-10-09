"""Reproducible scenario; defaults to a temporary database, never deletes existing files."""
import argparse
import json
import tempfile
from pathlib import Path
from orchestrator import ConflictError, Orchestrator


def run(path):
    path = Path(path)
    if path.exists():
        raise ValueError("Use a new database path for the reproducible demo")
    flow = Orchestrator(path)
    event = {"type": "vacancy.created", "vacancy_id": "synthetic-vacancy-01",
             "revision": 1, "slot": "2026-11-06T14:00:00+00:00"}
    evidence = {"synthetic_data_only": True, "database": str(path)}
    evidence["accepted"] = flow.accept("event-01", event)
    evidence["duplicate"] = flow.accept("event-01", dict(reversed(list(event.items()))))
    try:
        flow.accept("event-01", {**event, "slot": "2026-11-06T15:00:00+00:00"})
    except ConflictError as error:
        evidence["conflict_status"] = error.status_code
    evidence["timeout_before_accept"] = flow.tick("before_accept")
    evidence["after_timeout"] = flow.snapshot()
    evidence["retry_acknowledged"] = flow.tick()
    evidence["active_after_retry"] = flow.snapshot()["provider_schedules"]
    flow.accept("event-02", {**event, "type": "interview.rescheduled", "revision": 2,
                             "slot": "2026-11-06T16:00:00+00:00"})
    flow.accept("event-03", {"type": "interview.cancelled", "vacancy_id": event["vacancy_id"],
                             "revision": 3})
    evidence["stale_schedule_skipped"] = flow.tick()
    evidence["cancellation_acknowledged"] = flow.tick()
    evidence["late_event"] = flow.accept("late-event", {**event, "revision": 2})
    evidence["final"] = flow.snapshot()
    flow.close()
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, help="New persistent SQLite path (existing data refused)")
    parser.add_argument("--output", type=Path, help="Optional JSON evidence file")
    args = parser.parse_args()
    if args.db:
        result = run(args.db)
    else:
        with tempfile.TemporaryDirectory() as directory:
            result = run(Path(directory) / "demo.sqlite")
            result["database"] = "temporary database; removed after run"
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
