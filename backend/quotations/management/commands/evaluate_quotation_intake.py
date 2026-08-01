import json
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from quotations.golden_evaluation import (
    GoldenCorpusError,
    evaluate_golden_predictions,
    load_golden_corpus,
)


class Command(BaseCommand):
    help = "Validate the synthetic quotation-intake corpus and optionally score offline predictions."

    def add_arguments(self, parser):
        parser.add_argument("--corpus", help="Optional corpus JSON path.")
        parser.add_argument("--predictions", help="Optional offline predictions JSON path.")

    def handle(self, *args, **options):
        try:
            corpus = load_golden_corpus(options.get("corpus"))
            predictions = {}
            if options.get("predictions"):
                prediction_path = Path(options["predictions"])
                predictions = json.loads(prediction_path.read_text(encoding="utf-8"))
                if isinstance(predictions, dict) and "predictions" in predictions:
                    predictions = predictions["predictions"]
                if not isinstance(predictions, dict):
                    raise GoldenCorpusError("Predictions JSON must be keyed by golden case id.")
                report = evaluate_golden_predictions(corpus, predictions)
            else:
                report = {
                    "schema_version": corpus["schema_version"],
                    "corpus_id": corpus.get("corpus_id") or "",
                    "validation": "passed",
                    "case_count": len(corpus["cases"]),
                    "category_counts": dict(
                        sorted(Counter(case["category"] for case in corpus["cases"]).items())
                    ),
                    "routes": dict(
                        sorted(Counter(case["route"] for case in corpus["cases"]).items())
                    ),
                    "provenance": corpus["provenance"],
                }
        except (OSError, json.JSONDecodeError, GoldenCorpusError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
