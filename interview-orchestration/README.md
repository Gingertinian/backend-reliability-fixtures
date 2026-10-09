# Interview coordination fixture

A small, fresh Python standard-library example for discussing backend reliability in interview coordination. All vacancy IDs and timestamps are synthetic. No people are scored or selected. No real candidate data, calendars, credentials, paid APIs or network requests are used.

From this directory, with Python 3.10+:

```text
python -B -m unittest -v
python -B demo.py
```

To preserve a run locally, choose a **new** database path:

```text
python -B demo.py --db example.sqlite --output run.json
```

The demo refuses an existing database file before opening it. It never deletes an existing database.

## Observable behavior

1. A repeated `vacancy.created` event key with identical payload produces one outbox command. Key ordering does not affect the payload hash.
2. Reusing the key with a different slot raises `ConflictError` with `status_code=409`. This is an HTTP equivalent, not an HTTP endpoint. Contradictory commands at the same revision also conflict.
3. A simulated provider timeout leaves delivery in `retry`, with an attempt audit. A later acknowledgement changes it to `delivered`.
4. Rescheduling followed by cancellation supersedes the older pending command. A late lower revision does not re-create a cancelled schedule.
5. The fake provider maintains at most one schedule row per vacancy. Rescheduling updates the slot; cancellation changes that row to cancelled.

The tests also simulate a lost acknowledgement **after** the fake provider applies a schedule. The sender still records `retry`, not confirmed success. Retrying uses the same durable provider token and does not add another schedule.

## Scope and limits

- One local process with serialized event handling/worker ticks and SQLite, with explicitly supplied monotonically increasing revisions. Concurrent event ingestion during an in-flight provider call is outside this fixture. No distributed claims, production deployment, auth, HTTP server, scheduler, retry delay/backoff, retry ceiling or dead-letter queue.
- `vacancies.desired` is requested state. `outbox.status=delivered` means the mock acknowledged that particular command, not that every later command has finished.
- Cancellation is asynchronous. A previously delivered schedule can remain active in the fake provider until the cancellation is processed and acknowledged.
- The mock deliberately supports durable idempotency tokens and revision-aware updates. A real calendar/provider needs equivalent support or explicit reconciliation; these tests do not establish it. No distributed exactly-once guarantee is claimed.
- Input timestamps require a timezone but no real timezone/calendar scheduling rules are implemented. `vacancy.created` is a fixture label, not a complete hiring lifecycle.
- No code or private project source was copied into this example. It demonstrates a small backend mechanism, not an existing client's product or an AI hiring system.

`orchestrator.py` contains the fixture, `demo.py` produces JSON evidence and `test_orchestrator.py` checks business invariants.
