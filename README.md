# Backend reliability fixtures

Two small independent Python examples built around practical engineering failure modes.
All data is synthetic. Python standard library only; no API keys or external services.

- [Evidence release gate](evidence-release-gate): a correct citation must not hide a wrong amount, scale, period, currency or missing claim. Exact Decimal comparisons, structured reports, input hashes and CI exit codes. 25 regression tests.
- [Interview coordination](interview-orchestration): persisted deduplication, an outbox, observable retries, cancellation and stale revisions. A local mock provider models durable idempotency tokens. 11 regression tests.

## Run

From each sample folder, run the commands in its README. No installation is required.
The GitHub workflow runs both suites under Python 3.11 and 3.13.

## Short walkthroughs

- [Correct citation, wrong number (PDF)](docs/evidence-gate.pdf)
- [Retries without a second interview (PDF)](docs/interview-coordination.pdf)

## Boundaries

These are runnable local fixtures, not production deployments or findings about a real customer.
The evidence gate relies on manually reviewed reference facts and does not extract or authenticate documents.
The scheduling mock has explicit provider capabilities that a real integration must verify or reconcile.
No real candidate is scored or selected. No distributed exactly-once claim is made.
