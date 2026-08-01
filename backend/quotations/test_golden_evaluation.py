import copy
import io
import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase

from quotations.golden_evaluation import (
    GoldenCorpusError,
    evaluate_golden_predictions,
    load_golden_corpus,
    score_golden_case,
    validate_golden_corpus,
)


class GoldenEvaluationCorpusTests(SimpleTestCase):
    def setUp(self):
        self.corpus = load_golden_corpus()

    def test_corpus_is_large_enough_diverse_and_fully_synthetic(self):
        self.assertGreaterEqual(len(self.corpus["cases"]), 30)
        self.assertEqual(
            self.corpus["provenance"]["classification"],
            "fully_synthetic",
        )
        self.assertIs(self.corpus["provenance"]["contains_customer_data"], False)
        self.assertEqual(
            {case["route"] for case in self.corpus["cases"]},
            {"gmail", "manual"},
        )
        self.assertTrue(
            {
                "clean_excel",
                "messy_multi_sheet_excel",
                "selectable_text_pdf",
                "scanned_pdf",
                "email_body_table",
                "initial_follow_up",
                "partial_revision",
                "full_replacement_revision",
                "conflicting_documents",
                "similar_company_branches",
            }.issubset({case["category"] for case in self.corpus["cases"]})
        )
        message_usages = {
            usage
            for case in self.corpus["cases"]
            for usage in case["expected"]["message_usage"].values()
        }
        self.assertEqual(message_usages, {"used", "context", "excluded"})

    def test_perfect_predictions_score_one_and_report_only_known_observations(self):
        predictions = {}
        latencies = [100, 200, 300]
        for index, case in enumerate(self.corpus["cases"]):
            prediction = copy.deepcopy(case["expected"])
            if index < len(latencies):
                prediction["observation"] = {
                    "latency_ms": latencies[index],
                    "input_tokens": 10,
                    "cached_input_tokens": 2,
                    "output_tokens": 3,
                    "total_tokens": 13,
                }
            predictions[case["id"]] = prediction
        predictions["unknown-case"] = {
            "observation": {"latency_ms": 99_999, "total_tokens": 99_999}
        }

        report = evaluate_golden_predictions(self.corpus, predictions)

        self.assertTrue(all(value == 1.0 for value in report["quality"].values()))
        self.assertEqual(report["prediction_count"], len(self.corpus["cases"]))
        self.assertEqual(report["performance"]["latency_observed_cases"], 3)
        self.assertEqual(report["performance"]["token_observed_cases"], 3)
        self.assertEqual(
            report["performance"]["latency_ms"],
            {"p50": 200.0, "p90": 280.0, "p95": 290.0},
        )
        self.assertEqual(
            report["performance"]["cost_basis"],
            {
                "input_tokens": 30,
                "cached_input_tokens": 6,
                "output_tokens": 9,
                "total_tokens": 39,
            },
        )
        self.assertEqual(
            report["by_route"]["manual"]["performance"]["latency_observed_cases"],
            3,
        )
        self.assertEqual(
            report["by_route"]["gmail"]["performance"]["latency_observed_cases"],
            0,
        )
        self.assertIsNone(
            report["by_route"]["manual"]["quality"]["message_usage_accuracy"]
        )

    def test_scorer_penalizes_false_rows_wrong_fields_and_unsafe_price(self):
        case = copy.deepcopy(self.corpus["cases"][0])
        prediction = copy.deepcopy(case["expected"])
        prediction["items"][0]["name"] = prediction["items"][0]["name"].lower()
        prediction["items"][1].update(
            {
                "quantity": "999",
                "unit": "CARTON",
                "operation": "changed",
                "parse_status": "needs_review",
                "selling_price": "1.00",
                "citations": [
                    {
                        "source_id": "unknown-source",
                        "location": "invented",
                        "excerpt": "invented",
                    }
                ],
            }
        )
        prediction["items"].append(
            {
                "name": "Dear team",
                "quantity": "1",
                "unit": "PCS",
                "operation": "added",
                "parse_status": "parsed",
                "selling_price": "",
                "citations": [],
            }
        )
        prediction["identity"]["company"] = "Invented Company"
        prediction["ambiguities"] = ["invented_ambiguity"]

        score = score_golden_case(case, prediction)

        self.assertAlmostEqual(score["row_precision"], 1 / 3)
        self.assertEqual(score["row_recall"], 0.5)
        self.assertEqual(score["item_name_exact"], 0.5)
        self.assertEqual(score["quantity_exact"], 0.0)
        self.assertEqual(score["unit_exact"], 0.0)
        self.assertEqual(score["revision_operation_exact"], 0.0)
        self.assertEqual(score["citation_valid"], 0.0)
        self.assertEqual(score["parse_status_exact"], 0.0)
        self.assertEqual(score["blank_selling_price"], 0.0)
        self.assertEqual(score["identity_accuracy"], 0.0)
        self.assertEqual(score["ambiguity_accuracy"], 0.0)

    def test_scorer_counts_duplicate_predictions_as_false_positives(self):
        case = copy.deepcopy(self.corpus["cases"][0])
        prediction = copy.deepcopy(case["expected"])
        prediction["items"].append(copy.deepcopy(prediction["items"][0]))

        score = score_golden_case(case, prediction)

        self.assertAlmostEqual(score["row_precision"], 2 / 3)
        self.assertEqual(score["row_recall"], 1.0)

    def test_scorer_rejects_unsafe_price_or_missing_evidence_on_false_row(self):
        case = copy.deepcopy(self.corpus["cases"][0])
        prediction = copy.deepcopy(case["expected"])
        prediction["items"].append(
            {
                "name": "Signature Office Chair",
                "quantity": "500",
                "unit": "PCS",
                "operation": "added",
                "parse_status": "parsed",
                "selling_price": "99",
                "citations": [],
            }
        )

        score = score_golden_case(case, prediction)

        self.assertEqual(score["blank_selling_price"], 0.0)
        self.assertAlmostEqual(score["citation_valid"], 2 / 3)

    def test_scorer_rejects_unsafe_price_on_unnamed_row(self):
        case = copy.deepcopy(self.corpus["cases"][0])
        prediction = copy.deepcopy(case["expected"])
        prediction["items"].append({"name": "", "selling_price": "123"})

        score = score_golden_case(case, prediction)

        self.assertEqual(score["blank_selling_price"], 0.0)
        self.assertAlmostEqual(score["row_precision"], 2 / 3)

    def test_scorer_requires_exact_and_complete_citation_set(self):
        case = copy.deepcopy(self.corpus["cases"][19])
        prediction = copy.deepcopy(case["expected"])
        prediction["items"][0]["citations"] = prediction["items"][0]["citations"][:1]
        self.assertEqual(score_golden_case(case, prediction)["citation_valid"], 0.5)

        prediction = copy.deepcopy(case["expected"])
        prediction["items"][0]["citations"][0]["excerpt"] = (
            prediction["items"][1]["citations"][0]["excerpt"]
        )
        self.assertEqual(score_golden_case(case, prediction)["citation_valid"], 0.5)

    def test_scorer_checks_customer_price_evidence(self):
        case = copy.deepcopy(self.corpus["cases"][3])
        prediction = copy.deepcopy(case["expected"])
        prediction["items"][0].pop("customer_price_evidence")

        score = score_golden_case(case, prediction)

        self.assertEqual(score["customer_price_evidence_exact"], 0.0)

    def test_report_separates_invalid_predictions_and_observation_samples(self):
        valid_case = self.corpus["cases"][0]
        invalid_case = self.corpus["cases"][1]
        malformed_observation_case = self.corpus["cases"][2]
        predictions = {
            valid_case["id"]: {
                **copy.deepcopy(valid_case["expected"]),
                "observation": {"input_tokens": 25},
            },
            invalid_case["id"]: None,
            malformed_observation_case["id"]: {
                **copy.deepcopy(malformed_observation_case["expected"]),
                "observation": ["not", "an", "object"],
            },
        }

        report = evaluate_golden_predictions(self.corpus, predictions)

        self.assertEqual(report["prediction_count"], 2)
        self.assertEqual(report["invalid_prediction_case_ids"], [invalid_case["id"]])
        self.assertNotIn(invalid_case["id"], report["missing_case_ids"])
        self.assertEqual(
            len(report["missing_case_ids"]), len(self.corpus["cases"]) - 3
        )
        self.assertEqual(report["performance"]["latency_observed_cases"], 0)
        self.assertEqual(report["performance"]["token_observed_cases"], 1)
        self.assertEqual(report["performance"]["cost_basis"]["input_tokens"], 25)

    def test_scorer_penalizes_unknown_message_and_missing_identity_evidence(self):
        case = copy.deepcopy(self.corpus["cases"][16])
        prediction = copy.deepcopy(case["expected"])
        prediction["message_usage"]["invented-message"] = "used"
        prediction["identity"]["source_ids"] = []

        score = score_golden_case(case, prediction)

        self.assertEqual(score["message_usage_accuracy"], 0.75)
        self.assertEqual(score["identity_accuracy"], 1.0)
        self.assertEqual(score["identity_evidence_exact"], 0.0)

    def test_validator_rejects_non_synthetic_email(self):
        corpus = copy.deepcopy(self.corpus)
        corpus["cases"][13]["input"]["messages"][0]["from"] = (
            "Person <person@customer.com>"
        )

        with self.assertRaisesRegex(GoldenCorpusError, r"reserved \.test"):
            validate_golden_corpus(corpus)

    def test_validator_rejects_unverifiable_citation(self):
        corpus = copy.deepcopy(self.corpus)
        corpus["cases"][0]["expected"]["items"][0]["citations"][0][
            "excerpt"
        ] = "text absent from source"

        with self.assertRaisesRegex(GoldenCorpusError, "not present"):
            validate_golden_corpus(corpus)

    def test_validator_rejects_nonblank_selling_price(self):
        corpus = copy.deepcopy(self.corpus)
        corpus["cases"][0]["expected"]["items"][0]["selling_price"] = "25"

        with self.assertRaisesRegex(GoldenCorpusError, "selling prices must remain blank"):
            validate_golden_corpus(corpus)

    def test_validator_rejects_unclassified_or_customer_provenance(self):
        for provenance in (
            {"classification": "unknown", "contains_customer_data": False},
            {"classification": "fully_synthetic", "contains_customer_data": True},
        ):
            corpus = copy.deepcopy(self.corpus)
            corpus["provenance"] = provenance
            with self.subTest(provenance=provenance):
                with self.assertRaisesRegex(GoldenCorpusError, "fully synthetic"):
                    validate_golden_corpus(corpus)

    def test_management_command_validates_without_predictions_or_database(self):
        stdout = io.StringIO()

        call_command("evaluate_quotation_intake", stdout=stdout)

        report = json.loads(stdout.getvalue())
        self.assertEqual(report["validation"], "passed")
        self.assertEqual(report["case_count"], len(self.corpus["cases"]))
        self.assertEqual(report["routes"], {"gmail": 17, "manual": 13})
        self.assertNotIn("quality", report)

    def test_management_command_scores_offline_prediction_file(self):
        case = self.corpus["cases"][0]
        payload = {"predictions": {case["id"]: copy.deepcopy(case["expected"])}}
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json", delete=False
            ) as handle:
                json.dump(payload, handle)
                path = Path(handle.name)
            stdout = io.StringIO()

            call_command(
                "evaluate_quotation_intake",
                predictions=str(path),
                stdout=stdout,
            )

            report = json.loads(stdout.getvalue())
            self.assertEqual(report["prediction_count"], 1)
            self.assertEqual(report["case_count"], len(self.corpus["cases"]))
        finally:
            if path:
                path.unlink(missing_ok=True)
