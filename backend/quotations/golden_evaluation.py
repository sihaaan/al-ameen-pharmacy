"""Offline scoring for the synthetic quotation-intake golden corpus.

The module never calls an AI provider and never reads production data.  It is
intended to make later prompt/model/pipeline experiments comparable before any
production behavior is changed.
"""

import json
import math
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path


GOLDEN_CORPUS_SCHEMA_VERSION = "quotation_intake_golden_v1"
DEFAULT_GOLDEN_CORPUS_PATH = (
    Path(__file__).resolve().parent
    / "evaluation_corpus"
    / "quotation_intake_v1.json"
)
VALID_ROUTES = {"manual", "gmail"}
VALID_MESSAGE_USAGE = {"used", "context", "excluded"}
VALID_MESSAGE_DIRECTIONS = {"inbound", "outbound"}
VALID_IDENTITY_RESOLUTIONS = {"resolved", "unresolved", "ambiguous"}
VALID_OPERATIONS = {
    "added",
    "changed",
    "removed",
    "unchanged",
    "duplicate",
    "uncertain",
}
VALID_PARSE_STATUSES = {"parsed", "needs_review"}
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@([A-Z0-9.-]+)", re.IGNORECASE)


class GoldenCorpusError(ValueError):
    """Raised when a corpus or prediction violates the offline contract."""


def _normalized_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _collapsed_text(value):
    """Collapse layout whitespace without changing customer-authored casing."""

    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalized_decimal(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return value
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _source_map(case):
    return {
        str(source.get("id") or ""): source
        for source in ((case.get("input") or {}).get("sources") or [])
    }


def _validate_synthetic_emails(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    invalid = sorted(
        {
            match.group(0)
            for match in EMAIL_RE.finditer(encoded)
            if not match.group(1).lower().endswith(".test")
        }
    )
    if invalid:
        raise GoldenCorpusError(
            "Golden corpus email addresses must use reserved .test domains: "
            + ", ".join(invalid)
        )


def validate_golden_corpus(corpus):
    if not isinstance(corpus, dict):
        raise GoldenCorpusError("Golden corpus must be a JSON object.")
    if corpus.get("schema_version") != GOLDEN_CORPUS_SCHEMA_VERSION:
        raise GoldenCorpusError("Unsupported golden corpus schema version.")
    provenance = corpus.get("provenance") or {}
    if (
        provenance.get("classification") != "fully_synthetic"
        or provenance.get("contains_customer_data") is not False
    ):
        raise GoldenCorpusError(
            "Golden corpus must explicitly declare fully synthetic provenance."
        )
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        raise GoldenCorpusError("Golden corpus must contain at least one case.")
    _validate_synthetic_emails(corpus)

    seen_ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise GoldenCorpusError("Every golden case must be a JSON object.")
        case_id = str((case or {}).get("id") or "")
        if not case_id or case_id in seen_ids:
            raise GoldenCorpusError(f"Golden case id is missing or duplicated: {case_id!r}.")
        seen_ids.add(case_id)
        if case.get("route") not in VALID_ROUTES:
            raise GoldenCorpusError(f"{case_id}: invalid route.")
        if not str(case.get("category") or "").strip():
            raise GoldenCorpusError(f"{case_id}: category is required.")

        input_payload = case.get("input")
        if not isinstance(input_payload, dict):
            raise GoldenCorpusError(f"{case_id}: input must be an object.")
        messages = input_payload.get("messages") or []
        sources = input_payload.get("sources") or []
        if (
            not isinstance(messages, list)
            or not all(isinstance(message, dict) for message in messages)
            or not isinstance(sources, list)
            or not all(isinstance(source, dict) for source in sources)
        ):
            raise GoldenCorpusError(f"{case_id}: input messages and sources must be lists of objects.")
        message_ids = [str(message.get("id") or "") for message in messages]
        source_ids = [str(source.get("id") or "") for source in sources]
        if len(message_ids) != len(set(message_ids)) or "" in message_ids:
            raise GoldenCorpusError(f"{case_id}: message ids must be unique and non-empty.")
        if len(source_ids) != len(set(source_ids)) or "" in source_ids:
            raise GoldenCorpusError(f"{case_id}: source ids must be unique and non-empty.")
        for source in sources:
            if (
                not str(source.get("type") or "").strip()
                or not str(source.get("name") or "").strip()
                or not isinstance(source.get("content"), str)
                or not source.get("content").strip()
            ):
                raise GoldenCorpusError(
                    f"{case_id}: every source needs a type, name, and non-empty content."
                )
        for message in messages:
            if message.get("direction") not in VALID_MESSAGE_DIRECTIONS:
                raise GoldenCorpusError(f"{case_id}: invalid message direction.")
            if any(
                str(source_id) not in source_ids
                for source_id in message.get("source_ids") or []
            ):
                raise GoldenCorpusError(f"{case_id}: message cites an unknown source.")

        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise GoldenCorpusError(f"{case_id}: expected result must be an object.")
        message_usage = expected.get("message_usage") or {}
        if not isinstance(message_usage, dict):
            raise GoldenCorpusError(f"{case_id}: expected message usage must be an object.")
        if set(message_usage) != set(message_ids):
            raise GoldenCorpusError(
                f"{case_id}: expected message usage must classify every input message exactly once."
            )
        if any(value not in VALID_MESSAGE_USAGE for value in message_usage.values()):
            raise GoldenCorpusError(f"{case_id}: invalid expected message usage.")

        identity = expected.get("identity") or {}
        if not isinstance(identity, dict):
            raise GoldenCorpusError(f"{case_id}: expected identity must be an object.")
        if identity.get("resolution") not in VALID_IDENTITY_RESOLUTIONS:
            raise GoldenCorpusError(f"{case_id}: invalid identity resolution.")
        for source_id in identity.get("source_ids") or []:
            if source_id not in source_ids:
                raise GoldenCorpusError(f"{case_id}: identity cites an unknown source.")

        items = expected.get("items")
        if (
            not isinstance(items, list)
            or not items
            or not all(isinstance(item, dict) for item in items)
        ):
            raise GoldenCorpusError(f"{case_id}: at least one expected item is required.")
        item_names = []
        source_by_id = _source_map(case)
        for item in items:
            item_name = str(item.get("name") or "").strip()
            normalized_name = _normalized_text(item_name)
            if not normalized_name:
                raise GoldenCorpusError(f"{case_id}: every expected item needs a name.")
            item_names.append(normalized_name)
            operation = item.get("operation")
            parse_status = item.get("parse_status")
            if operation not in VALID_OPERATIONS:
                raise GoldenCorpusError(f"{case_id}: invalid item operation {operation!r}.")
            if parse_status not in VALID_PARSE_STATUSES:
                raise GoldenCorpusError(f"{case_id}: invalid parse status {parse_status!r}.")
            if operation not in {"removed", "uncertain"}:
                if not str(item.get("quantity") or "").strip() or not str(item.get("unit") or "").strip():
                    raise GoldenCorpusError(
                        f"{case_id}: resolved item {item_name!r} needs quantity and unit."
                    )
            if str(item.get("selling_price") or "").strip():
                raise GoldenCorpusError(
                    f"{case_id}: selling prices must remain blank in the intake corpus."
                )
            citations = item.get("citations") or []
            if not isinstance(citations, list) or not citations:
                raise GoldenCorpusError(f"{case_id}: every item needs row-level evidence.")
            for citation in citations:
                if not isinstance(citation, dict):
                    raise GoldenCorpusError(f"{case_id}: citation must be an object.")
                source_id = str(citation.get("source_id") or "")
                location = str(citation.get("location") or "").strip()
                excerpt = str(citation.get("excerpt") or "").strip()
                if source_id not in source_by_id or not location or not excerpt:
                    raise GoldenCorpusError(f"{case_id}: citation is incomplete or unknown.")
                if _normalized_text(excerpt) not in _normalized_text(source_by_id[source_id].get("content")):
                    raise GoldenCorpusError(
                        f"{case_id}: citation excerpt is not present in its synthetic source."
                    )
        if len(item_names) != len(set(item_names)):
            raise GoldenCorpusError(f"{case_id}: expected item names must be unique.")
        if not isinstance(expected.get("ambiguities"), list):
            raise GoldenCorpusError(f"{case_id}: ambiguities must be a list.")
    return corpus


def load_golden_corpus(path=None):
    corpus_path = Path(path or DEFAULT_GOLDEN_CORPUS_PATH)
    try:
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenCorpusError(f"Could not read golden corpus: {exc}") from exc
    return validate_golden_corpus(corpus)


def _citation_is_valid(citation, case, expected_item):
    if not isinstance(citation, dict):
        return False
    source = _source_map(case).get(str(citation.get("source_id") or ""))
    if not source:
        return False
    location = str(citation.get("location") or "").strip()
    excerpt = str(citation.get("excerpt") or "").strip()
    if not location or not excerpt:
        return False
    if _normalized_text(excerpt) not in _normalized_text(source.get("content")):
        return False
    return any(
        str(expected.get("source_id") or "") == str(citation.get("source_id") or "")
        and str(expected.get("location") or "").strip() == location
        and _normalized_text(expected.get("excerpt")) == _normalized_text(excerpt)
        for expected in expected_item.get("citations") or []
    )


def _citation_key(citation):
    return (
        str(citation.get("source_id") or ""),
        str(citation.get("location") or "").strip(),
        _normalized_text(citation.get("excerpt")),
    )


def _citations_match_expected(citations, case, expected_item):
    if not isinstance(citations, list) or not citations:
        return False
    if not all(
        _citation_is_valid(citation, case, expected_item) for citation in citations
    ):
        return False
    return Counter(_citation_key(citation) for citation in citations) == Counter(
        _citation_key(citation) for citation in expected_item.get("citations") or []
    )


def score_golden_case(case, prediction):
    prediction = prediction if isinstance(prediction, dict) else {}
    expected = case["expected"]
    expected_items = {
        _collapsed_text(item.get("name")): item for item in expected["items"]
    }
    predicted_items_list = (
        prediction.get("items") if isinstance(prediction.get("items"), list) else []
    )
    predicted_named_items = [
        item
        for item in predicted_items_list
        if isinstance(item, dict) and _collapsed_text(item.get("name"))
    ]
    predicted_items = {
        _collapsed_text(item.get("name")): item
        for item in predicted_named_items
    }
    matched_names = set(expected_items) & set(predicted_items)
    expected_count = len(expected_items)
    # Count duplicate, unnamed, and malformed output rows as false positives
    # instead of allowing the dictionary used for field scoring to collapse or
    # ignore them.
    predicted_count = len(predicted_items_list)

    def matched_rate(predicate):
        if not expected_count:
            return 1.0
        return sum(
            1
            for name in matched_names
            if predicate(expected_items[name], predicted_items[name])
        ) / expected_count

    expected_message_usage = expected.get("message_usage") or {}
    predicted_message_usage = prediction.get("message_usage") or {}
    message_denominator = max(
        len(expected_message_usage),
        len(predicted_message_usage) if isinstance(predicted_message_usage, dict) else 0,
    )
    message_accuracy = (
        sum(
            1
            for message_id, usage in expected_message_usage.items()
            if isinstance(predicted_message_usage, dict)
            and predicted_message_usage.get(message_id) == usage
        )
        / message_denominator
        if message_denominator
        else None
    )
    identity_fields = ("company", "contact", "email", "resolution")
    predicted_identity = (
        prediction.get("identity")
        if isinstance(prediction.get("identity"), dict)
        else {}
    )
    identity_accuracy = float(
        all(
            _normalized_text(predicted_identity.get(field))
            == _normalized_text((expected.get("identity") or {}).get(field))
            for field in identity_fields
        )
    )
    expected_identity_sources = {
        str(source_id)
        for source_id in (expected.get("identity") or {}).get("source_ids") or []
    }
    identity_evidence_exact = (
        float(
            {
                str(source_id)
                for source_id in predicted_identity.get("source_ids") or []
            }
            == expected_identity_sources
        )
        if expected_identity_sources
        else None
    )
    predicted_ambiguities = (
        prediction.get("ambiguities")
        if isinstance(prediction.get("ambiguities"), list)
        else []
    )
    expected_ambiguities = expected.get("ambiguities") or []
    ambiguity_accuracy = (
        float(
            {_normalized_text(value) for value in predicted_ambiguities}
            == {_normalized_text(value) for value in expected_ambiguities}
        )
        if expected_ambiguities or predicted_ambiguities
        else None
    )
    valid_citation_count = 0
    for predicted_item in predicted_named_items:
        expected_item = expected_items.get(_collapsed_text(predicted_item.get("name")))
        citations = predicted_item.get("citations")
        if expected_item and _citations_match_expected(citations, case, expected_item):
            valid_citation_count += 1
    return {
        "case_id": case["id"],
        "row_precision": len(matched_names) / predicted_count if predicted_count else 0.0,
        "row_recall": len(matched_names) / expected_count if expected_count else 1.0,
        "item_name_exact": matched_rate(
            lambda wanted, got: str(got.get("name") or "").strip()
            == str(wanted.get("name") or "").strip()
        ),
        "quantity_exact": matched_rate(
            lambda wanted, got: _normalized_decimal(got.get("quantity"))
            == _normalized_decimal(wanted.get("quantity"))
        ),
        "unit_exact": matched_rate(
            lambda wanted, got: _normalized_text(got.get("unit"))
            == _normalized_text(wanted.get("unit"))
        ),
        "revision_operation_exact": matched_rate(
            lambda wanted, got: got.get("operation") == wanted.get("operation")
        ),
        "citation_valid": (
            valid_citation_count / predicted_count if predicted_count else 0.0
        ),
        "parse_status_exact": matched_rate(
            lambda wanted, got: got.get("parse_status") == wanted.get("parse_status")
        ),
        "customer_price_evidence_exact": matched_rate(
            lambda wanted, got: _normalized_decimal(got.get("customer_price_evidence"))
            == _normalized_decimal(wanted.get("customer_price_evidence"))
        ),
        "blank_selling_price": float(
            bool(predicted_items_list)
            and all(
                isinstance(item, dict)
                and not str(item.get("selling_price") or "").strip()
                for item in predicted_items_list
            )
        ),
        "message_usage_accuracy": message_accuracy,
        "identity_accuracy": identity_accuracy,
        "identity_evidence_exact": identity_evidence_exact,
        "ambiguity_accuracy": ambiguity_accuracy,
    }


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 1)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(value, 1)


def _quality_summary(case_scores):
    if not case_scores:
        return {}
    quality_keys = [key for key in case_scores[0] if key != "case_id"]
    summary = {}
    for key in quality_keys:
        applicable = [score[key] for score in case_scores if score[key] is not None]
        summary[key] = (
            round(sum(applicable) / len(applicable), 4) if applicable else None
        )
    return summary


def _performance_summary(observations):
    latencies = [
        observation.get("latency_ms")
        for observation in observations
        if isinstance(observation.get("latency_ms"), (int, float))
        and math.isfinite(observation.get("latency_ms"))
        and observation.get("latency_ms") >= 0
    ]
    token_fields = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
    cost_basis = {
        field: sum(
            max(0, int(observation.get(field) or 0))
            for observation in observations
            if isinstance(observation.get(field), (int, float))
            and math.isfinite(observation.get(field))
        )
        for field in token_fields
    }
    token_observed_cases = sum(
        1
        for observation in observations
        if any(
            isinstance(observation.get(field), (int, float))
            and math.isfinite(observation.get(field))
            for field in token_fields
        )
    )
    return {
        "latency_observed_cases": len(latencies),
        "token_observed_cases": token_observed_cases,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p90": _percentile(latencies, 0.90),
            "p95": _percentile(latencies, 0.95),
        },
        "cost_basis": cost_basis,
    }


def _is_submitted_prediction(prediction):
    return (
        isinstance(prediction, dict)
        and isinstance(prediction.get("items"), list)
        and isinstance(prediction.get("message_usage"), dict)
        and isinstance(prediction.get("identity"), dict)
        and isinstance(prediction.get("ambiguities"), list)
    )


def evaluate_golden_predictions(corpus, predictions):
    validate_golden_corpus(corpus)
    predictions = predictions if isinstance(predictions, dict) else {}
    case_scores = [
        score_golden_case(case, predictions.get(case["id"]) or {})
        for case in corpus["cases"]
    ]
    case_ids = {case["id"] for case in corpus["cases"]}
    valid_prediction_ids = {
        case_id
        for case_id, prediction in predictions.items()
        if case_id in case_ids
        and _is_submitted_prediction(prediction)
    }
    known_prediction_ids = case_ids & set(predictions)
    invalid_prediction_ids = sorted(
        case_id
        for case_id, prediction in predictions.items()
        if case_id in case_ids
        and not _is_submitted_prediction(prediction)
    )
    observations_by_case = {}
    for case_id, prediction in predictions.items():
        if case_id not in case_ids or not isinstance(prediction, dict):
            continue
        observation = prediction.get("observation")
        observations_by_case[case_id] = (
            observation if isinstance(observation, dict) else {}
        )
    observations = list(observations_by_case.values())
    score_by_id = {score["case_id"]: score for score in case_scores}
    by_route = {}
    for route in sorted(VALID_ROUTES):
        route_cases = [case for case in corpus["cases"] if case["route"] == route]
        route_scores = [score_by_id[case["id"]] for case in route_cases]
        route_observations = [
            observations_by_case[case["id"]]
            for case in route_cases
            if case["id"] in observations_by_case
        ]
        by_route[route] = {
            "case_count": len(route_cases),
            "prediction_count": sum(
                1 for case in route_cases if case["id"] in valid_prediction_ids
            ),
            "quality": _quality_summary(route_scores),
            "performance": _performance_summary(route_observations),
        }
    return {
        "schema_version": GOLDEN_CORPUS_SCHEMA_VERSION,
        "corpus_id": corpus.get("corpus_id") or "",
        "case_count": len(corpus["cases"]),
        "prediction_count": len(valid_prediction_ids),
        "missing_case_ids": sorted(case_ids - known_prediction_ids),
        "invalid_prediction_case_ids": invalid_prediction_ids,
        "category_counts": dict(sorted(Counter(case["category"] for case in corpus["cases"]).items())),
        "quality": _quality_summary(case_scores),
        "performance": _performance_summary(observations),
        "by_route": by_route,
        "cases": case_scores,
    }
