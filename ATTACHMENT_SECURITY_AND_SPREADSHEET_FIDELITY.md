# Attachment Security and Spreadsheet Fidelity

| Field | Value |
|---|---|
| Document version | 1.0.0 |
| Status | Implemented Task 2.4 repository contract; not a malware or parser-isolation certification |
| Owner | Al Ameen quotation-system maintainers and production operators |
| Last verified | 2026-08-01 |
| Reviewed code | `d88b767` baseline through Task 2.2, Task 2.3 checkpoint `fc4c77c`, plus the Task 2.4 worktree checkpoint on `codex/technical-hardening` |
| Production snapshot | Railway deployment `c234c4bc-ba7e-4ed0-ab88-b5a1dcc2a6b8`, commit `70d3da7162b63864e479e9a1998aa138046c2433`; Task 2.4 was not deployed or provider-verified by this work |

Task 2.4 adds bounded attachment inspection before supported document parsers
or image persistence. It separates definite safety/format violations from
accuracy risks that require employee review. One additive database migration
adds structured parser metadata to outcome-PO imports; existing rows receive
an empty metadata object, while no existing business value is changed or
deleted. The task does **not** change the Gmail or
manual workflows, public endpoint paths or request shapes, OAuth scopes, AI
provider/model/prompt/schema, or production infrastructure.

The controls preserve employee review, row-level evidence and uncertainty,
suggestion-only company/product matching, and blank selling prices after
inquiry extraction. They also preserve preview-before-send, verified Gmail
reply behavior, one successful send per quotation revision, ambiguous-send
lockout, and no-send reconciliation.

## 1. Decision model

| Result | Meaning | Processing behavior |
|---|---|---|
| **Hard failure** | The bytes are malformed, structurally inconsistent, encrypted where unsupported, or exceed a definite resource boundary. | The affected route rejects or records the source as failed. It does not fabricate rows, store a newly uploaded manual/historical source, or send the failed Gmail attachment to AI. |
| **Warning-only fidelity signal** | The container passed hard checks, but a feature could make extracted values incomplete, stale, duplicated, or visually misaligned. | Parsing may continue. The warning and bounded inspection metadata remain available for employee review; the application does not silently "fix" the source. |
| **Unsupported** | The route intentionally does not accept that otherwise recognizable type. | The route explains the limitation. This is distinct from claiming that the file is malicious or malformed. |

A declared MIME mismatch is warning-only when the bytes validate as the file
type named by the extension. Empty/generic MIME values such as
`application/octet-stream` do not fail a valid file. The validated bytes, not
the browser-supplied MIME alone, determine the format.

## 2. Route behavior

| Route | Hard failures | Warning-only signals and evidence | Important route-specific behavior |
|---|---|---|---|
| Manual inquiry, quotation LPO/outcome, and proforma LPO uploads | Supported PDF/Excel files are inspected before parsing and before a new private source object is stored. Invalid signatures, malformed/encrypted containers, unsafe OOXML packages, and hard resource-limit violations fail the request. | `warnings` plus `meta.attachment_safety`, `meta.spreadsheet_fidelity`, or `meta.pdf_fidelity` are returned with the preview. Row/sheet provenance remains attached. LPO, proforma, and outcome-PO records retain the structured inspection subset in `parsed_meta`. | Manual inquiry images use the existing bounded vision-upload path. PO/outcome/LPO/proforma file pickers and backend routes accept PDF/Excel only; image PO/LPO parsing remains unavailable. Optional AI cleanup cannot erase deterministic source context or staff-entered pricing/VAT review. |
| Gmail native inquiry analysis | Supported inbound PDF, XLS, and XLSX attachments receive the shared inspection before provider submission. | Warning-only files may still be submitted to the existing native AI flow; warnings and bounded safety/fidelity fields are copied into the attachment manifest, evidence, and import warnings. | If **any selected attachment** has a hard validation failure, the whole provider call for that selection is blocked, prepared file inputs are cleared, other submitted files are marked skipped, and zero request rows are produced. The failed source remains a bounded `attachment_inspection_v1` evidence record with its digest and reason, but is not item evidence. Gmail images remain excluded and XLSB remains unsupported by the native file-input path. |
| Price-reference upload | Direct XLSX uses shared inspection; PDF, XLS, and XLSB use the normal preview inspection path. Hard failures stop reference application. | Inspection metadata, sheet metadata, and explicit row/column/sheet truncation warnings are returned. | Price reference is an explicit employee action and may apply reference prices to the current inquiry preview. It is the deliberate exception to the blank-price rule for newly extracted inquiry rows; customer budgets/prices from inquiry evidence are still not treated as Al Ameen selling prices. |
| Historical quotation PDF | PDF inspection runs before table extraction and before the private source is stored. Malformed, incomplete, non-PDF, or encrypted input fails. | MIME, active-content, embedded-file, and form-field warnings remain in the preview with `attachment_safety` and `pdf_fidelity`. | Historical price extraction keeps its existing duplicate check, review, and commit flow. Passing inspection does not prove that every table was extracted accurately. |
| Product and quotation-branding images | PNG/JPEG/WebP extension, declared MIME when supplied, decoded format, complete decode, configured byte limit, dimensions, pixel count, and single-frame requirement must agree. Validation runs before persistence. | There is no warning-only acceptance for an image that fails these checks. | Product catalog images, quotation-line product images, company brand logos, quotation logos, signatures, and stamps use the shared validator. The validator rewinds the upload after inspection; invalid bytes are not persisted by these application paths. |

Mailbox-PO and contract-intelligence parsing paths that reuse the normal file
preview inherit its document checks. Their business matching, approval, and
evidence rules remain unchanged.

## 3. Spreadsheet hard failures

OOXML XLSX/XLSB packages fail before a workbook parser runs when any of these
conditions is detected:

- malformed or incomplete ZIP/package structure;
- too many archive entries, one member exceeding the expansion limit, or the
  package exceeding the total uncompressed limit;
- absolute/traversal/backslash/NUL member names, symbolic-link members, or
  duplicate file-part names (repeated explicit directory records are harmless);
- encrypted ZIP members;
- missing required OOXML package parts or an extension/package-kind mismatch;
- macro parts concealed inside an `.xlsx` file; or
- malformed or unsafe inspected XML metadata.

Legacy `.xls` input must have the OLE compound-file signature. That signature
is only a format check; it does not prove that the workbook is unencrypted,
benign, or accurately inspectable.

## 4. Spreadsheet warning-only fidelity signals

The following conditions are deliberately warning-only because rejecting them
would create false positives for legitimate customer workbooks:

- suspicious compression ratios that remain below all hard expansion limits;
- formulas, formula/error cells, missing cached formula results, and cached
  values that may be stale because formulas are never recalculated;
- hidden sheets, rows, or columns; merged cells; workbook/sheet protection;
- explicit date cells that may normalize code-like values;
- external links/connections, which are never refreshed;
- embedded or active objects, which are never used for row extraction; and
- limited formula/hidden/merged/external/macro visibility in legacy `.xls`
  and binary `.xlsb` content.

Detailed XLSX worksheet-XML inspection is bounded to 4 MiB per worksheet; a
larger worksheet that remains within hard archive limits receives a limited-
inspection warning. Date counting covers cells explicitly stored with OOXML
date type, not every date-like value or arbitrary number format/style. These
signals must not be interpreted as complete formula, style, or date analysis.

Spreadsheet parsing selects visible sheets before applying the sheet limit.
Hidden sheets are excluded and do not consume the visible-sheet allowance;
hidden rows or columns inside a visible sheet can still enter extraction and
therefore require source review. Row, column, and sheet truncation warnings
appear only when the corresponding bound is exceeded.
The XLSX fallback reader is used only after the primary reader fails without
partially yielding rows. Rows repeated across visible sheets remain included
and are flagged as possible duplicates; the parser never silently removes them.

Ambiguous grouped/signed numeric source text and missing quantity or unit remain
review signals. Existing compatible numeric values are preserved for review;
the parser does not infer a replacement merely to clear the warning.

## 5. PDF and image boundaries

PDF inspection requires a valid PDF signature and a complete, readable,
unencrypted container. A bounded raw cross-reference preflight runs before
`PdfReader`, followed by object, stream, page-count, page-geometry/`UserUnit`,
embedded-image, raster-output, extracted-text/word, and table-output limits.
The raw preflight follows bounded `Prev` and `XRefStm` revision chains, validates
classic and stream xrefs, exact object generations/offsets, compressed-object
stream indexes, supported filter chains, decoded sizes, and exact declared
stream boundaries before `PdfReader`. The reader is then restricted to that
validated graph so its repair behavior cannot introduce an unvalidated object.
This remains a bounded recognizer rather than an independent PDF parser. A
malformed unused object can therefore still make the document fail closed
during the later complete object walk.

Active-content markers (including JavaScript, launch, rich-media, and open
actions), embedded-file markers, and AcroForm/XFA fields are warnings because
the application does not execute or open them. Form values or visual layout
may still be absent from extracted text. Locally supported unfiltered,
ASCII85/ASCIIHex plus Flate, and Flate stream chains receive decoded-size
bounds. Standard compressed image streams are bounded using declared geometry.
If another non-image stream filter cannot be decoded within this bounded
preflight, the file is marked unsafe for local page traversal: manual and
historical local extraction/rendering/OCR skip it and report the warning. Every
local AI-vision render repeats this inspection immediately before opening the
native renderer and requires `local_traversal_safe`.
Gmail native analysis may still submit the original byte-identical document to
the configured provider after all other hard checks pass, and exposes this
limitation for employee review.

Each embedded image must remain within the decoded image ceiling. A document
whose aggregate estimated image memory exceeds the decoded-stream aggregate
limit receives a warning rather than a hard rejection so ordinary multi-page
scans remain usable; each page also receives a hard aggregate image-pixel and
estimated decoded-byte check before local rendering. Reachable inline-image
operators are detected in page content, recursive Form XObjects, tiling
Patterns, Type3 glyphs, soft-mask Forms, and annotation appearances. Their
individual dictionaries are not interpreted locally, so their presence makes
local extraction/rendering unavailable and remains visible for employee
review/provider-safe handling.

The shared catalog/branding image validator defaults to a 2 MiB byte limit,
allows one PNG/JPEG/WebP frame, and applies fixed ceilings of 12,000 pixels on
either edge and 25 million total pixels. It performs both Pillow verification
and a complete pixel load. This is validation, not content sanitization or
image re-encoding.

## 6. Evidence and blank-price guarantees

- Hard failures create no extracted item rows and never invent evidence.
- Manual previews preserve warning metadata alongside existing row-level
  source sheet/row, page, line, hash, and private-source references.
- Gmail hard failures preserve a bounded rejection record (filename, type,
  source key/message ownership, digest, status, reason, and inspection fields).
  That record proves why the file was rejected; it is not evidence that an
  item, quantity, unit, or price was requested.
- Warning-only files retain their inspection warnings in the same manifest and
  evidence chain used by the reviewed import.
- Gmail and manual inquiry extraction leave Al Ameen selling prices blank.
  Manual text/file/AI responses move detected unit price, amount, VAT, and
  total values into explicit `customer_*` evidence fields and clear the
  quotation-facing money fields before the employee review UI receives them.
  The same bounded evidence labels are appended to the existing inquiry-line
  notes so they remain available after the preview is saved and reopened.
  Customer budgets or document prices therefore remain source evidence only.
  Prices typed by an employee and the separate reviewed price-reference action
  remain deliberate exceptions. AI cleanup is blocked after employee pricing
  or VAT review has begun so it cannot discard those reviewed values. Parser
  warnings are rebound after AI cleanup and are not copied into reusable cache
  payloads. Deterministic customer-price evidence is restored only to a unique
  matching AI row; if it cannot be mapped uniquely, the AI replacement is
  rejected and the deterministic rows remain for review.
- Company/contact/product matches remain suggestions; no Product, alias,
  company assignment, inquiry, or quotation is created without the existing
  employee review/confirmation gates.
- LPO/outcome and proforma evidence continue to represent customer acceptance
  or order evidence under their existing review rules; Task 2.4 does not turn
  those values into unreviewed inquiry selling prices.

## 7. Configuration inventory

The canonical examples for document-parser settings are in
[backend/.env.example](backend/.env.example). Values are bytes unless noted.

| Variable | Default | Applies to |
|---|---:|---|
| `QUOTATION_IMPORT_MAX_UPLOAD_BYTES` | 5 MiB | Normal manual/import document reads and the private-evidence default ceiling |
| `QUOTATION_IMPORT_MAX_EXCEL_ROWS` | 500 per visible sheet | Manual preview Excel parsing |
| `QUOTATION_IMPORT_MAX_EXCEL_SHEETS` | 10 visible sheets | Manual and price-reference workbook parsing |
| `QUOTATION_IMPORT_MAX_EXCEL_COLUMNS` | 100 per sheet | Manual and price-reference workbook parsing |
| `QUOTATION_IMPORT_MAX_PDF_PAGES` | 10 | Normal PDF preview parsing |
| `QUOTATION_IMPORT_MAX_PDF_OBJECTS` | 20,000; clamped to 50,000 | PDF xref/object inventory |
| `QUOTATION_IMPORT_MAX_PDF_STREAMS` | 5,000; clamped to 10,000 | PDF stream inventory |
| `QUOTATION_IMPORT_MAX_PDF_DECODED_STREAM_BYTES` | 32 MiB; clamped to 64 MiB | One locally decoded PDF stream or estimated decoded image |
| `QUOTATION_IMPORT_MAX_PDF_TOTAL_DECODED_STREAM_BYTES` | 64 MiB; clamped to 128 MiB | Aggregate locally decoded non-image streams; aggregate images warn above this value |
| `QUOTATION_IMPORT_MAX_PDF_PAGE_DIMENSION_POINTS` | 10,000; clamped to 20,000 | Effective PDF page width/height after `UserUnit` |
| `QUOTATION_IMPORT_MAX_PDF_PAGE_AREA_POINTS` | 16,000,000; clamped to 32,000,000 | Effective PDF page area |
| `QUOTATION_IMPORT_MAX_PDF_RENDER_PIXELS` | 25,000,000; clamped to 50,000,000 | One locally rasterized PDF page |
| `QUOTATION_IMPORT_MAX_PDF_IMAGE_PIXELS` | 25,000,000; clamped to 50,000,000 | One embedded PDF image |
| `QUOTATION_IMPORT_MAX_PDF_TEXT_CHARS_PER_PAGE` | 250,000; clamped to 1,000,000 | Extracted/OCR characters per page |
| `QUOTATION_IMPORT_MAX_PDF_TOTAL_TEXT_CHARS` | 1,000,000; clamped to 4,000,000 | Aggregate extracted/OCR characters |
| `QUOTATION_IMPORT_MAX_PDF_WORDS_PER_PAGE` | 50,000; clamped to 200,000 | Extracted words per page |
| `QUOTATION_IMPORT_MAX_PDF_TOTAL_WORDS` | 250,000; clamped to 500,000 | Aggregate extracted words |
| `QUOTATION_IMPORT_MAX_PDF_TABLE_ROWS` | 20,000; clamped to 50,000 | Materialized PDF table rows |
| `QUOTATION_IMPORT_MAX_PDF_TABLE_CELLS` | 100,000; clamped to 250,000 | Materialized PDF table cells |
| `QUOTATION_IMPORT_MAX_ARCHIVE_ENTRIES` | 2,048; clamped to 10,000 | OOXML inspection |
| `QUOTATION_IMPORT_MAX_ARCHIVE_UNCOMPRESSED_BYTES` | 128 MiB; clamped to 256 MiB | Total OOXML expansion |
| `QUOTATION_IMPORT_MAX_ARCHIVE_MEMBER_BYTES` | 32 MiB; clamped to 64 MiB | Individual OOXML member expansion |
| `QUOTATION_PRICE_REFERENCE_MAX_EXCEL_ROWS` | 5,000 per visible sheet | Direct XLSX price-reference parsing |
| `PRODUCT_IMAGE_MAX_UPLOAD_BYTES` | 2 MiB | Product catalog and quotation-line product images |
| `QUOTATION_BRANDING_IMAGE_MAX_UPLOAD_BYTES` | 2 MiB | Quotation logos, signatures, and stamps |

Gmail native AI uses separate existing limits:
`QUOTATION_AI_NATIVE_MAX_FILES` (12),
`QUOTATION_AI_NATIVE_MAX_TOTAL_BYTES` (20 MiB),
`QUOTATION_AI_NATIVE_MAX_PDF_PAGES` (25), and
`QUOTATION_AI_NATIVE_MAX_SPREADSHEET_ROWS_PER_SHEET` (1,000). Increasing a
Gmail limit does not bypass the shared container inspection. Native workbook
preflight also uses the lower of its hard ceilings and the configured manual
visible-sheet/column limits: 10 visible sheets, 100 columns per sheet, 5,000
aggregate visible rows, and 500,000 aggregate visible cells. These are hard
provider-submission boundaries, not silent truncation. Fixed image
dimension/pixel/frame ceilings are not environment-configurable.

Treat higher limits as a memory/CPU availability decision. Archive settings
cannot exceed their code-level clamps. Change one bound at a time, use
representative non-sensitive fixtures, and verify warning visibility before
promotion.

## 8. Operations and rollback

For a hard failure, keep the original customer source canonical, record the
sanitized validation reason, and ask for a valid/smaller/exported copy. Do not
rename an invalid file, bypass inspection, remove a selected Gmail attachment
from evidence without employee intent, or copy customer contents into logs.
For a warning-only result, compare the extracted rows with the visible source,
especially formulas, hidden/merged cells, truncated sheets, and duplicates.

Migration `quotations.0036_quotationoutcomepoimport_parsed_meta` adds one
non-destructive JSON field to `QuotationOutcomePOImport` and initializes
existing rows with an empty object. Apply it before promoting Task 2.4 code;
there is no customer-content backfill. A pre-deployment rollback may revert
the source checkpoint and the unused migration. Once outcome imports contain
structured inspection metadata, do not reverse `0036` as an ordinary rollback:
that would delete the retained evidence. Keep the column and prefer a forward
fix or a compatibility rollback.

After deployment, first restore an accidentally tightened environment limit
to its previous reviewed value or prefer a forward fix. A full code rollback
must revert inspection call sites and their tests together; it weakens
attachment defenses and therefore requires an explicit security decision.
Re-run a fresh preview after rollback rather than trusting results produced
under a different inspection contract.

## 9. Residual risks and non-claims

- There is **no malware scanner or antivirus (AV)** in this pipeline.
- There is **no parser sandbox** or separate worker/process isolation; parsers
  still execute in the web process. Bounds reduce exposure but cannot eliminate
  vulnerabilities in Pillow, PDF, ZIP, XML, Excel, or OCR libraries.
- Legacy `.xls` and binary `.xlsb` receive limited inspection. In particular,
  the `.xls` OLE signature does not prove absence of encryption, macros,
  embedded content, or malware, and `.xlsb` formula/hidden/merged/macro
  semantics cannot be fully enumerated by this inspection.
- XLSX fidelity inspection is also bounded: large worksheet XML, style-based
  date formatting, and every possible formula/rendering semantic are not fully
  inspected even when the package passes hard validation.
- PDF raw-xref and marker checks are bounded recognizers, not an independent
  parser or a complete active-content/malware detector. Unsupported non-image
  stream filters are not decompressed locally; local traversal is skipped, but
  a warning-only Gmail source may still be submitted to the configured AI
  provider after other hard checks pass.
- Reachable PDF inline images are conservatively detected rather than decoded;
  affected files intentionally skip local extraction/rendering. DCT, JPX,
  JBIG2, and CCITT image codecs still execute in the web process after declared
  geometry, per-image, per-page aggregate, and output checks. Codec-internal
  complexity is not fully contained without process isolation, and a
  25-million-pixel RGB raster can consume roughly 75 MiB plus library overhead.
- Gmail's decoded JSON/MIME tree is materialized before the bounded 100-entry
  per-message attachment manifest is applied. Selected inbound overflow fails
  closed before attachment fetch or AI submission, but bounding the upstream
  response reader/tree itself remains defense-in-depth work.
- Custom upload serializers enforce the shared image decoder. Direct trusted
  Django-administration or model-level file assignment is not a substitute for
  those serializers and remains a defense-in-depth validation boundary.
- Formula cached values, external-link values, merged layouts, OCR, and
  semantic row classification can still be wrong; employee source comparison
  remains mandatory.
- A valid container may still contain misleading business data or prompt
  injection. AI and deterministic parsers must continue treating content as
  untrusted data.
- MIME mismatch remains warning-only after byte validation, so operators must
  review suspiciously labelled files rather than interpreting acceptance as
  provenance proof.
- Hard limits can reject a legitimate large document. The supported recovery
  is a smaller/exported source or a separately reviewed configuration change,
  not a validation bypass.
- Task 2.4 made no production deployment, no provider/storage selection, no
  OAuth scope change, no AI model/prompt/schema change, and no malware/sandbox
  integration.

## 10. Tests and source index

Focused tests cover OOXML package/resource attacks, warning-only fidelity
features, visible-sheet/row/column boundaries, duplicate preservation, fallback
behavior, PDF warnings/failures, Gmail provider blocking and evidence, price
reference and historical PDF propagation, ambiguous numeric rows, and invalid
product/branding image uploads.

Primary implementation and test locations:

- `backend/quotations/attachment_inspection.py`
- `backend/quotations/import_parsers.py`
- `backend/quotations/gmail_inquiry_import.py`
- `backend/quotations/price_reference.py`
- `backend/quotations/historical_import_parsers.py`
- `backend/quotations/import_rules.py`
- `backend/quotations/quote_po_intelligence.py`
- `backend/quotations/models.py`
- `backend/quotations/serializers.py`
- `backend/quotations/migrations/0036_quotationoutcomepoimport_parsed_meta.py`
- `backend/api/upload_validation.py`
- `backend/api/serializers.py`
- `backend/quotations/test_attachment_fidelity.py`
- `backend/quotations/test_attachment_meta_retention.py`
- `backend/quotations/test_gmail_attachment_fidelity.py`
- `backend/quotations/test_pdf_resource_bounds.py`
- `backend/quotations/test_reference_attachment_fidelity.py`
- `backend/quotations/test_import_rule_fidelity.py`
- `backend/quotations/test_documentation_contract.py`

See also [SECURITY.md](SECURITY.md), [OPERATIONS.md](OPERATIONS.md),
[DEPLOYMENT.md](DEPLOYMENT.md), and
[GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md](GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md).
