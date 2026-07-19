# Quotation Module

## Business Problem

Al Ameen Pharmacy receives company inquiries and LPOs for pharmacy items. Staff currently use separate, unstructured Excel files per company to look up previous prices, match requested item names, copy prices into quotations, and manually update history after sending.

The quotation module brings that workflow into the existing pharmacy website admin dashboard so staff can manage companies, inquiries, product-backed quotation items, company-specific aliases, company-specific pricing history, generated quotations, PDFs, and finalization without Git, Docker, localhost, or a second application.

## Phase 1 MVP Scope

Phase 1 is limited to the existing Django/DRF backend and existing React admin dashboard at `/admin`.

Included:
- Separate Django app named `quotations`
- Staff-only DRF APIs
- Company and contact management
- Product-backed quotation item catalog. `Product` is now the master item identity; draft/internal products stay hidden from the public website but remain usable in quotations.
- Manual inquiry creation and inquiry lines
- Safe inquiry import preview for pasted text, `.xlsx`, and digitally generated `.pdf`
- Optional AI-assisted import parsing cleanup for messy text/PDF extraction, controlled from `Quotations -> Settings` and always gated by staff review
- Manual review/edit before saving imported inquiry lines
- Quotation creation and line editing
- Company-specific previous price history
- Finalization workflow
- Audit logs for important quotation actions
- Protected PDF generation streamed from the backend
- React admin dashboard tab: `Admin Dashboard -> Quotations`
- Historical finalized quotation PDF import for reviewed company price-history backfill
- Batch historical quotation PDF upload and review-only AI learning suggestions for company/Product/alias/new-draft-product decisions

Not included in Phase 1:
- Fully automatic AI product creation, alias creation, product matching, or pricing decisions without staff approval
- OpenAI embeddings
- pgvector migrations
- Gmail API import
- Gmail draft creation
- Background workers
- Full free-form document parsing outside deterministic Excel/PDF flows
- OCR/scanned PDF extraction
- Automatic quotation creation from uploaded files
- Public storage or public URLs for uploaded inquiry/source files
- Complex reporting
- Public quotation pages
- Public Cloudinary storage for sensitive quotation PDFs

## Current Implementation Status

Status: Phase 1 implemented and verified.

Completed:
- Planning and repo-specific architecture review
- Documentation scaffold for quotation module
- Backend `quotations` app
- Models and initial migration
- Staff-only permissions
- DRF serializers, viewsets, and endpoints
- Service-layer workflow logic
- Audit logging
- Protected PDF generation
- Backend tests for required security and workflow rules
- React `Quotations` tab inside the existing `/admin` dashboard
- Frontend quotation API helper
- React quotation components for dashboard, companies, quote items, inquiries, quotations, editor, price history, and audit logs
- Quotation-specific frontend error panels with endpoint, HTTP status, and backend detail
- Local frontend development API target corrected to `http://localhost:8000/api`
- Final Phase 1 browser smoke pass for product/admin behavior, quotation workflow, optional product dropdown failure handling, and quotation API security
- Phase 1 workflow hardening:
  - Manual Inquiry requested-line layout no longer overflows
  - Create Quote is protected against double-click duplicate creation in frontend and backend
  - Quotation editor shows status progress, saved/unsaved line states, and `Save All Lines`
  - Mutating workflow actions and PDF download now show loading/disabled states
  - PDF output has configurable Al Ameen Pharmacy branding, metadata, totals, terms, and signature/stamp area
- Safe inquiry import:
  - pasted text preview
  - Excel `.xlsx`, `.xlsb`, and `.xls` preview through deterministic header/row parsing
  - digitally generated PDF preview through PyMuPDF, `pypdf`, and `pdfplumber`
  - optional AI Clean Parse candidate rows for messy imported text/PDF extraction
  - reviewed imported inquiry save endpoint
  - no automatic quotation creation from uploaded content
  - no persistent uploaded binary storage
- Staff-editable quotation settings:
  - `Quotations -> Settings` page inside the React admin dashboard
  - singleton `QuotationSettings` model
  - staff-only settings API
  - logo, signature, and stamp image upload with extension, MIME type, size, and file-signature validation
  - PDF branding pulled from saved settings with sensible defaults
- Historical finalized quotation backfill:
  - `Quotations -> Historical Imports` tab inside the React admin dashboard
  - staff-only upload of old finalized Al Ameen quotation PDFs
  - deterministic extraction of quotation number, date, totals, and price rows from text-based PDF tables
  - private source-file refs stored outside public media/Cloudinary URLs
  - staff review/linking to `Product` before committing any `CompanyPriceHistory`
  - committed rows create a hidden finalized historical quotation record so price history keeps a traceable source
- Inline company creation:
  - company selectors in inquiry import, manual inquiry, direct quotation creation, historical imports, and price-history filtering are searchable
  - missing companies can be created in place and are selected automatically
  - historical imports prefill the inline create form from the suggested company name parsed from the source filename
- Bulk import review:
  - historical import price rows support checkbox selection, select visible, select unmatched, select needs-review, select ready, and clear selection
  - bulk toolbar supports duplicate-safe Product create/link, mark ready, mark needs-review, and skip
  - compact review table includes status row highlighting, search, status/unmatched/error filters, hidden raw source rows, and a row action menu
  - sticky commit bar shows total/ready/needs-review/skipped/duplicate/selected counts and commits only ready rows
- imported inquiry previews support checkbox selection and bulk delete for removing parser noise before saving
- Product-as-master item refactor:
  - `Product` is now the active quotation item identity
  - old `QuoteItem` table/fields remain temporarily for migration compatibility and rollback safety
  - internal quotation-only items are draft Products and are not shown publicly
  - `ProductAlias` supports global aliases and company-specific aliases
  - deterministic matching checks company aliases first, then global aliases, then conservative product-name/SKU/barcode matches
  - finalized quotations and historical imports write price history against Products
- used Products are archived instead of deleted; used Companies are deactivated instead of deleted
- AI-assisted parsing cleanup:
  - `Quotations -> Settings` controls `Enable AI Parsing`, `Enable Auto AI Cleanup`, and `Enable Vision AI for PDFs and inquiry images`
  - provider keys and hard safety limits stay in environment variables
  - deterministic parsing always runs first
  - inquiry staff can click `AI Clean & Apply`, which applies the reviewed extraction in one operation and offers Undo
  - historical import `AI Clean Rows` remains a staged candidate workflow that requires explicit application
  - AI parsing does not match Products, create Products, create aliases, create price history, finalize quotations, or bypass review
  - missing Product matches never trigger AI cleanup and do not lower parse confidence
- Batch AI-assisted historical quotation learning:
  - staff can upload up to 25 old finalized PDF files into a `HistoricalImportBatch`
  - each file becomes a normal staged `HistoricalPriceImport` or returns duplicate metadata without creating another staged import
  - staff can run AI suggestions on selected imports in the batch
  - AI suggestions are stored as pending review data only and can propose existing Product matches, company-specific aliases, new draft/internal Products, company matches/new companies, skips, or manual review
  - durable changes happen only after staff selects and applies suggestions
  - alias conflicts and duplicate Product names are guarded before approval mutates data
  - ready rows still use the existing duplicate-safe historical price-history commit flow

Partially completed:
- None

Remaining in Phase 1:
- None

## Local Manual Testing Steps

Run the backend from `backend/`:

```bash
python manage.py migrate
python manage.py runserver
```

Run the frontend from `frontend/` in a second terminal:

```bash
npm start
```

Create or confirm a staff user:

```bash
cd backend
python manage.py createsuperuser
```

Login flow:
- Open `http://localhost:3000/login`
- Log in with the staff/superuser account
- Open `http://localhost:3000/admin`
- Confirm the top-level admin tabs include `Overview`, `Products`, `Orders`, and `Quotations`
- Click `Quotations`

Create a company:
- Go to `Quotations -> Companies`
- Fill `Name`; optional fields include email, phone, TRN, billing address, and notes
- Click `Save Company`
- Select the saved company from the table
- Optionally add a contact in the `Contacts` section

Create a product item:
- Go to `Quotations -> Products / Items`
- Fill `Product Name`
- Keep status as `Draft / Internal` for quotation-only items that should not appear on the public website
- Use `Active / Public` only for products that should be visible in the public catalog
- Optionally fill SKU, barcode, dosage, pack/unit, active ingredient, and description
- Click `Save Product Item`

Create an inquiry:
- Go to `Quotations -> Inquiries`
- Select a company and optional contact
- Enter a subject and source text
- Add one or more requested lines
- For each line, either select a Product to confirm the match or leave it unmatched
- Click `Save & Open Quotation`; the inquiry is saved, its idempotent quotation is created, and the quotation editor opens directly

Import an inquiry:
- Go to `Quotations -> Inquiries`
- In `Import Inquiry`, select a company and optional contact
- Choose one source:
  - `Paste Text`: paste item lines, then click `Extract Lines`
  - `Upload File`: choose or drop an `.xlsx`, `.xlsb`, `.xls`, `.pdf`, `.png`, `.jpg`, `.jpeg`, or `.webp` file, then click `Parse File`
- The upload automatically identifies Excel, PDF, or image input. The backend still validates the real file signature and rejects unsupported or renamed files.
- Review the preview table before saving:
  - requested item name
  - quantity
  - unit
  - parse status
  - confidence
  - raw source line
- Delete weak/irrelevant rows, insert missing rows at any position, or drag/use arrow controls to reorder them
- Click `Save & Open Quotation`

Import limitations:
- Uploaded source files are stored as private source-file refs after a successful parse; they are not exposed through public URLs.
- PDF import uses selectable text/tables first and can use capped Vision AI when explicitly enabled.
- Image import requires configured Vision AI. Images are decoded, bounded, orientation-corrected, downscaled, and stripped of metadata before provider use.
- `AI Clean & Apply` updates inquiry rows in one action and provides Undo. Empty AI results and lossy structured-Excel results never replace existing rows.
- OCR, Gmail import, fully automatic AI product matching, and background workers are intentionally not part of this implementation.
- No AI API calls happen when `Enable AI Parsing` is off in `Quotations -> Settings` or the global environment kill switch is disabled.
- Missing Product matches never trigger AI cleanup. Parse confidence describes extraction quality only.

Backfill historical finalized quotation prices:
- Go to `Quotations -> Historical Imports`
- Upload an old finalized Al Ameen quotation PDF
- Confirm the detected quotation number, quotation date, suggested company name, and totals
- Select the real company from `Companies`; create it first in `Quotations -> Companies` if needed
- Review each extracted price row
- Link each usable row to a `Product`; use `Create Products` for internal draft Products that do not exist yet
- Mark reviewed usable rows as `Ready`; mark irrelevant/unclear rows as `Skipped`
- Click `Commit Price History`
- Confirm `Quotations -> Price History` shows the imported company-specific prices
- This does not create a new active quotation to send. It creates a hidden finalized historical quotation record only to keep price history traceable.

Batch backfill old finalized quotation prices:
- Go to `Quotations -> Historical Imports`
- In `Batch Historical Learning`, choose multiple PDF files; V1 caps the browser upload at 25 files
- Click `Upload Batch`; the frontend uploads files sequentially so one slow/duplicate file does not silently block the rest
- Open the created batch and select imports that should be reviewed
- Click `Run AI Suggestions` only when AI Parsing is enabled and provider keys are configured
- Review `AI Suggestion Review`; AI suggestions are not durable data until selected and applied
- Approve/edit high-confidence Product matches, company-specific aliases, new draft/internal Products, company suggestions, skips, or manual-review decisions
- Mark complete rows/imports ready, then commit selected ready imports to price history through the existing duplicate-safe commit logic
- Missing Product matches and low Product catalog coverage are expected during setup; staff approval is still the source of truth

Create a quotation from an inquiry:
- In `Quotations -> Inquiries`, click `Create Quotation from Inquiry` beside the inquiry
- The button changes to `Creating...` while the request is in progress
- After success, click `Open Quotation`
- Confirm each line is matched to a Product
- Enter quantity, unit, unit price, and VAT rate as needed
- Click `Save All Lines`, or use row-level `Save`, after editing

Create a quotation directly:
- Go to `Quotations -> Quotations`
- Select a company and optional contact
- Click `Create Quotation`
- Add lines manually in the quotation editor

Finalize a quotation:
- In the quotation editor, confirm every non-ignored line has:
  - matched Product
  - `Confirmed` match status
  - positive quantity
  - positive unit price
- Click `Finalize`
- Confirm the quote status changes to `Finalized`
- Confirm the editor shows the locked-quote notice

Download the PDF:
- Open a quotation in the quotation editor
- Click `Download Draft PDF` for a draft, or `Download PDF` for a non-draft quotation
- Confirm a PDF downloads with the quotation number as the filename
- The PDF is generated from latest saved quotation data, so save line changes before downloading

Verify price history:
- Finalize a quotation with at least one valid line
- Go to `Quotations -> Price History`
- Filter by company or item
- Confirm the finalized line price appears once
- Try finalizing the same quote again through the API or UI; it should not create duplicate price history

## Manual Smoke Test Checklist

- [ ] Staff user can open `/admin`
- [ ] Non-staff user is redirected away from `/admin`
- [ ] `Quotations` tab is visible only inside the existing admin dashboard
- [ ] Company can be created
- [ ] Company contact can be added
- [ ] Quote item can be created without linking a public product
- [ ] Quote item can optionally link to an existing public product
- [ ] Manual inquiry can be created with multiple lines
- [ ] Pasted inquiry text can be extracted into preview lines
- [ ] Excel `.xlsx` inquiry can be uploaded and previewed
- [ ] Digitally generated PDF inquiry can be uploaded and previewed
- [ ] Scanned/no-text PDF shows a clear no-OCR warning
- [ ] Imported inquiry preview lines can be edited, deleted, and added before saving
- [ ] Saved imported inquiry does not automatically create a quotation
- [ ] Inquiry line can be matched to a quote item
- [ ] Quotation can be created from inquiry
- [ ] Clicking `Create Quotation from Inquiry` multiple times does not create duplicates
- [ ] Quotation can be created directly from a company
- [ ] Quotation line can be added, edited, and deleted while editable
- [ ] `Save All Lines` saves edited line data and clears unsaved indicators
- [ ] Previous price history can be viewed from the editor or Price History tab
- [ ] Invalid quote cannot be finalized
- [ ] Valid quote can be finalized
- [ ] Finalized quote is visibly locked
- [ ] Finalized quote cannot be edited
- [ ] Finalized quote appends price history once
- [ ] Finalized/sent quote can create a new draft revision
- [ ] PDF downloads for staff
- [ ] PDF has pharmacy branding, quotation metadata, totals, terms, and signature/stamp area
- [ ] `Quotations -> Settings` opens for staff and saves PDF branding details
- [ ] Invalid logo, signature, or stamp uploads are rejected with a clear validation error
- [ ] `Quotations -> Historical Imports` opens for staff
- [ ] Old finalized quotation PDF can be parsed into staged historical price rows
- [ ] Historical import rows can be linked to Quote Items and marked ready
- [ ] Committing historical imports appends price history once and hides the generated historical quotation from normal quotation lists
- [ ] Duplicate historical imports do not append duplicate company price-history rows
- [ ] Anonymous users cannot access quotation API endpoints
- [ ] Normal customer users cannot access quotation API endpoints
- [ ] Staff users can access quotation API endpoints

## Product Item Direction

Current direction: `Product` is the master item catalog for both ecommerce and quotation workflows.

- Public storefront items are `Product(status="active")`.
- Internal quotation-only items are `Product(status="draft")`.
- Archived Products are hidden from public catalog and excluded from quotation matching.
- `QuoteItem` is deprecated but kept temporarily so existing migrations, old rows, cleanup scripts, and rollback paths remain safe. New runtime flows should use Product FKs.
- Public product pages must never expose quotation aliases, company price history, private import data, or quotation PDFs.

Aliases:
- `ProductAlias(company=<company>, alias="band aids", product=<product>)` is company-specific.
- `ProductAlias(company=None, ...)` is global.
- Company-specific aliases override global aliases and deterministic name matching, so the same alias text can map to different Products for different companies.

## Models Added

Implemented Phase 1 models:
- `Company`
- `CompanyContact`
- `QuoteItem`
- `Inquiry`
- `InquiryLine`
- `Quotation`
- `QuotationLine`
- `CompanyPriceHistory`
- `QuotationAuditLog`
- `QuotationSettings`
- `HistoricalPriceImport`
- `HistoricalPriceImportLine`
- `ProductAlias`
- `HistoricalImportBatch`
- `HistoricalImportAISuggestion`

`QuoteItem` is deprecated compatibility storage. New inquiry lines, quotation lines, historical import lines, and price-history rows can link directly to `Product`.

Import metadata fields added for reviewed imported inquiries:
- `Inquiry.source_type`
- `Inquiry.source_filename`
- `Inquiry.source_mime_type`
- `Inquiry.source_sha256`
- `Inquiry.source_file_ref`
- `Inquiry.source_file_size`
- `Inquiry.parse_method`
- `Inquiry.parse_meta`
- `InquiryLine.raw_line`
- `InquiryLine.parse_status`
- `InquiryLine.parse_confidence`

Historical price backfill models:
- `HistoricalPriceImport` stores the reviewed source metadata for an old finalized quotation PDF: company, suggested company name, source filename/MIME/SHA-256/private ref, parser metadata, document number/date, currency, totals, status, committed user/time, and the hidden historical quotation created during commit.
- `HistoricalPriceImportLine` stores each extracted old price row: raw source line, cleaned item text, linked `Product`, compatibility `QuoteItem`, quantity, unit, unit price, VAT/total fields, serial/page/row metadata, parse confidence, status, duplicate reason, match reason, and notes.
- `Quotation.is_historical_import` marks hidden finalized quotations created only to keep historical price-history rows traceable. Normal `GET /api/quotations/quotes/` excludes these unless `include_historical=true`.
- `HistoricalImportBatch` groups staged historical imports from a multi-file upload and stores per-file parse/duplicate/failure summaries.
- `HistoricalImportAISuggestion` stores pending review-only company and line suggestions from AI learning. Suggestions can point to existing Companies/Products, propose new Companies/Products, propose aliases, mark skips, or flag manual review, but they are not durable business changes until staff applies them.

## API Endpoints Added

Implemented base path: `/api/quotations/`

Implemented endpoints:
- `/companies/`
- `/contacts/`
- `/items/`
- `/aliases/`
- `/inquiries/`
- `/inquiry-lines/`
- `/historical-imports/`
- `/historical-import-lines/`
- `/historical-import-batches/`
- `/historical-import-ai-suggestions/`
- `/quotes/`
- `/quote-lines/`
- `/price-history/`
- `/audit-logs/`
- `/settings/`

Implemented custom actions:
- `POST /inquiry-lines/{id}/remember_alias/`
- `POST /quote-lines/{id}/remember_alias/`
- `POST /historical-import-lines/{id}/remember_alias/`
- `POST /inquiries/parse_text/`
- `POST /inquiries/parse_file/`
- `POST /inquiries/create_imported/`
- `POST /inquiries/{id}/create_quote/` (idempotent for existing inquiry quotations)
- `POST /historical-imports/parse_file/`
- `POST /historical-imports/{id}/commit/`
- `GET /historical-imports/{id}/preview_page/`
- `POST /historical-imports/{id}/run_ai_suggestions/`
- `POST /historical-import-batches/{id}/upload_file/`
- `POST /historical-import-batches/{id}/run_ai_suggestions/`
- `POST /historical-import-batches/{id}/apply_ai_suggestions/`
- `POST /historical-import-batches/{id}/commit_ready_imports/`
- `POST /historical-import-ai-suggestions/apply/`
- `POST /historical-import-ai-suggestions/reject/`
- `GET /companies/{id}/price_history/`
- `POST /quotes/{id}/submit_review/`
- `POST /quotes/{id}/approve/`
- `POST /quotes/{id}/finalize/`
- `POST /quotes/{id}/mark_sent/`
- `POST /quotes/{id}/revise/`
- `POST /quotes/{id}/cancel/`
- `GET /quotes/{id}/pdf/`

Import endpoints:
- `parse_text` accepts JSON with `raw_text`, runs deterministic rules, and returns preview JSON only.
- `parse_file` accepts multipart `file`, supports `.xlsx` and `.pdf`, validates size/type/signature, computes SHA-256, and returns preview JSON only.
- `create_imported` accepts reviewed preview JSON and atomically creates `Inquiry` plus `InquiryLine` rows. It does not create a quotation automatically.

Historical import endpoints:
- `POST /historical-imports/parse_file/` accepts a finalized quotation PDF, validates it, stores the source file in private quotation storage, parses known Al Ameen quotation table fields, and creates a staged `HistoricalPriceImport` with review lines.
  - Duplicate detection runs before creating a new staged import. Exact SHA-256 re-uploads and same-company quotation-number matches return the existing import with `duplicate_check` metadata instead of silently creating another staged import.
  - Similar date/totals/row fingerprints add a warning but still allow staff review.
  - A deliberate future override can use `force_new_import=true`; the current UI prefers opening the existing import.
- `PATCH /historical-imports/{id}/` updates reviewed company/date/metadata before commit.
- `PATCH /historical-import-lines/{id}/` updates reviewed row fields, linked `Product`, notes, and row status.
- `POST /historical-imports/{id}/bulk_create_quote_items/` is a backward-compatible endpoint name that now creates/links internal draft Products for selected rows and links deterministic existing Product matches instead of duplicating them.
- `POST /historical-imports/{id}/bulk_update_rows/` marks selected rows `ready`, `needs_review`, or `skipped` with validation and per-row results.
- `POST /historical-imports/{id}/bulk_skip_rows/` marks selected rows skipped so they remain visible but are excluded from commit.
- `POST /historical-imports/{id}/commit/` atomically creates hidden finalized historical quotation/line records and appends non-duplicate `CompanyPriceHistory` rows only for reviewed `ready` lines.
- `GET /historical-imports/{id}/preview_page/` streams a staff-only first-page PNG preview from private storage when PyMuPDF is available.
- `POST /historical-imports/{id}/ai_clean_rows/` returns AI-cleaned candidate price rows for staff review when AI parsing is enabled.
- `POST /historical-imports/{id}/apply_ai_clean_rows/` replaces staged historical import rows with explicitly approved AI-cleaned rows and leaves them `needs_review`.
- `POST /historical-imports/{id}/run_ai_suggestions/` stores review-only company/Product/alias/new-product suggestions for one staged historical import.
- `POST /historical-import-batches/` creates a batch wrapper for a multi-file historical import run.
- `POST /historical-import-batches/{id}/upload_file/` uploads one PDF into the batch, reusing existing duplicate detection and normal staged import creation.
- `POST /historical-import-batches/{id}/run_ai_suggestions/` runs review-only AI suggestions for selected imports in the batch.
- `POST /historical-import-batches/{id}/apply_ai_suggestions/` applies selected suggestions only after staff approval.
- `POST /historical-import-batches/{id}/commit_ready_imports/` commits selected ready imports through the existing duplicate-safe historical price-history flow.
- `PATCH /historical-import-ai-suggestions/{id}/` lets staff edit the pending suggestion action/target/proposed fields before approval.
- `POST /historical-import-ai-suggestions/apply/` applies selected pending suggestions.
- `POST /historical-import-ai-suggestions/reject/` rejects selected pending suggestions.

AI parsing endpoints:
- `POST /inquiries/ai_clean_parse/` accepts the deterministic inquiry preview JSON and returns AI-cleaned candidate rows. It does not save an inquiry.
- `POST /historical-imports/{id}/ai_clean_rows/` accepts a staged historical import and returns candidate rows. It does not mutate rows.
- AI cleanup responses include `result_source` (`ai_text_cleanup` or `ai_vision_cleanup`), provider/model metadata, cache-hit flag, warnings, and validated rows.
- AI parse calls are staff-only and backend-only; API keys are never exposed to React.

Settings endpoints:
- `GET /api/quotations/settings/` returns the singleton quotation PDF branding settings, creating a default settings row if one does not exist.
- `PATCH /api/quotations/settings/` updates settings. It accepts JSON for text/style changes and multipart form data when uploading a logo.
- All settings access uses `IsQuotationStaff`.

## Frontend Components Added

Implemented React admin components:
- `QuotationModule`
- `QuotationDashboard`
- `CompanyManager`
- `QuoteItemManager`
- `InquiryManager`
- `QuotationList`
- `QuotationEditor`
- `PriceHistoryPanel`
- `HistoricalImportManager`
- `AuditLogPanel`
- `QuotationSettings`
- `QuotationErrorNotice`

The module appears only inside the existing React admin dashboard at `/admin` as a top-level `Quotations` tab beside `Overview`, `Products`, and `Orders`.

`InquiryManager` now includes an `Import Inquiry` area with paste text, Excel upload, PDF upload, shared preview/review table, save imported inquiry, and create quotation from saved inquiry actions. The existing manual inquiry form remains available as a fallback.

`QuotationSettings` provides a staff-editable branding page at `Quotations -> Settings`. It controls company details, logo, optional signature/stamp images, terms, validity, payment text, footer note, style colors, template style selection, and signature/stamp labels used by generated PDFs.

`HistoricalImportManager` provides `Quotations -> Historical Imports`. It is separate from the new inquiry importer because it backfills old approved prices instead of creating a live customer quotation. Staff upload a finalized quotation PDF, review detected company/date/totals and price rows, link rows to `Product`, and commit only approved rows into company-specific price history.

The historical import review header is organized as a staff workflow instead of a raw form: a `Document Preview` card shows the source file, parser status, and lines found, while an `Import Details` card groups import summary, company selection/inline company creation, quotation number/date/totals, and the `Save Import Details` action. The suggested company from the file appears as a banner with a `Use suggestion` action. `+ New Company` opens the inline company form, `Create Company` submits it, and newly created companies are selected automatically with a selected-company badge. Final price-history commit remains in the sticky row-review bar below.

Historical import bulk workflow:
- Select a staged historical import.
- Use row filters (`All`, `Ready`, `Needs Review`, `Unmatched`, `Skipped`, `Errors`) and search to narrow the table.
- Use `Select Visible`, `Select Unmatched`, `Select Needs Review`, or `Select Ready`.
- Click `Create Products` to create/link internal draft Products for the selected rows. Exact deterministic matches are linked instead of duplicated.
- Use `Mark Ready`, `Needs Review`, or `Skip` for selected rows. `Ready` requires company, quotation date, linked Product, quantity greater than zero, and unit price zero or more.
- Commit from the sticky bottom bar. Only ready rows are committed into price history; skipped/needs-review/duplicate rows are ignored.

Historical import duplicate behavior:
- Re-uploading the exact same historical quotation PDF shows `This PDF has already been added before.` and does not create a duplicate staged import.
- Staff stay on the current upload/review screen. `View previous import` opens the existing staged or committed import only when clicked.
- Same-company quotation-number matches are treated as blocking duplicates by default.
- Same date/totals with highly similar item rows is treated as a possible duplicate warning so staff can review before commit.
- The commit service remains idempotent: duplicate ready rows are marked duplicate and do not append another `CompanyPriceHistory` record.

`CompanySelectWithCreate` is the shared inline company selector/creator used by daily quotation workflows. Use it instead of a raw company `<select>` when staff are choosing a company for a quotation/inquiry/backfill task, so missing companies can be added without leaving the current screen.

## Phase 1 Stabilization: Sub-Tab Error Fix

Issue found:
- The quotation sub-tabs rendered generic error behavior because local React was configured to call the deployed Railway API: `https://al-ameen-pharmacy-production.up.railway.app/api`.
- The Phase 1 quotation routes existed only in the local Django code at the time of testing, so production returned `404` for `/api/quotations/...`.
- Local Django had `quotations.0001_initial` applied and returned `401` for anonymous quotation requests, confirming the local routes existed and were permission-protected.

Fix:
- Local `frontend/.env` now points to `REACT_APP_API_URL=http://localhost:8000/api` for development.
- The Railway API URL remains documented as the deployed-backend option, but should only be used after deploying the backend quotation routes/migrations there.
- Quotation load/save/finalize/PDF errors now render `QuotationErrorNotice` inline with:
  - failed action
  - endpoint
  - HTTP status
  - backend detail

Verified in browser:
- `Dashboard` opens with no quotation error panel.
- `Companies` opens with no quotation error panel.
- `Quote Items` opens with no quotation error panel; it may take longer because it also loads the existing `/api/products/` list.
- `Inquiries` opens with no quotation error panel.
- `Quotations` opens with no quotation error panel.
- `Price History` opens with no quotation error panel.
- `Audit Logs` opens with no quotation error panel.

Verification commands run after the fix:

```bash
cd backend
python manage.py showmigrations quotations
python manage.py migrate --plan
python manage.py test quotations --keepdb
python manage.py check

cd ../frontend
npm run build
```

Results:
- `quotations.0001_initial` is applied.
- `migrate --plan` reports no pending operations.
- `python manage.py test quotations --keepdb` passes 9 tests.
- `python manage.py check` passes with no issues.
- `npm run build` passes with the existing non-quotation warnings in `OrderManagement.js` and `ProductDetail.js`.

## Phase 1 Stabilization: Quote Items React Auth And Slow Products

Issue checked:
- Direct browser access to `/api/quotations/items/` returns `401` without JWT, which is expected.
- From the logged-in React admin dashboard, the quotation item request must use the shared JWT Axios instance and include `Authorization: Bearer ...`.
- `/api/products/` can be slow, but linking a public product is optional for quote items and should not block the private quote item workflow.

Findings:
- `/api/me/` with the active JWT returned `200` and `is_staff: true`.
- The JWT access token was present and not expired during verification.
- React requests to `/api/quotations/items/` returned `200`.
- React requests to `/api/quotations/items/` included the `Authorization: Bearer ...` header.
- No `/api/token/refresh/` request was needed in the verified run.
- `/api/products/` remained slow, but quote item loading now works independently from the optional public product dropdown.

Fix:
- `QuoteItemManager` now loads `/api/quotations/items/` and `/api/products/` independently.
- The quote item table and private quote item form can render as soon as `/api/quotations/items/` succeeds.
- While `/api/products/` is still loading, the optional product select shows a loading option and a notice telling staff they can leave the public product link blank.
- If `/api/products/` fails, staff can still save a private quote item and retry the product dropdown load.

## Local Development Performance Notes

Local frontend API target:
- Keep local development on `REACT_APP_API_URL=http://localhost:8000/api` so new quotation routes are exercised against the local Django code.
- Do not switch local development back to the deployed Railway API unless the deployed backend has the same quotation routes and migrations.

Local backend/database behavior:
- The local backend currently uses PostgreSQL through a Neon pooler host in the `ap-southeast-1` region.
- Local Django plus remote Neon can be slower than deployed Railway when Railway is closer to Neon or has warmer connections.
- `DEBUG=1` is enabled locally, which is expected for development but can add some overhead.

Product endpoint findings:
- Before the product optimization, local `GET /api/products/` took about 21.5 seconds.
- The response was about 70 KB for 107 products, so payload size was not the main issue.
- The main bottleneck was an existing product image N+1 query: product serialization ran 109 queries because `Product.primary_image` queried images per product even though images were prefetched.
- After using prefetched images correctly, product serialization dropped to 2 queries and local `GET /api/products/` measured about 4.2 seconds.
- The remaining time is mostly remote database latency/cold connection cost, not quotation code.

Admin dashboard performance:
- The admin shell and tabs should render immediately for staff users.
- Admin overview no longer blocks the whole dashboard on full product loading.
- Admin stats now use `GET /api/products/summary/` for a count instead of loading all product records.
- Product and order stats load inside the overview cards; slow stats should not prevent opening Products, Orders, or Quotations.

Quote Items performance:
- Quote Items no longer blocks on optional public product dropdown loading.
- The optional public product dropdown now uses `GET /api/products/?compact=true&limit=200`, which returns only product IDs and names.
- Staff can create private quote items while the optional public product dropdown is still loading.

Final browser performance verification on May 21, 2026:
- Home page rendered product cards successfully against the local backend; observed product-card load was about 3.0 seconds in the final smoke run.
- Product card images, quick view images, and product detail gallery images still rendered after the N+1 image-query fix.
- Admin dashboard shell and tabs rendered in about 146 ms in the final smoke run.
- `GET /api/products/?compact=true&limit=200` returned 107 compact rows with only `id` and `name`.
- `GET /api/products/summary/` returned only `{"count": 107}` for admin overview product stats.
- Local full `GET /api/products/` remains slower than production-like Railway behavior because local Django is talking to remote Neon, but it is no longer blocked by product-image N+1 queries.

## Final Phase 1 Browser Verification

Final smoke pass completed on May 21, 2026 against:
- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`
- API target: `REACT_APP_API_URL=http://localhost:8000/api`

Product/admin checks passed:
- Home page loads products.
- Product cards show images.
- Product quick view opens.
- Product detail page opens from quick view.
- Product detail image gallery switches thumbnails on a multi-image product.
- Admin dashboard shell and tabs render immediately.
- Admin Products tab opens.
- Admin Orders tab opens.
- Compact products endpoint returns only product IDs/names.
- Product summary endpoint returns only count data.
- Product image behavior still works after the N+1 fix.

Quotation workflow checks passed:
- Created a company.
- Created a company contact.
- Created quote items without linking them to public `Product` records.
- Created an inquiry with multiple matched lines.
- Created a quotation from that inquiry.
- Edited quotation line quantities, units, prices, and confirmed match statuses.
- Finalized the quotation.
- Confirmed the finalized quotation is locked.
- Downloaded the PDF from the browser.
- Confirmed two price history rows were appended once, and a second finalize request returned `400` without duplicating history.
- Created a draft revision from the finalized quotation.
- Confirmed the original quotation stayed locked and the revision was editable.

Optional product dropdown checks passed:
- Quote Items loads independently from the optional public product dropdown.
- A private quote item can be created without a linked public product.
- If the compact products request fails, the quote item form still works and shows a non-blocking notice.

Security checks passed:
- Anonymous quotation API request: `401`.
- Normal non-staff quotation API request: `403`.
- Staff quotation API request: `200`.
- Anonymous PDF endpoint request: `401`.
- Normal non-staff PDF endpoint request: `403`.
- Staff PDF endpoint request: `200`.
- Non-staff users do not see the `Quotations` admin tab.

Temporary smoke-test companies, quote items, inquiries, quotations, price history, audit logs, and test users were removed after verification.

Final checks after the browser pass:
- `python manage.py test quotations --keepdb` passed 9 tests.
- `python manage.py check` passed with no issues.
- `npm run build` passed with the existing non-quotation warnings in `OrderManagement.js` and `ProductDetail.js`, plus the existing Browserslist data-age notice.

## Phase 1 Hardening: Workflow UI And PDF Branding

Status: implemented and browser verified on May 21, 2026.

Workflow/UI changes:
- Manual Inquiry requested lines now use wrapping responsive layout so requested item name, quantity, unit, match select, and Delete stay inside the card.
- Inquiry creation is still manual and staff-controlled. The form now presents the workflow as:
  - Step 1: Company
  - Step 2: Inquiry Lines
  - Step 3: Create Quote
- `Create Quotation from Inquiry` disables immediately after the first click and shows `Creating...`.
- The backend `create_quote` action is idempotent. If a quotation already exists for an inquiry, the endpoint returns that existing quotation instead of creating another one.
- After quote creation, the UI shows a single success panel with `Open Quotation`.
- Quotation editor actions (`Submit Review`, `Approve`, `Finalize`, `Mark Sent`, `Create Revision`, `Cancel`, and PDF download) use loading/disabled states to prevent duplicate submissions.
- The editor shows the status path `Draft -> Pending Review -> Approved -> Finalized -> Sent`.
- Finalize is disabled with helper text until required line data is saved and valid.
- Staff can edit multiple quotation lines and click `Save All Lines`; each row shows saved/unsaved state.
- PDF buttons are labeled `Download Draft PDF` or `Download PDF`, with helper text explaining the PDF uses latest saved data.

PDF branding:
- ReportLab remains the stable PDF generator.
- PDFs are generated from quotation snapshot fields, not mutable live product fields.
- PDFs include the configured pharmacy name, optional Arabic name, address, phone, email, TRN/license, optional logo, quotation metadata, line table, totals box, terms, payment terms, prepared-by line, and signature/stamp area.
- PDFs continue to stream through the staff-only backend endpoint and are not stored in public Cloudinary URLs.

Staff-editable branding settings:
- Settings are edited in `Admin Dashboard -> Quotations -> Settings`.
- Backend data lives in singleton model `quotations.QuotationSettings`.
- API endpoints are `GET/PATCH /api/quotations/settings/`.
- Supported fields include company name, Arabic name, address, phone, email, TRN, license number, logo, signature image, stamp image, footer note, default terms, payment terms, validity days, prepared-by default, signature/stamp labels, PDF template style, primary/accent colors, and display toggles.
- If no settings row exists, the API returns sensible defaults and creates the singleton settings record.
- Environment variables remain fallback defaults through `backend/quotations/pdf_config.py`, but daily changes should be made through the Settings page.

Branding image uploads:
- Staff can upload logo, signature, and stamp images as `png`, `jpg`, `jpeg`, or `webp`.
- The backend validates extension, MIME type, file size, and basic binary signature.
- Default max upload size is `QUOTATION_BRANDING_IMAGE_MAX_UPLOAD_BYTES`, currently 2 MB.
- Image storage uses the configured Django file storage/media setup. These images are branding material; quotation PDFs and quote data remain protected and are not exposed through public PDF URLs.
- PDF rendering supports local filesystem paths, local `/media/...` URLs, and storage-backed `http`/`https` URLs such as Cloudinary. This fixes uploaded logos disappearing from generated PDFs when storage returns a URL instead of a local file path.
- If a signature or stamp image is configured, the generated PDF renders it in the approval area. If no image is configured, the PDF falls back to the configured text label.

PDF template style:
- `classic` is the implemented polished default.
- `modern` and `compact` are reserved style choices so the data model/API can support additional layouts later without changing the settings contract.

Logo notes:
- By default, local development looks for `frontend/public/brand/al-ameen-pharmacy-logo-dark.png`.
- On Railway, set `QUOTATION_LOGO_PATH` only if that file path exists in the deployed filesystem.
- If no readable logo exists, the PDF falls back to text branding without failing.

Word-template investigation:
- Filling a `.docx` template is feasible with `python-docx` or `docxtpl`.
- Reliable DOCX-to-PDF conversion on Railway/Linux usually requires LibreOffice in headless mode or an external conversion service.
- Adding LibreOffice/package-level document conversion is too heavy and risky for this production-safe pass, so Word-template PDF support is deferred.
- Preferred future approach: allow staff/admins to upload a static PDF/image background template with logo/header/footer already designed, then use ReportLab to overlay quotation metadata, dynamic lines, totals, terms, and signature text. This avoids LibreOffice and keeps Railway deployment simpler.
- A DOCX-template path should only be added later if the deployment explicitly supports LibreOffice/headless conversion or a trusted external conversion service.

Hardening browser verification on May 21, 2026:
- Manual Inquiry requested-line layout no longer overflows; the Delete button stayed inside the right-side form card and no horizontal body overflow was detected.
- Quote Items allowed creation of a private quote item while the optional public product dropdown was still loading.
- A manual inquiry with two requested lines was created successfully.
- Double-clicking `Create Quotation from Inquiry` showed `Creating...` and produced one quotation only.
- The success panel showed one `Open Quotation` action.
- The quotation editor showed status progress, required-field guidance, and blocked finalization until prices were saved.
- Editing two lines showed unsaved states; `Save All Lines` saved both lines and returned to `All line changes saved`.
- Finalization succeeded after accepting the confirmation dialog; the quotation became locked and editable controls were disabled.
- `Download PDF` downloaded a protected PDF. Text inspection confirmed the branded PDF contains Al Ameen Pharmacy, the quotation number, customer name, terms, signature, and stamp text.
- Security checks returned `401` for anonymous quotation API/PDF requests, `403` for normal non-staff requests, and `200` for staff requests.
- Temporary smoke-test records, users, and downloaded files were removed after verification.

## Safe Inquiry Import Workflow

Status: production importer hardening implemented and verified on May 22, 2026.

Purpose:
- Help staff turn pasted text, company Excel LPOs, or digitally generated PDFs into reviewed inquiry lines.
- Keep humans in control before any inquiry is saved.
- Keep uploaded inquiry source files private. The parser stores a private source-file reference after a successful parse and never exposes a public URL.

Supported sources:
- Pasted text
- `.xlsx`, `.xlsb`, and `.xls` Excel workbooks
- Digitally generated `.pdf` files with selectable text/tables
- `.png`, `.jpg`, `.jpeg`, and `.webp` inquiry screenshots when Vision AI is configured

Unsupported in this implementation:
- scanned/image-only PDF OCR
- `.csv`, `.docx`, HEIC, email messages, ZIPs, or arbitrary file types
- AI matching or product guessing
- automatic quotation creation from uploaded files
- public storage or public download URLs for uploaded inquiry source files

Parser modules:
- `backend/quotations/import_rules.py`
  - deterministic line normalization, title/header detection, repeated-header skipping, serial stripping, and common quantity/unit patterns
  - examples covered by tests:
    - `Panadol 500mg - 10 boxes`
    - `Panadol 500mg x 10`
    - `10 boxes Panadol 500mg`
    - `Gloves medium 5 packs`
    - `1. Panadol 500mg - 10 box`
    - `SL NO | ITEMS | UNIT | QUANTITY` with quantities like `1 bottle`, `1 BOX`, `5 Nos`, and `2 packs`
- `backend/quotations/import_parsers.py`
  - upload size/type/signature validation through optional `python-magic`, `filetype`, and explicit PDF/ZIP/OLE signatures
  - SHA-256 hashing
  - Excel parsing with `openpyxl.load_workbook(read_only=True, data_only=True)` for `.xlsx`
  - Excel parsing fallback/coverage with `python-calamine` for `.xlsx`, `.xlsb`, and `.xls`
  - PDF preflight with `pypdf`
  - PDF text classification with PyMuPDF
  - PDF table/text extraction with `pdfplumber`
- `backend/quotations/private_storage.py`
  - saves successful uploaded inquiry source files under a private storage root and returns a DB metadata ref only
- `backend/quotations/ocr.py`
  - scaffolds OCR provider interfaces for `local_tesseract` and `google_document_ai`; neither provider is enabled by default

Security/storage behavior:
- All import APIs use `IsQuotationStaff`.
- Uploaded files are read with a size limit and validated before parsing.
- Successful uploaded source files are stored in private storage, not Cloudinary public media. The inquiry stores metadata such as `source_file_ref`, `source_file_size`, filename, MIME, SHA-256, and parser metadata.
- Browser `Content-Type` is not trusted by itself; extension, MIME sniffing, and binary signatures are checked.
- `.xlsx` and `.xlsb` files must look like ZIP/OpenXML-style files.
- `.xls` files must look like OLE compound documents.
- `.pdf` files must start with a PDF signature and pass `pypdf` preflight.
- Encrypted PDFs are rejected with a clear 400 response.
- Scanned/no-text PDFs return preview JSON with the warning: `No selectable text detected. OCR is not enabled in this environment.`
- Local development private files live under `backend/private_media/quotations` by default and are gitignored. For Railway production, configure durable private object storage or persistent private storage before relying on source-file retention.

Default limits:
- `QUOTATION_IMPORT_MAX_UPLOAD_BYTES`: 5 MB
- `QUOTATION_IMPORT_MAX_EXCEL_ROWS`: 500 rows per inspected sheet
- `QUOTATION_IMPORT_MAX_EXCEL_SHEETS`: 10 sheets
- `QUOTATION_IMPORT_MAX_PDF_PAGES`: 10 pages
- `QUOTATION_IMPORT_STORE_SOURCE_FILES`: `1`
- `QUOTATION_PRIVATE_STORAGE_ROOT`: `backend/private_media/quotations` by default
- `QUOTATION_IMPORT_OCR_PROVIDER`: empty by default

Dependencies added:
- `PyMuPDF`
- `pdfplumber`
- `pypdf`
- `openpyxl`
- `defusedxml`
- `filetype`
- `python-calamine`
- `pyxlsb`

OCR is deferred as a runtime feature because Tesseract/Poppler/image conversion would add heavy native dependencies and deployment risk on Railway. The provider interface exists now; enabling OCR should be a separate deployment decision using either a managed OCR provider or a verified local runtime image.

Importer verification on May 22, 2026:
- Backend tests cover the exact `FIRST AID MATERIAL LOG.xlsx` pattern:
  - row 1 title skipped
  - row 2 header detected
  - serial numbers excluded from item names
  - `1 bottle`, `1 BOX`, `5 Nos`, and `2 packs` split into quantity/unit correctly
  - no false `no clear header row detected` warning
- Pasted text extracted two preview lines.
- Reviewed pasted-text inquiry saved successfully.
- `Create Quotation from This Inquiry` worked only after the imported inquiry was saved.
- Excel `.xlsx` upload now parses through `openpyxl_structured_v2`.
- Digitally generated PDF upload now parses through `pymupdf_pdfplumber_table_v2` or `pymupdf_text_v2`.
- Blank/no-text PDF showed the expected no-OCR warning.
- Browser verification confirmed `Quotations -> Inquiries -> Import Inquiry` opens with the Excel mode, company selector, private-source helper text, and compact import controls.
- The Browser plugin could not automate the native file input directly, so the FIRST AID workbook was verified through the same staff-only `/api/quotations/inquiries/parse_file/` endpoint with a real generated `.xlsx` upload. The API returned `200`, selected header row 2, parsed 4/4 lines, returned no warnings, and included a private source-file ref.

## Quotation Settings And PDF Branding

Status: implemented and browser/API verified on May 22, 2026.

Purpose:
- Let staff/admin users update quotation PDF branding and default business text without editing code or environment variables.
- Keep ReportLab as the stable production-safe PDF generator.
- Avoid heavy template-conversion dependencies on Railway.

Where to edit:
- Open `Admin Dashboard -> Quotations -> Settings`.

Backend implementation:
- Model: `quotations.QuotationSettings`
- Migrations:
  - `quotations.0003_quotationsettings`
  - `quotations.0004_quotationsettings_signature_image_and_more`
  - `quotations.0005_quotationsettings_logo_layout`
- API:
  - `GET /api/quotations/settings/`
  - `PATCH /api/quotations/settings/`
- Serializer: `QuotationSettingsSerializer`
- Permission: `IsQuotationStaff`
- Django Admin backup access: `QuotationSettingsAdmin`

Settings fields that affect generated PDFs:
- company name and optional Arabic name
- address, phone, email, TRN, and license number
- logo
- logo layout
- signature image
- stamp image
- default terms
- payment terms
- validity days
- footer note
- prepared-by default
- signature and stamp labels
- primary and accent colors
- display toggles for Arabic name, TRN, license number, signature area, and stamp area
- `pdf_template_style`

Logo layout options:
- `full_logo_only`: use the uploaded logo as the complete brand lockup. Do not print a separate large company name beside it. This is the recommended default for the current Al Ameen full logo.
- `logo_plus_company_text`: use a smaller logo plus company name/details.
- `icon_left_company_text`: use an icon-only mark on the left plus company name/details beside it.
- `no_logo`: hide the logo and render company name/details as text.

Use `full_logo_only` when the uploaded logo already includes the icon, Arabic name, English brand name, and `Pharmacy LLC`.

Branding image behavior:
- Staff can upload logo, signature, and stamp images as `png`, `jpg`, `jpeg`, or `webp`.
- The backend validates file extension, MIME type, max size, and basic binary signature.
- Default max size is `QUOTATION_BRANDING_IMAGE_MAX_UPLOAD_BYTES` in Django settings.
- Branding image media may be normal branding media. Quotation PDFs and quotation data remain protected and are not exposed through public PDF URLs.
- PDF rendering supports local filesystem paths, local `/media/...` URLs, and storage-backed `http`/`https` URLs such as Cloudinary.
- This fixes the uploaded-logo issue where Cloudinary-backed logos could preview in the settings UI but disappear from generated PDFs.
- Signature and stamp images render in the approval area when configured. If an image is not configured, the PDF falls back to the text label.
- Uploaded logo, signature, and stamp images can be removed from `Quotations -> Settings` with the visible remove buttons. The backend also supports `clear_logo`, `clear_signature_image`, and `clear_stamp_image` on the staff-only settings PATCH endpoint.
- Recommended logo format: tightly cropped PNG/WebP with transparent background if possible. Avoid huge white padding around the artwork because the PDF preserves the image aspect ratio and fits it into the header.

PDF style:
- `classic` is the implemented polished default.
- `modern` and `compact` are reserved choices for later styles.
- The current code is structured so more styles can be added without replacing the settings API.

Template upload decision:
- Full Word-template upload and DOCX-to-PDF conversion is deferred.
- `python-docx`/`docxtpl` can fill DOCX files, but production-safe PDF conversion on Railway usually needs LibreOffice/headless conversion or an external service.
- Preferred future route: upload a static PDF/image background template and overlay dynamic quotation data with ReportLab. This keeps deployment lighter and avoids LibreOffice.
- A Settings-page `Download Sample PDF` action is not implemented yet. It remains a good future improvement, but was skipped here to avoid adding dummy quotation generation complexity to the stable settings API.

Verification on May 22, 2026:
- Browser opened `/admin`, then `Quotations -> Settings`.
- Settings page loaded defaults and showed the logo, signature, and stamp upload areas, company fields, PDF text fields, style fields, and signature/stamp toggles.
- Settings page showed the `Logo layout` selector with `Full Logo Only`, `Logo + Company Text`, `Icon Left + Company Text`, and `No Logo`.
- Remove buttons were visible for configured logo/signature images; the stamp remove button stays hidden until a stamp image exists.
- Clicking `Save Settings` showed `Quotation settings saved.`
- Anonymous `GET /api/quotations/settings/` returned `401`.
- Normal non-staff `GET /api/quotations/settings/` returned `403`.
- Staff `GET /api/quotations/settings/` returned `200`.
- Staff `PATCH /api/quotations/settings/` returned `200`.
- Runtime PDF generation with the already-uploaded Cloudinary logo embedded an image in the generated PDF.
- Runtime PDF generation with `full_logo_only` produced a header with the uploaded full logo and did not extract a separate duplicate `Al Ameen Pharmacy` text title from the PDF.
- Temporary smoke-test users and temporary settings text were removed after verification.

## Permission Model

Phase 1 uses staff-only backend access. Every quotation API endpoint and custom action must enforce `IsQuotationStaff`.

The permission layer is structured for future role expansion:
- `quotation_viewer`
- `quotation_staff`
- `quotation_manager`
- `quotation_admin`

For now, these roles are extension points only. The actual Phase 1 access rule is `request.user.is_authenticated and request.user.is_staff`.

Frontend tab visibility is only a usability layer. Backend permissions are the security boundary.

## Quotation Workflow

Supported statuses:
- `draft`
- `pending_review`
- `approved`
- `finalized`
- `sent`
- `revised`
- `cancelled`

Rules:
- Only draft, pending review, and approved quotations are editable.
- Finalized, sent, revised, and cancelled quotations cannot be edited directly.
- Revisions create a new draft version linked to the previous quotation.
- A quotation cannot be finalized with unresolved required item matches.
- A quotation cannot be finalized with missing or invalid prices.
- Only finalization appends company-specific price history.
- Finalization and revision creation run inside database transactions.

## PDF Generation Approach

Phase 1 PDFs are generated on demand from stored quotation snapshot fields and streamed from staff-only API endpoints. The generator is ReportLab-based and configured through `backend/quotations/pdf_config.py`.

Sensitive quotation PDFs are not stored in public Cloudinary URLs in Phase 1.

The current PDF layout includes:
- logo-layout-aware pharmacy branding
- pharmacy contact/TRN/license details
- quotation title, quotation number, and date in the header
- quotation metadata
- customer/contact details
- itemized quotation lines
- right-aligned totals
- default terms and payment terms
- prepared-by, signature, and stamp areas

Default branding values live in `backend/pharmacy_api/settings.py` and can be overridden with environment variables, but the active daily configuration should be managed from `Admin Dashboard -> Quotations -> Settings`.

The Settings page controls:
- company name and optional Arabic name
- address, phone, email, TRN, and license number
- logo
- logo layout
- signature image
- stamp image
- default terms, payment terms, validity days, and footer note
- prepared-by text
- signature/stamp labels and visibility toggles
- primary/accent colors
- PDF template style (`classic` implemented; `modern` and `compact` reserved)

Logo/signature/stamp uploads currently use the configured Django storage backend. These images can be served as normal media/static branding, but generated quotation PDFs are still protected and streamed only from staff-only endpoints.

Header behavior:
- `full_logo_only` is the default and prevents duplicated branding when the uploaded logo is a full lockup.
- `logo_plus_company_text` and `icon_left_company_text` are available for smaller/icon-only logos.
- `no_logo` uses text branding only.

Approval behavior:
- A configured signature image renders in the approval section.
- Without a signature image, the placeholder is `Authorized Signature` unless a custom signature label is configured.
- A configured stamp image renders in the approval section.
- Without a stamp image, the placeholder is `Company Stamp` unless a custom stamp label is configured.

Future template customization:
- Preferred: upload a static PDF/image background and overlay dynamic data with ReportLab.
- Deferred: DOCX template upload plus PDF conversion, because reliable conversion on Railway/Linux usually needs LibreOffice/headless conversion or an external service.

## Phase 2/3 Roadmap

Phase 2:
- Broader alias-management UI outside row-level workflows
- Continued deterministic item matching improvements
- Fuzzy matching
- Confirmed-match learning beyond historical import approval actions
- Optional `pg_trgm`
- AI-assisted matching/reranking expansion beyond approval-gated historical import suggestions
- Embeddings and pgvector evaluation
- OCR/scanned document parsing for uploaded inquiries/LPOs

Phase 3:
- Gmail API import
- Gmail draft creation
- Background workers
- Reporting and analytics
- Rich PDF branding and templates
- More granular quotation roles/groups

## Known Risks And Limitations

- Phase 1 staff-only permissions are coarse; finer roles are intentionally deferred.
- PDFs are generated synchronously; this is acceptable for MVP but may need async generation later.
- Manual inquiry entry is intentionally simple; email/document parsing is deferred.
- Import parsing is deterministic and intentionally conservative; unclear non-empty lines are returned for review instead of being silently guessed.
- Excel/PDF import handles common digital LPOs but will need tuning against real company files.
- Product matching remains staff-approved. Historical import batches can receive AI suggestions, but staff must apply or edit them before any Product, alias, Company, or price-history record changes.
- Existing public product/cart/order/auth flows must not be coupled to quotation internals.
- Quotation UI errors now use inline diagnostic panels. Existing non-quotation admin/product/order screens still use their older alert patterns.
- Directly loading `/admin` in a fresh browser session can briefly redirect to `/login` before the existing auth context finishes reading local storage. Navigating to `/admin` from inside the app works. This is an existing admin route-guard timing issue, not quotation-specific, but it is worth cleaning up later.
- Required fields use browser validation but do not yet show visible `Required` markers in every form.
- The quotation module currently has seven sub-tabs. This is complete for MVP, but daily use may be easier if `Price History` and `Audit Logs` move into contextual panels or an advanced area.
- `QuotationEditor`, `InquiryManager`, and backend `models.py`/`views.py` are acceptable for Phase 1 but are natural split points if the module grows.
- Staff can download PDFs for draft quotations. This is useful for review, but the business may later prefer watermarking draft PDFs or allowing PDFs only after approval/finalization.
- Frontend build still reports pre-existing hook dependency warnings in `OrderManagement.js` and `ProductDetail.js`.
- Word-template PDF customization is deferred because production-safe DOCX-to-PDF conversion on Railway would require LibreOffice or an external conversion service.
- The branded PDF currently uses standard ReportLab fonts; Arabic display may need dedicated font/shaping work later if the Arabic pharmacy name must render perfectly.

## Phase 1 API Access Verification

Automated tests cover anonymous, non-staff, and staff access across all quotation list endpoints:
- `/api/quotations/companies/`
- `/api/quotations/contacts/`
- `/api/quotations/items/`
- `/api/quotations/inquiries/`
- `/api/quotations/inquiry-lines/`
- `/api/quotations/quotes/`
- `/api/quotations/quote-lines/`
- `/api/quotations/price-history/`
- `/api/quotations/audit-logs/`

Automated tests also verify the PDF endpoint is staff-only.

Automated tests also cover staff-only import endpoints:
- `/api/quotations/inquiries/parse_text/`
- `/api/quotations/inquiries/parse_file/`
- `/api/quotations/inquiries/create_imported/`

Automated tests also cover quotation settings:
- anonymous users are blocked from `/api/quotations/settings/`
- normal non-staff users receive `403`
- staff users receive `200`
- defaults are returned if no settings row exists
- settings updates persist
- invalid logo, signature, and stamp uploads are rejected
- existing PDF generation still works with saved settings

Automated tests also cover historical price backfill:
- anonymous and non-staff users cannot parse historical PDFs
- staff users can parse staged finalized quotation PDFs
- encrypted historical PDFs are rejected
- parser extracts Al Ameen quotation number/date/rows from a machine-generated table PDF
- commit requires reviewed linked rows and appends company price history
- committed historical quotations are hidden from normal quotation lists unless `include_historical=true`
- duplicate historical imports do not append duplicate price-history rows
- bulk actions are staff-only
- bulk create links exact normalized existing QuoteItems instead of duplicating them
- bulk create creates missing private QuoteItems only for selected rows
- bulk ready status validates required fields
- bulk skipped rows are excluded from commit

Local manual endpoint checks during stabilization:
- Anonymous requests to local quotation endpoints return `401`.
- Existing automated tests verify normal non-staff users are blocked with `403`.
- Staff users can list quotation endpoints with `200`.

Run:

```bash
cd backend
python manage.py test quotations --keepdb
```

Expected result: all quotation tests pass.

## Next Recommended Fixes Before Phase 2

- Have a staff user, ideally Dad, repeat the workflow with real-ish sample data and confirm labels/order of operations feel natural.
- Run a small batch of real old finalized quotation PDFs through `Historical Imports` and record which layouts/rows fail before expanding parser scope.
- Configure durable private storage for Railway before using historical source-file retention as an audit/archive feature.
- Continue tuning labels, empty states, and tab organization after real staff feedback.
- Consider simplifying the sub-tabs for daily use: keep `Dashboard`, `Companies`, `Quote Items`, `Inquiries`, and `Quotations` primary; tuck `Price History` and `Audit Logs` into contextual panels.
- Add a small branded/draft indication to generated PDFs if staff will download draft quotes.
- Decide whether future template customization should start with static PDF/image backgrounds overlaid by ReportLab. This is safer for Railway than DOCX-to-PDF conversion.
- Add frontend component tests or a Playwright smoke test once the UI flow is accepted.
- Resolve the existing non-quotation React hook warnings in `OrderManagement.js` and `ProductDetail.js` separately.
- Fix the existing admin route guard so a hard refresh on `/admin` waits for auth initialization before redirecting.

## How To Continue Development

Start by reading:
- `QUOTATION_MODULE.md`
- `TODO_QUOTATIONS.md`
- `backend/quotations/`
- `frontend/src/components/quotations/`

Run backend checks from `backend/`:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py test
python manage.py check
```

Run frontend build from `frontend/`:

```bash
npm run build
```

## Continuation Notes

Completed:
- Quotation module plan approved
- Documentation scaffold created
- Backend app, models, migration, permissions, serializers, services, viewsets, PDF generation, admin registrations, and tests
- `python manage.py test quotations --keepdb` passes
- React admin dashboard integration at `/admin`
- Frontend quotation API helper and daily workflow components
- Frontend quotation error details for failed actions/endpoints
- Local API target corrected for Phase 1 development testing
- `python manage.py makemigrations` reports no pending changes
- `python manage.py migrate` applied `quotations.0001_initial`
- `python manage.py test --keepdb` passes
- `python manage.py check` passes
- `npm run build` passes
- Browser smoke test confirms every quotation sub-tab opens against local Django without a generic error alert
- Final browser smoke test confirms product/admin behavior, full manual quotation workflow, optional product dropdown failure handling, and staff-only quotation/PDF security
- Final browser smoke test data and temporary users were cleaned from the database
- Phase 1 hardening implementation adds responsive Manual Inquiry lines, duplicate Create Quote prevention, idempotent inquiry-to-quote creation, status progress, `Save All Lines`, action loading states, and branded configurable PDFs
- Latest workflow/PDF hardening browser verification passed, including overflow fix, duplicate-click prevention, Save All Lines, finalization, protected PDF download, and staff-only API/PDF access
- Latest hardening smoke-test data and temporary users were cleaned from the database
- Safe inquiry import implementation adds parser modules, metadata migration, staff-only parse/save endpoints, reviewed import UI, and backend tests for text/Excel/PDF paths
- Latest safe import browser verification passed for pasted text, Excel, digitally generated PDF, no-text PDF warning, save imported inquiry, and create quotation from saved imported inquiry
- Latest import smoke-test data and temporary files were cleaned from the database/filesystem
- Quotation Settings implementation adds singleton `QuotationSettings`, staff-only settings API, logo/signature/stamp validation and upload, Settings tab, and PDF branding driven from saved settings with environment fallbacks
- Production-grade importer hardening adds robust header detection, title/repeated-header skipping, serial stripping, quantity/unit splitting, `.xlsb/.xls` parser support through `python-calamine`, PyMuPDF PDF classification, private source-file refs, and OCR provider abstractions
- `python manage.py test quotations --keepdb -v 2` passed 41 tests after importer hardening
- `python manage.py migrate` applied `quotations.0006_inquiry_source_file_ref_inquiry_source_file_size`
- `python manage.py check` passed after importer hardening
- `npm run build` passed after importer hardening with only pre-existing non-quotation hook dependency warnings
- Browser/API verification confirmed the FIRST AID workbook parse result through the staff-only parse endpoint and confirmed the updated import UI is reachable in `/admin -> Quotations -> Inquiries`
- Historical finalized quotation backfill adds `Quotations -> Historical Imports`, `HistoricalPriceImport`, `HistoricalPriceImportLine`, staff-only PDF parse/preview/commit APIs, private source-file refs, and a commit service that appends reviewed non-duplicate rows into `CompanyPriceHistory`
- Historical backfill tests passed for staff-only parse access, encrypted PDF rejection, Al Ameen PDF table extraction, private source refs, commit into price history, hidden historical quotation records, and duplicate prevention
- `python manage.py makemigrations quotations` created `quotations.0007_historicalpriceimport_historicalpriceimportline_and_more`
- `python manage.py migrate` applied `quotations.0007_historicalpriceimport_historicalpriceimportline_and_more`
- `python manage.py check` passed after historical backfill implementation
- `python manage.py test quotations --keepdb` passed 47 tests after historical backfill implementation
- `npm run build` passed after historical backfill implementation with the existing non-quotation hook dependency warnings and Browserslist data-age notice
- Real sample parser check against `ANCIENT BUILDERS CONSTN  21052026.pdf` detected quotation `QUOTATION-26052101`, date `2026-05-21`, 19 rows, no parser warnings, and totals `551.25 / 17.15 / 568.40`
- After private source-ref resolver hardening, `python manage.py check` passed and `python manage.py test quotations.tests.InquiryImportTests quotations.tests.HistoricalPriceImportTests --keepdb` passed 18 tests
- Inline company creation QoL added through `CompanySelectWithCreate` and wired into inquiry import, manual inquiry, direct quotation creation, historical imports, and price-history filtering
- `python manage.py check` passed after the inline company creation update
- `npm run build` passed after the inline company creation update with the existing non-quotation hook dependency warnings and Browserslist data-age notice
- Historical import bulk review UX added with checkbox selection, filters/search, bulk create/link QuoteItems, bulk skip/status updates, row action menu, row highlighting, and sticky commit bar
- Imported inquiry preview rows now support checkbox selection and bulk delete
- `python manage.py test quotations --keepdb` passed 51 tests after the bulk review update
- `python manage.py check` passed after the bulk review update
- `npm run build` passed after the bulk review update with the existing non-quotation hook dependency warnings and Browserslist data-age notice
- Browser verification reached `/admin -> Quotations -> Historical Imports`, selected the sample import, confirmed the bulk toolbar/sticky commit bar/action menu rendered, and confirmed `Select Visible` selected 19 visible rows
- Historical import review header was polished into a two-card layout with a document preview card and import details card
- Suggested company handling now uses a banner/action, and inline company creation opens inside the Company section with the suggested name prefilled
- `python manage.py check` passed after the historical import header polish
- `python manage.py test quotations --keepdb` passed 51 tests after the historical import header polish
- `npm run build` passed after the historical import header polish with the existing non-quotation hook dependency warnings and Browserslist data-age notice
- Browser verification confirmed the polished historical import header, inline company creation form, no horizontal overflow, and sticky commit bar presence
- Historical import company creation UX was refined so `+ New Company` opens the collapsed form, `Create Company` submits it, selected companies show a badge, and the document preview card stretches with the import details card
- Browser verification captured form-closed, form-open, and company-created/selected states at normal zoom with no horizontal overflow
- Optional AI-assisted import parsing cleanup added behind `QuotationSettings` toggles, backend provider abstraction, strict JSON validation, DB cache/log tables, staff-only AI endpoints, inquiry/historical candidate review panels, and settings UI controls
- `python manage.py check` passed after AI-assisted parsing
- `python manage.py test quotations --keepdb` passed 69 tests after AI-assisted parsing on a clean temporary SQLite database
- `npm run build` passed after AI-assisted parsing with only pre-existing non-quotation hook dependency warnings and the existing Browserslist notice

Partially completed:
- Native file-picker automation was not available in the Browser plugin, so the actual `.xlsx` upload parse was verified with a staff JWT against the real DRF endpoint rather than through the browser file chooser
- Historical import inline company creation was browser-verified up to opening the inline create form with the suggested company name prefilled. Creating and saving a real new company should still be done by staff during manual acceptance with a non-temporary company.
- AI-assisted parsing has automated backend/build verification only in this session. A manual browser pass should still verify the `AI Clean Parse`, `AI Clean Rows`, and settings toggles with a configured provider key.

Next:
- Have staff try `Quotations -> Inquiries -> Import Inquiry` with real company files and confirm the compact review table labels feel clear
- Have staff click-test inline company creation in all company selection spots and confirm the search/create wording feels natural
- Have staff try `Quotations -> Historical Imports` with several real old finalized quotation PDFs. The parser is intentionally tuned for Al Ameen quotation tables first; reviewed failures should drive the next parser rule updates.
- Configure `OPENAI_API_KEY` and enable AI Parsing in `Quotations -> Settings`, then manually test pasted messy text and a visually tabular PDF with `AI Clean Parse`
- Confirm the Railway private storage plan before relying on historical source PDFs being retained long term
- Have Dad/staff manually repeat create/edit/finalize/PDF workflow in `/admin -> Quotations` with real-ish sample data
- Add real company/item data
- Continue with Phase 2 only after Phase 1 is accepted

Warnings:
- Do not add quotation models to `backend/api/models.py`
- Do not create public quotation routes
- Do not store sensitive quotation PDFs in public Cloudinary URLs
- Do not expose uploaded inquiry or historical source files through Cloudinary public URLs; use private storage refs only
- Railway production needs a durable private storage plan before relying on long-term source-file retention
- Do not add DOCX-to-PDF conversion unless Railway deployment has an explicit, supported conversion path
- Do not implement Gmail API, pgvector, OCR, broad automatic document processing, reporting, or background workers in this cleanup scope
- AI parsing and AI learning suggestions are review-only. They must not create Products, aliases, Companies, price history, quotations, or pricing decisions automatically.
- No AI calls happen unless the environment has a provider key and staff enable AI Parsing in `Quotations -> Settings`.
- Do not break product catalog, cart, checkout, orders, admin product management, admin order management, or JWT auth flows
- Current frontend build still has pre-existing hook dependency warnings in `OrderManagement.js` and `ProductDetail.js`; quotation-specific build warnings were fixed
