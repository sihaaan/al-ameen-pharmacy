import re
from difflib import SequenceMatcher

from .models import Company, normalize_label


LEGAL_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "llc",
    "llp",
    "ltd",
    "limited",
    "pvt",
    "private",
    "plc",
    "fz",
    "fzco",
    "fze",
}


def _name_tokens(value):
    text = normalize_label(value)
    text = re.sub(r"\bl\.?\s*l\.?\s*c\.?\b", " llc ", text)
    text = re.sub(r"\b\d{6,8}[a-z]?\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [token for token in text.split() if token]
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return tokens


def company_match_key(value):
    return " ".join(_name_tokens(value))


def score_company_name(source_name, candidate_name):
    source_key = company_match_key(source_name)
    candidate_key = company_match_key(candidate_name)
    if not source_key or not candidate_key:
        return 0, "No usable company name."
    if normalize_label(source_name) == normalize_label(candidate_name):
        return 100, "Exact company name match."
    if source_key == candidate_key:
        return 96, "Same company name after removing legal suffixes or date-like noise."

    source_tokens = set(source_key.split())
    candidate_tokens = set(candidate_key.split())
    if source_tokens and candidate_tokens:
        overlap = len(source_tokens & candidate_tokens) / max(len(source_tokens), len(candidate_tokens))
        if overlap >= 0.8:
            return 88, "Most company name words match."
        if source_tokens.issubset(candidate_tokens) or candidate_tokens.issubset(source_tokens):
            return 84, "One company name is a shorter version of the other."

    ratio = SequenceMatcher(None, source_key, candidate_key).ratio()
    if ratio >= 0.86:
        return round(ratio * 100), "Company names are very similar."
    if ratio >= 0.74:
        return round(ratio * 100), "Company names may refer to the same customer."
    return round(ratio * 100), "Low similarity."


def find_similar_companies(name, queryset=None, limit=5, threshold=74):
    queryset = queryset or Company.objects.all()
    suggestions = []
    for company in queryset:
        score, reason = score_company_name(name, company.name)
        if score >= threshold:
            suggestions.append(
                {
                    "id": company.id,
                    "name": company.name,
                    "email": company.email,
                    "phone": company.phone,
                    "trn": company.trn,
                    "score": score,
                    "reason": reason,
                    "is_active": company.is_active,
                }
            )
    return sorted(suggestions, key=lambda item: (-item["score"], item["name"].lower()))[:limit]
