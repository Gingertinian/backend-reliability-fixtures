"""A small structured-output release gate against manually reviewed facts."""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re
import sys


UNITS = {"ones": Decimal("1"), "thousands": Decimal("1000"),
         "millions": Decimal("1000000")}
FACT_KEYS = {"fact_id", "metric", "amount", "unit", "currency", "period", "source"}
SOURCE_KEYS = {"document_id", "page", "locator"}
AMOUNT = re.compile(r"[+-]?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,6})?")
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,79}")
MAX_ROWS = 1000
MAX_FILE_BYTES = 1_000_000
MAX_INTEGER_DIGITS = 64


class InvalidInput(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def issue(code: str, message: str, fact_id: str | None = None) -> dict:
    return {"code": code, "severity": "critical",
            "fact_id": fact_id, "message": message}


def _rows(payload: object, key: str, *, allow_empty: bool) -> list:
    keys = {key} if key == "facts" else {"status", key}
    if not isinstance(payload, dict) or set(payload) != keys:
        raise InvalidInput("SCHEMA_INVALID", f"Expected exact fields: {', '.join(sorted(keys))}.")
    if key == "claims" and payload["status"] != "success":
        raise InvalidInput("JOB_NOT_SUCCESS", "The extraction job did not report success.")
    rows = payload[key]
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise InvalidInput("SCHEMA_INVALID", f"'{key}' must be a bounded array.")
    if not rows and not allow_empty:
        raise InvalidInput("SCHEMA_INVALID", "Reference facts cannot be empty.")
    return rows


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise InvalidInput("FIELD_INVALID", f"Invalid {field} identifier.")
    return value


def _period(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidInput("PERIOD_FORMAT", "Period must be a string.")
    if re.fullmatch(r"FY[0-9]{4}", value) and int(value[2:]) >= 1:
        return value
    if re.fullmatch(r"as_of:[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            date.fromisoformat(value[6:])
            return value
        except ValueError:
            pass
    raise InvalidInput("PERIOD_FORMAT", "Use FYyyyy or a valid as_of:yyyy-mm-dd.")


def _fact(row: object) -> dict:
    if not isinstance(row, dict) or set(row) != FACT_KEYS:
        raise InvalidInput("SCHEMA_INVALID", "Claim fields are missing or unexpected.")
    fact_id = _identifier(row["fact_id"], "fact_id")
    metric = _identifier(row["metric"], "metric")
    raw_amount = row["amount"]
    # No float coercion, scientific notation, grouping commas, NaN or infinity.
    if not isinstance(raw_amount, str) or not AMOUNT.fullmatch(raw_amount):
        raise InvalidInput("AMOUNT_FORMAT",
                           "Amount must be a bounded decimal string, up to 6 fractional digits.")
    unit = row["unit"]
    if not isinstance(unit, str) or unit not in UNITS:
        raise InvalidInput("UNIT_FORMAT", "Unit must be ones, thousands or millions.")
    amount = Decimal(raw_amount)
    if not amount.is_finite():
        raise InvalidInput("AMOUNT_FORMAT", "Non-finite amounts are forbidden.")
    # At most 24 integer + 6 fractional + 6 multiplier digits; no rounding.
    with localcontext() as context:
        context.prec = 40
        canonical_amount = amount * UNITS[unit]
    currency = row["currency"]
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise InvalidInput("CURRENCY_FORMAT", "Currency must be a three-letter uppercase code.")
    source = row["source"]
    if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
        raise InvalidInput("SOURCE_FORMAT", "Source fields are missing or unexpected.")
    document_id = _identifier(source["document_id"], "document_id")
    page = source["page"]
    if type(page) is not int or not 1 <= page <= 100000:
        raise InvalidInput("SOURCE_FORMAT", "Source page must be a positive integer.")
    locator = source["locator"]
    if not isinstance(locator, str) or not locator.strip() or len(locator) > 160:
        raise InvalidInput("SOURCE_FORMAT", "Source locator must be a nonempty bounded string.")
    return {"fact_id": fact_id, "metric": metric,
            "canonical_amount": canonical_amount, "currency": currency,
            "period": _period(row["period"]),
            "source": (document_id, page, locator)}


def check(reference: object, candidate: object) -> dict:
    """Return BLOCK on any malformed, unsupported, absent or conflicting claim."""
    expected = {}
    try:
        for row in _rows(reference, "facts", allow_empty=False):
            fact = _fact(row)
            fact_id = fact["fact_id"]
            if fact_id in expected:
                raise InvalidInput("DUPLICATE_FACT", "Duplicate fact_id in reference.")
            expected[fact_id] = fact
    except InvalidInput as error:
        return {"gate": "BLOCK", "required_claims": None, "matched_claims": 0,
                "issues": [issue("REFERENCE_INVALID", f"{error.code}: {error}")]}

    issues = []
    seen = set()
    matched = 0
    try:
        rows = _rows(candidate, "claims", allow_empty=True)
    except InvalidInput as error:
        return {"gate": "BLOCK", "required_claims": len(expected), "matched_claims": 0,
                "issues": [issue(error.code, str(error))]}
    for row in rows:
        label = row.get("fact_id") if isinstance(row, dict) else None
        label = label if isinstance(label, str) else None
        try:
            fact = _fact(row)
        except InvalidInput as error:
            issues.append(issue(error.code, str(error), label))
            continue
        fact_id = fact["fact_id"]
        if fact_id in seen:
            issues.append(issue("DUPLICATE_FACT", "A fact is claimed more than once.", fact_id))
            continue
        seen.add(fact_id)
        if fact_id not in expected:
            issues.append(issue("UNKNOWN_FACT", "No reviewed reference for this fact.", fact_id))
            continue
        baseline = expected[fact_id]
        before = len(issues)
        for field, code in (("metric", "METRIC_MISMATCH"),
                            ("currency", "CURRENCY_MISMATCH"),
                            ("period", "PERIOD_MISMATCH"),
                            ("source", "SOURCE_MISMATCH")):
            if fact[field] != baseline[field]:
                issues.append(issue(code, f"{field} differs from the reviewed reference.", fact_id))
        if fact["canonical_amount"] != baseline["canonical_amount"]:
            wanted = format(baseline["canonical_amount"], "f")
            actual = format(fact["canonical_amount"], "f")
            issues.append(issue("AMOUNT_MISMATCH",
                                f"Base-unit amount differs: expected {wanted}, observed {actual}.",
                                fact_id))
        if len(issues) == before:
            matched += 1
    for fact_id in sorted(set(expected) - seen):
        issues.append(issue("MISSING_FACT", "Required fact is absent or malformed.", fact_id))
    return {"gate": "PASS" if not issues else "BLOCK",
            "required_claims": len(expected), "matched_claims": matched,
            "issues": issues}


def _unique_object(pairs: list) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise InvalidInput("INPUT_JSON", "Duplicate JSON object keys are forbidden.")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise InvalidInput("INPUT_JSON", "Non-finite JSON constants are forbidden.")


def _bounded_integer(value: str) -> int:
    if len(value.lstrip("-")) > MAX_INTEGER_DIGITS:
        raise InvalidInput("INPUT_JSON", "JSON integer token exceeds the 64-digit sample limit.")
    return int(value)


def load_json(path: Path) -> tuple[object, str]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise InvalidInput("INPUT_JSON", "Input exceeds the 1 MB sample limit.")
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise InvalidInput("INPUT_JSON", "Input exceeds the 1 MB sample limit.")
        value = json.loads(data.decode("utf-8"),
                           object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant,
                           parse_int=_bounded_integer)
        return value, hashlib.sha256(data).hexdigest()
    except InvalidInput:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise InvalidInput("INPUT_JSON", "Cannot read a valid bounded UTF-8 JSON input.") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args(argv)
    try:
        reference, reference_hash = load_json(args.reference)
        candidate, candidate_hash = load_json(args.candidate)
        report = check(reference, candidate)
        report["reference_sha256"] = reference_hash
        report["candidate_sha256"] = candidate_hash
    except InvalidInput as error:
        report = {"gate": "BLOCK", "issues": [issue(error.code, str(error))]}
        print(json.dumps(report, indent=2, sort_keys=True))
        return 3
    print(json.dumps(report, indent=2, sort_keys=True))
    if any(item["code"] == "REFERENCE_INVALID" for item in report["issues"]):
        return 3
    return 0 if report["gate"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
