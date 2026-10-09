import tempfile
import unittest
from pathlib import Path
from orchestrator import ConflictError, Orchestrator
from demo import run


class BusinessInvariants(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "fixture.sqlite"
        self.flow = Orchestrator(self.path)
        self.event = {"type": "vacancy.created", "vacancy_id": "synthetic-01",
                      "revision": 1, "slot": "2026-11-06T14:00:00+00:00"}

    def tearDown(self):
        self.flow.close()
        self.directory.cleanup()

    def active(self):
        return [row for row in self.flow.snapshot()["provider_schedules"] if row["state"] == "active"]

    def test_duplicate_events_create_one_schedule_even_after_restart(self):
        self.flow.accept("original", self.event)
        for _ in range(10):
            self.assertEqual(self.flow.accept("original", self.event)["result"], "duplicate")
        self.assertEqual(self.flow.tick(), "delivered")
        self.flow.close()
        self.flow = Orchestrator(self.path)
        self.flow.accept("original", self.event)
        self.flow.accept("distinct-key-same-command", self.event)
        self.assertEqual(self.flow.tick(), "idle")
        self.assertEqual(len(self.active()), 1)
        self.assertEqual(len(self.flow.snapshot()["outbox"]), 1)
        self.assertEqual(len(self.flow.snapshot()["provider_receipts"]), 1)

    def test_conflicting_payload_rejects_without_mutation(self):
        self.flow.accept("original", self.event)
        before = self.flow.snapshot()
        with self.assertRaises(ConflictError) as caught:
            self.flow.accept("original", {**self.event, "slot": "2026-11-06T15:00:00+00:00"})
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.flow.snapshot(), before)
        with self.assertRaises(ConflictError):
            self.flow.accept("another-key", {**self.event, "type": "interview.cancelled", "slot": None})
        self.assertEqual(self.flow.snapshot(), before)

    def test_transient_failure_does_not_claim_delivery_then_retry_succeeds(self):
        self.flow.accept("original", self.event)
        self.assertEqual(self.flow.tick("before_accept"), "retry")
        state = self.flow.snapshot()
        self.assertEqual(state["outbox"][0]["status"], "retry")
        self.assertEqual(len(self.active()), 0)
        self.assertEqual(self.flow.tick(), "delivered")
        self.assertEqual(len(self.active()), 1)
        self.assertEqual([row["outcome"] for row in self.flow.snapshot()["attempts"]], ["retry", "delivered"])

    def test_lost_ack_remains_unconfirmed_and_retries_same_provider_token(self):
        self.flow.accept("original", self.event)
        self.assertEqual(self.flow.tick("after_accept"), "retry")
        self.assertEqual(self.flow.snapshot()["outbox"][0]["status"], "retry")
        self.assertEqual(len(self.active()), 1)  # Provider acted; sender did not receive acknowledgement.
        self.flow.close()
        self.flow = Orchestrator(self.path)
        self.assertEqual(self.flow.tick(), "delivered")
        self.assertEqual(len(self.active()), 1)
        self.assertEqual(len(self.flow.snapshot()["provider_receipts"]), 1)

    def test_cancel_supersedes_pending_reschedule_and_late_event(self):
        self.flow.accept("original", self.event)
        self.flow.tick()
        self.flow.accept("reschedule", {**self.event, "type": "interview.rescheduled", "revision": 2})
        self.flow.accept("cancel", {"type": "interview.cancelled", "vacancy_id": "synthetic-01", "revision": 3})
        self.assertEqual(self.flow.tick(), "superseded")
        self.assertEqual(self.flow.tick(), "delivered")
        self.assertEqual(self.flow.accept("late", {**self.event, "revision": 2})["result"], "stale")
        self.assertEqual(self.flow.tick(), "idle")
        self.assertEqual(len(self.active()), 0)
        self.assertEqual(self.flow.snapshot()["provider_schedules"][0]["revision"], 3)
        self.assertEqual(len(self.flow.snapshot()["provider_receipts"]), 2)

    def test_cancel_supersedes_a_timed_out_schedule(self):
        self.flow.accept("original", self.event)
        self.flow.tick("after_accept")
        self.flow.accept("cancel", {"type": "interview.cancelled", "vacancy_id": "synthetic-01", "revision": 2})
        self.assertEqual(self.flow.tick(), "superseded")
        self.assertEqual(self.flow.tick(), "delivered")
        self.assertEqual(len(self.active()), 0)
        self.assertEqual(self.flow.snapshot()["outbox"][0]["status"], "superseded")

    def test_rescheduling_replaces_active_slot_without_adding_another_schedule(self):
        self.flow.accept("original", self.event)
        self.flow.tick()
        later = {**self.event, "type": "interview.rescheduled", "revision": 2,
                 "slot": "2026-11-06T16:00:00+00:00"}
        self.flow.accept("later", later)
        self.flow.tick()
        self.assertEqual(len(self.active()), 1)
        self.assertEqual(self.active()[0]["slot"], later["slot"])

    def test_invalid_revision_and_unzoned_timestamp_do_not_enqueue(self):
        for invalid in ({**self.event, "revision": True}, {**self.event, "revision": 0},
                        {**self.event, "slot": "2026-11-06T14:00:00"}):
            with self.assertRaises(ValueError):
                self.flow.accept("invalid", invalid)
        self.assertEqual(len(self.flow.snapshot()["outbox"]), 0)

    def test_demo_preserves_an_existing_file_without_opening_it(self):
        existing = Path(self.directory.name) / "unrelated.sqlite"
        original = b"An existing unrelated file must remain intact"
        existing.write_bytes(original)
        with self.assertRaises(ValueError):
            run(existing)
        self.assertEqual(existing.read_bytes(), original)

    def test_malformed_payload_and_event_type_reject_without_mutation(self):
        before = self.flow.snapshot()
        for payload in (None, [], "payload", {**self.event, "type": []}, {**self.event, "type": {}}):
            with self.assertRaises(ValueError):
                self.flow.accept("malformed", payload)
            self.assertEqual(self.flow.snapshot(), before)

    def test_oversized_revision_rejects_before_sqlite_without_mutation(self):
        before = self.flow.snapshot()
        with self.assertRaises(ValueError):
            self.flow.accept("oversized", {**self.event, "revision": 2**63})
        self.assertEqual(self.flow.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
