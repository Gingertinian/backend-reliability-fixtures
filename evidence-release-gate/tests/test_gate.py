import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evidence_check import check, load_json  # noqa: E402
USE_DEFAULT = object()


class EvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.reference = json.loads((ROOT / "fixtures/reference.json").read_text())
        self.good = json.loads((ROOT / "fixtures/good.json").read_text())

    def assert_block(self, candidate, code, reference=USE_DEFAULT):
        result = check(self.reference if reference is USE_DEFAULT else reference, candidate)
        self.assertEqual(result["gate"], "BLOCK")
        self.assertIn(code, {item["code"] for item in result["issues"]})
        self.assertTrue(all(item["severity"] == "critical" for item in result["issues"]))
        return result

    def cli(self, candidate):
        return subprocess.run(
            [sys.executable, str(ROOT / "evidence_check.py"),
             str(ROOT / "fixtures/reference.json"), str(candidate)],
            capture_output=True, text=True, timeout=5,
        )

    def test_good_fixture_accepts_equivalent_units_not_identical_strings(self):
        result = check(self.reference, self.good)
        self.assertEqual(result["gate"], "PASS")
        self.assertEqual(result["matched_claims"], 3)
        self.assertEqual(result["issues"], [])

    def test_right_citation_does_not_certify_wrong_number(self):
        wrong = json.loads((ROOT / "fixtures/wrong-number.json").read_text())
        self.assertEqual(wrong["claims"][0]["source"], self.good["claims"][0]["source"])
        result = self.assert_block(wrong, "AMOUNT_MISMATCH")
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["matched_claims"], 2)

    def test_nominally_successful_but_incomplete_output_is_blocked(self):
        candidate = json.loads((ROOT / "fixtures/incomplete.json").read_text())
        result = self.assert_block(candidate, "MISSING_FACT")
        self.assertEqual(result["issues"][0]["fact_id"], "net-debt-year-end")

    def test_same_digits_wrong_scale_is_blocked(self):
        self.good["claims"][0]["unit"] = "ones"
        self.assert_block(self.good, "AMOUNT_MISMATCH")

    def test_success_status_and_correct_citation_cannot_hide_thousandfold_error(self):
        candidate = json.loads((ROOT / "fixtures/wrong-scale.json").read_text())
        self.assertEqual(candidate["status"], "success")
        self.assertEqual(candidate["claims"][0]["source"], self.good["claims"][0]["source"])
        self.assertEqual(candidate["claims"][0]["amount"],
                         self.reference["facts"][0]["amount"])
        result = self.assert_block(candidate, "AMOUNT_MISMATCH")
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("1250000000.00", result["issues"][0]["message"])

    def test_failed_job_status_cannot_be_certified_by_good_claims(self):
        self.good["status"] = "error"
        self.assert_block(self.good, "JOB_NOT_SUCCESS")

    def test_currency_is_not_silently_converted(self):
        self.good["claims"][0]["currency"] = "EUR"
        self.assert_block(self.good, "CURRENCY_MISMATCH")

    def test_wrong_period_cannot_borrow_current_year_citation(self):
        self.good["claims"][0]["period"] = "FY2024"
        self.assert_block(self.good, "PERIOD_MISMATCH")

    def test_wrong_metric_is_blocked_even_with_matching_amount(self):
        self.good["claims"][0]["metric"] = "gross_profit"
        self.assert_block(self.good, "METRIC_MISMATCH")

    def test_unrelated_document_page_or_locator_is_blocked(self):
        for field, value in (("document_id", "synthetic-other"),
                             ("page", 5), ("locator", "ebitda-fy2025")):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.good)
                candidate["claims"][0]["source"][field] = value
                self.assert_block(candidate, "SOURCE_MISMATCH")

    def test_duplicates_do_not_mask_omissions(self):
        candidate = copy.deepcopy(self.good)
        candidate["claims"][2] = copy.deepcopy(candidate["claims"][0])
        result = self.assert_block(candidate, "DUPLICATE_FACT")
        self.assertIn("MISSING_FACT", {item["code"] for item in result["issues"]})

    def test_unsupported_claim_is_blocked(self):
        self.good["claims"][0]["fact_id"] = "invented-fact"
        self.assert_block(self.good, "UNKNOWN_FACT")

    def test_nonfinite_malformed_and_unbounded_amounts_fail_closed(self):
        for amount in ("NaN", "sNaN", "Infinity", "-Infinity", "1e309", "1,250",
                       "", " 1.25", "01.25", "1.1234567", "9" * 25):
            with self.subTest(amount=amount):
                candidate = copy.deepcopy(self.good)
                candidate["claims"][0]["amount"] = amount
                self.assert_block(candidate, "AMOUNT_FORMAT")

    def test_json_numbers_and_bool_are_not_decimal_strings(self):
        for amount in (1.25, 1250, True, None, [], {}):
            with self.subTest(amount=amount):
                candidate = copy.deepcopy(self.good)
                candidate["claims"][0]["amount"] = amount
                self.assert_block(candidate, "AMOUNT_FORMAT")

    def test_unknown_unit_cannot_be_guessed(self):
        self.good["claims"][0]["unit"] = "billions"
        self.assert_block(self.good, "UNIT_FORMAT")

    def test_invalid_dates_and_boolean_page_fail_closed(self):
        self.good["claims"][2]["period"] = "as_of:2025-02-30"
        self.assert_block(self.good, "PERIOD_FORMAT")
        self.good["claims"][0]["source"]["page"] = True
        self.assert_block(self.good, "SOURCE_FORMAT")

    def test_extra_fields_and_empty_outputs_fail_closed(self):
        self.good["approval"] = "certified"
        self.assert_block(self.good, "SCHEMA_INVALID")
        self.assert_block({"status": "success", "claims": []}, "MISSING_FACT")
        self.assert_block({"status": "success", "claims": [None]}, "SCHEMA_INVALID")

    def test_reference_corruption_cannot_create_a_pass(self):
        for reference in ({"facts": []}, None,
                          {"facts": self.reference["facts"] + self.reference["facts"][:1]}):
            with self.subTest(reference=reference):
                self.assert_block(self.good, "REFERENCE_INVALID", reference)
        reference = copy.deepcopy(self.reference)
        reference["facts"][0]["amount"] = "NaN"
        self.assert_block(self.good, "REFERENCE_INVALID", reference)

    def test_amount_comparison_is_exact_without_tolerance(self):
        self.good["claims"][0]["amount"] = "1.250001"
        self.assert_block(self.good, "AMOUNT_MISMATCH")

    def test_decimal_precision_does_not_round_large_valid_values(self):
        reference = copy.deepcopy(self.reference)
        candidate = copy.deepcopy(self.good)
        value = "123456789012345678901234.123456"
        reference["facts"][0]["amount"] = value
        reference["facts"][0]["unit"] = "millions"
        candidate["claims"][0]["amount"] = value
        candidate["claims"][0]["unit"] = "millions"
        self.assertEqual(check(reference, candidate)["gate"], "PASS")
        candidate["claims"][0]["amount"] = "123456789012345678901234.123455"
        self.assert_block(candidate, "AMOUNT_MISMATCH", reference)

    def test_signed_financial_amounts_remain_exact(self):
        reference = copy.deepcopy(self.reference)
        candidate = copy.deepcopy(self.good)
        reference["facts"][1]["amount"] = "-187.50"
        candidate["claims"][1]["amount"] = "-187500"
        self.assertEqual(check(reference, candidate)["gate"], "PASS")

    def test_cli_pass_and_block_exit_codes_with_input_hashes(self):
        for name, exit_code, gate in (("good.json", 0, "PASS"),
                                     ("wrong-number.json", 2, "BLOCK"),
                                     ("wrong-scale.json", 2, "BLOCK"),
                                     ("incomplete.json", 2, "BLOCK")):
            with self.subTest(name=name):
                result = self.cli(ROOT / "fixtures" / name)
                self.assertEqual(result.returncode, exit_code, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["gate"], gate)
                self.assertEqual(len(report["reference_sha256"]), 64)
                self.assertEqual(len(report["candidate_sha256"]), 64)

    def test_cli_rejects_invalid_json_and_duplicate_keys(self):
        bad_inputs = ('{"claims": [', '{"claims": [], "claims": []}',
                      '{"claims": [NaN]}',
                      '{"claims": [{"amount":"1.25","amount":"1.30"}]}')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for content in bad_inputs:
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    result = self.cli(path)
                    self.assertEqual(result.returncode, 3, result.stderr)
                    self.assertEqual(json.loads(result.stdout)["gate"], "BLOCK")

    def test_missing_file_and_oversized_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            result = self.cli(path)
            self.assertEqual(result.returncode, 3)
            path.write_text(" " * 1_000_001)
            result = self.cli(path)
            self.assertEqual(result.returncode, 3)
            self.assertEqual(json.loads(result.stdout)["gate"], "BLOCK")

    def test_unbounded_integer_token_returns_structured_block_without_traceback(self):
        oversized_token = "9" * 5000
        bad_inputs = (
            '{"status":"success","claims":' + oversized_token + '}',
            json.dumps(self.good).replace('"page": 4', '"page": ' + oversized_token, 1),
            json.dumps(self.good).replace('"page": 4', '"page": ' + ("9" * 65), 1),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large-integer.json"
            for content in bad_inputs:
                with self.subTest(length=len(content)):
                    self.assertLess(len(content.encode("utf-8")), 1_000_000)
                    path.write_text(content, encoding="utf-8")
                    result = self.cli(path)
                    self.assertEqual(result.returncode, 3)
                    self.assertEqual(result.stderr, "")
                    report = json.loads(result.stdout)
                    self.assertEqual(report["gate"], "BLOCK")
                    self.assertEqual(report["issues"][0]["code"], "INPUT_JSON")


if __name__ == "__main__":
    unittest.main()
