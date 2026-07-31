# Quotation-intake golden corpus

`quotation_intake_v1.json` is a versioned, fully synthetic seed benchmark for
the manual-upload and Gmail thread-to-quotation routes. It contains no copied
customer messages, names, domains, documents, identifiers, or prices. All
email domains use the reserved `.test` suffix.

The corpus measures the behavior employees depend on:

- exact customer-authored item snapshot names;
- row precision and recall, including duplicate and signature false positives;
- quantities, units, revision operations, uncertainty, and message selection;
- company/contact resolution;
- customer-stated price or budget evidence without treating it as our price;
- row-level source evidence; and
- the invariant that extracted selling prices remain blank.

It also accepts optional, content-free observations for route-stratified
latency percentiles and provider token totals. Latency and token sample counts
are reported separately. Token totals are a cost basis, not currency:
historical cost must be calculated against the provider price schedule that
applied when the observation was recorded.

Validate the corpus without calling Gmail, an AI provider, or the database:

```powershell
cd backend
python manage.py evaluate_quotation_intake
```

Score a predictions file offline:

```powershell
python manage.py evaluate_quotation_intake --predictions path/to/predictions.json
```

The predictions file is a JSON object keyed by case ID, or an object with that
mapping under `predictions`. Each prediction mirrors the corresponding
`expected` object and may include an `observation` object containing
`latency_ms`, `input_tokens`, `cached_input_tokens`, `output_tokens`, and
`total_tokens`.

This v1 corpus is deliberately a text-auditable semantic seed. Synthetic
scanned-document entries are transcripts, not binary image/PDF fidelity tests,
and the case mix is not evidence of production-level accuracy. Future corpus
versions should add independently adjudicated synthetic or irreversibly
de-identified cases through privacy review, retain older versions for
comparison, and set release thresholds only after a representative baseline
has been measured.
