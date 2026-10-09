# Evidence check: a small Python QA sample

Synthetic diligence facts, intentionally flawed outputs, deterministic checks,
and a release gate. Python 3.10+ standard library only. No API keys, model calls,
external services or client data.

This is an independent sample, not a reported VouchAPI defect or a financial
audit. The reference is manually reviewed fixture data. The checker does not
extract documents, establish that a source is authentic, verify financial
statements, or certify compliance.

## Run

From this directory:

~~~powershell
python -m unittest discover -s tests -v
python evidence_check.py fixtures/reference.json fixtures/good.json
python evidence_check.py fixtures/reference.json fixtures/wrong-scale.json
python evidence_check.py fixtures/reference.json fixtures/wrong-number.json
python evidence_check.py fixtures/reference.json fixtures/incomplete.json
~~~

Tests return 0 when the positive and negative expectations hold. The gate returns
0 for PASS, 2 for a blocked candidate, and 3 for unreadable/malformed JSON or an
invalid reference. Its JSON report includes exact-file SHA-256 fingerprints.
CI should require gate exit 0 before releasing the corresponding output.

## Exact reproduction

1. Read fixtures/source.txt. Revenue is USD 1250 thousand in FY2025.
2. The good output claims USD 1.25 million: PASS after exact unit normalization.
3. The wrong-scale job reports status success and copies the source's 1250.00
   figure as millions instead of thousands. The citation is correct, but
   the amount is 1000 times too large: BLOCK, AMOUNT_MISMATCH, exit 2.
   Expected base units 1250000.00, observed 1250000000.00.
4. The wrong-number output claims USD 1.30 million with the same correct
   document, page, locator, metric, currency and period: BLOCK, AMOUNT_MISMATCH.
   Expected base units 1250000.00, observed 1300000.00.
5. The incomplete output omits net debt: BLOCK, MISSING_FACT. A job returning
   JSON successfully does not establish that extraction finished.

Other regressions cover wrong scale, currency, period, metric and citation;
duplicate/unknown claims; missing fields; malformed amounts, nonfinite values,
JSON numbers, duplicate JSON keys, invalid source pages/dates; corrupted
references; exact large Decimal values and signed financial amounts; and JSON
integer tokens exceeding the parser's explicit 64-digit bound without a traceback.

## Contract and trust boundary

Each required reference fact has a reviewed metric, decimal string, scale,
currency, period and exact document/page/locator. All required claims must occur
exactly once. Candidate claims cannot add unsupported facts or fields.
Candidate input is an exact envelope with status and claims. A success status
is necessary and insufficient: each financial claim must pass independently.

Scale may be ones, thousands or millions. Equivalent representations pass;
currency conversion, rounding tolerance and inferred scales are deliberately
absent. Amounts accept up to 24 integer and 6 fractional digits. No float
coercion, exponents, thousands separators, NaN or infinity.
JSON integer tokens are bounded before conversion, independently of the Python
runtime's integer-string limit. Parser errors produce a structured BLOCK report.

A citation is checked against the reviewed fact's expected locator. It cannot
make an inconsistent amount pass. Reference preparation still requires a human
to map the source correctly; a consistently wrong reference and candidate can
pass. SHA-256 identifies the exact inputs, not their authenticity.

Only these structured claims are covered. Free-form narrative, calculations,
cross-document reconciliation and document extraction are outside this sample.

## Proposed deployment gate policy

- All reported issues are critical in this deliberately narrow contract.
  Missing, malformed, unsupported or conflicting evidence blocks release.
- The release pipeline records both input hashes, the full report and the
  checker revision. It requires exit 0 for that exact candidate/reference pair.
  A passing test suite alone is insufficient to release a different output.
- A blocked job stays blocked and retains its evidence. Correct the candidate
  or review the source mapping, then run the gate again. Do not silently drop a
  bad claim, convert an unknown currency, or broaden a numeric tolerance.
- Reference corrections require a responsible engineer plus an independent
  data reviewer to record the source evidence, rationale and new reference
  revision. These are proposed responsibilities, not claimed VouchAPI policy.
- This sample exposes no override flag. An urgent override would require a
  separate signed exception record with both hashes, named accountable
  approver, evidence, reason, scope and expiry. It must preserve the original
  BLOCK report. Under this proposed policy, known wrong numbers, malformed
  amounts and missing required facts cannot be waived into a client deliverable.
  Fix and rerun instead. Authentication/exception workflow is not implemented.

This boundary makes the artifact useful as a reproducible QA conversation
starter without presenting a toy checker as an entire diligence platform.
