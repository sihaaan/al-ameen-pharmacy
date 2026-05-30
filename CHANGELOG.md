# Changelog

## Unreleased

### Added
- Added a simple `Accounting access` checkbox to Django User admin so superusers can grant/revoke Accounting module access without manually managing groups and permissions.
- Added protected `Admin Dashboard -> Accounting` overdue statement workflow for accounting staff to upload monthly POS agewise outstanding exports, review due customers, persist customer emails/categories, and download statement PDFs or a ZIP of due statements without sending emails.
- Added Accounting statement PDF output with split `Invoice No.` and `LPO / Reference No.` columns for POS bill references.
- Added Accounting category workbook re-apply support so category mappings can be applied to an existing import, including duplicate outstanding uploads that include a category workbook.
- Added inline email editing, a customer detail drawer, ageing filters, and sorting to the Accounting Due Customers workflow.
- Added selected-customer Accounting ZIP downloads and automatic part-ZIP batching for large full-import statement downloads.
- Added Accounting statement invoice date range filtering that carries into customer summaries, detail rows, PDFs, and ZIP-generated statements.
- Started Phase 1 quotation module implementation for the existing admin dashboard.
- Added dedicated quotation module documentation and future-work tracking.
- Added backend `quotations` app with staff-only APIs, workflow services, audit logs, PDF generation, and tests.
- Added React admin dashboard `Quotations` tab with companies, product-backed items, inquiries, quotation editor, price history, and audit log views.
- Verified quotation migrations, backend tests/checks, and frontend production build.
- Completed final Phase 1 browser smoke verification for existing product/admin pages and the full manual quotation workflow.
- Added configurable quotation PDF branding settings for pharmacy name, Arabic name, address, phone, email, TRN/license, logo, terms, validity, and payment terms.
- Added a clearer quotation editor workflow with status progress, disabled invalid actions, saved/unsaved line indicators, and `Save All Lines`.
- Added staff-only inquiry import previews for pasted text, `.xlsx` files, and digitally generated `.pdf` files.
- Added safe parser modules for deterministic text rules, `openpyxl` workbook parsing, and `pypdf`/`pdfplumber` PDF parsing.
- Added imported inquiry metadata fields for source type, filename, MIME type, SHA-256, parse method, and parse metadata.
- Added production-grade importer hardening with robust Excel header scoring, title/repeated-header skipping, serial-number stripping, quantity/unit splitting, `.xlsb`/`.xls` support via `python-calamine`, PDF classification with PyMuPDF, private inquiry source-file refs, and OCR provider abstractions.
- Added importer tests for the `FIRST AID MATERIAL LOG.xlsx` pattern so title rows and serial numbers are not imported as items.
- Added reviewed imported-inquiry creation flow that saves inquiry lines without automatically creating a quotation.
- Added staff-editable `Quotations -> Settings` page for quotation PDF branding and business defaults.
- Added singleton `QuotationSettings` model with staff-only GET/PATCH API support.
- Added safe quotation logo upload validation for extension, MIME type, file size, and binary signature.
- Added PDF branding integration so ReportLab output uses saved settings with environment fallbacks.
- Added uploadable signature and stamp images for the quotation PDF approval area.
- Added quotation logo layout controls for full lockup logos, logo plus text, icon plus text, and text-only headers.
- Added staff-only remove/clear support for uploaded logo, signature, and stamp images.
- Added staff-only historical finalized quotation PDF imports for reviewed company price-history backfill.
- Added `HistoricalPriceImport` and `HistoricalPriceImportLine` models with private source-file refs, document metadata, parsed price rows, review status, and commit tracking.
- Added historical import APIs for parsing finalized quotation PDFs, reviewing/editing extracted rows, rendering private first-page previews, and committing approved rows into `CompanyPriceHistory`.
- Added a React `Quotations -> Historical Imports` tab for uploading old PDFs, selecting the real company, linking rows to Products, creating internal draft Products when needed, and committing price history.
- Added inline company creation wherever quotation staff select a company, including inquiry import, manual inquiry, direct quotation creation, historical imports, and price-history filtering.
- Added checkbox-based bulk review actions for historical import price rows: select visible, select unmatched, select needs-review, select ready, bulk create/link Products, bulk status changes, and bulk skip.
- Added backend bulk endpoints for historical imports with per-row results and duplicate-safe Product linking by deterministic matching.
- Added a compact historical import review table with status row highlighting, row action menu, filters, search, hidden raw source rows, and a sticky commit bar.
- Added a lighter checkbox/bulk-delete workflow for imported inquiry preview rows.
- Added Product-backed quotation item identity: internal quotation items are now draft Products, public items remain active Products, and deprecated QuoteItem compatibility fields are retained temporarily.
- Added `ProductAlias` for global and company-specific aliases, with company-specific aliases taking priority over global aliases and deterministic product matching.
- Added row-level alias remembering from inquiry, quotation, and historical import lines.
- Added safe delete/deactivate behavior for companies in the quotation module.
- Added historical finalized quotation duplicate detection so exact re-uploads and same-company quotation-number matches warn staff and avoid creating another staged import.
- Added optional AI-assisted import parsing cleanup for pasted inquiry text, inquiry file previews, and staged historical imports.
- Added `QuotationSettings` controls for `Enable AI Parsing`, `Enable Auto AI Cleanup`, and `Enable Vision AI for PDFs`, while provider keys and hard limits remain environment-managed.
- Added staff-only AI cleanup endpoints that return candidate rows, plus explicit apply behavior for historical imports so AI output cannot bypass review.
- Added AI parse cache/log models for provider/model/mode metadata, cache hits, text/page/image counts, success/failure, and usage data when returned by the provider.

### Fixed
- Corrected local frontend API targeting for quotation development so `/admin -> Quotations` calls the local Django API instead of undeployed Railway quotation routes.
- Replaced generic quotation error alerts with inline details showing action, endpoint, HTTP status, and backend response detail.
- Allowed Quote Items to load independently while the optional public product dropdown is still loading or unavailable.
- Fixed product list N+1 image queries that made local `/api/products/` very slow against remote Neon.
- Added a lightweight product summary endpoint for admin dashboard stats so the admin shell is not blocked by full product loading.
- Fixed Manual Inquiry requested-line layout overflow so the Delete button stays inside the card on desktop and smaller screens.
- Prevented duplicate quotation creation from repeated Create Quote clicks with frontend loading states and backend idempotency for inquiry-created quotations.
- Renamed PDF actions to clearer `Download PDF` / `Download Draft PDF` labels and added helper text that PDFs use latest saved quotation data.
- Fixed uploaded quotation logos disappearing from PDFs when storage returns a URL, such as Cloudinary, instead of a local filesystem path.
- Fixed duplicated PDF header branding by making `full_logo_only` avoid printing a separate large company name beside a full uploaded logo lockup.
- Improved signature/stamp placeholders so missing images show `Authorized Signature` and `Company Stamp` rather than fake-looking stamp text.
- Improved imported inquiry review UI with a summary banner, compact editable rows, hidden raw-source details, and sticky save actions.
- Hardened historical PDF table parsing so split `TOTAL` rows are not imported as price items.
- Avoided PostgreSQL `SELECT FOR UPDATE` nullable outer-join failures during historical import commits.
- Tightened the inline company creation layout so create/search/select controls stay grouped cleanly.
- Polished the historical import review header so document preview, import summary, company selection/creation, quotation details, and save action are organized into structured cards.
- Refined historical import company creation wording and layout balance: `+ New Company` opens the form, `Create Company` submits it, selected companies show a badge, and the document preview stretches with import details.
- Changed historical import bulk create/link to create or link Products instead of new QuoteItem catalog rows.
- Added safe archive-on-delete behavior for Products used by quotations, company price history, aliases, carts, or orders.
- Referenced quotation companies are deactivated instead of destructively deleted.
- Prevented accidental duplicate historical import staging from exact same-file uploads while keeping row-level commit idempotency as a second safety net.
- Improved quotation/LPO PDF and pasted-text parsing so real table columns like `Material Description`, `Req Quantity`, `unit`, `u price`, and `total` are mapped into item, quantity, unit, price, and total fields instead of being folded into the item name.
- Skipped common quotation PDF metadata/header/footer rows such as date, seller/buyer blocks, tender numbers, table headers, contact text, and totals from inquiry import item rows.
- Kept parse confidence separate from Product matching confidence so unmatched Products do not trigger AI cleanup or make clean parsed rows look weak.
- Improved Accounting statement PDFs so customer-facing documents omit internal-only fields like email-missing state, unknown category, parser warnings, ignored status, and system status.
- Improved Accounting category matching to prefer customer code when a category workbook provides one, then exact normalized customer name.
- Improved Accounting category upload messages to show matched, updated, already-up-to-date, and unmatched counts.
- Updated Accounting statement PDFs to a single polished ledger-style statement aligned with the quotation document hierarchy, including centered branding, right-side title block, cleaner footer/page numbering, and customer-facing debit/credit/balance columns.
- Cleaned Accounting POS bill-reference splitting so trailing/repeated dashes are removed from `LPO / Reference No.` and existing migrated rows are normalized.
- Removed PDC from customer-facing Accounting statements and made statement periods always show concrete invoice date ranges.
- Changed customer-facing Accounting PDFs from ageing-bucket reports to ledger-style Statements of Account with Debit, Credit, and cumulative Balance columns.

### Deferred
- Word-template-based PDF customization was investigated and deferred. Filling DOCX templates is reasonable with `python-docx` or `docxtpl`, but reliable DOCX-to-PDF conversion on Railway/Linux would require LibreOffice/headless conversion or an external service, which is outside the Phase 1 hardening scope.
- OCR/scanned PDF extraction remains deferred. The PDF importer only handles selectable text/tables and returns a clear warning when no selectable text is found.
- Full template upload/editor support remains deferred. The recommended future direction is static PDF/image background templates overlaid with ReportLab dynamic quotation data.
- Historical finalized quotation import currently supports text-based Al Ameen quotation PDFs first. Word/Excel historical finalized quotation backfill and broad third-party layouts remain deferred until real samples prove the needed parser shape.
- AI Product matching, embeddings, automatic alias creation, automatic Product creation, and automatic price-history writes remain deferred. AI parsing is cleanup-only.

### Verified
- Home products, product card images, product quick view, product detail, and product gallery still work after the product performance fix.
- Admin dashboard shell renders immediately and Products/Orders tabs still open.
- `/api/products/?compact=true&limit=200` returns compact `id`/`name` rows, and `/api/products/summary/` returns only product-count summary data.
- Staff can complete company, contact, product item, inquiry, quotation, finalize, PDF, price-history, and revision workflow inside `/admin -> Quotations`.
- Anonymous and non-staff users are blocked from quotation APIs and PDFs; staff users are allowed.
- Manual Inquiry overflow fix, duplicate Create Quote prevention, Save All Lines, branded PDF download, and staff-only PDF/API security were browser-verified after the hardening pass.
- Backend tests cover import permissions, invalid file types, upload size limits, Excel parsing, machine-generated PDF parsing, no-text PDF warnings, encrypted PDF rejection, imported inquiry creation, and manual inquiry regression.
- Browser verification passed for pasted text import, Excel import, digitally generated PDF import, no-text PDF warning, saving imported inquiry, and creating a quotation after save.
- Backend verification passed for robust import parsing, including the FIRST AID workbook fixture, multi-sheet Excel selection, invalid signature rejection, encrypted/no-text PDF handling, and staff-only import endpoint permissions.
- Backend tests cover quotation settings permissions, defaults, update, invalid logo/stamp rejection, signature/stamp upload, image clearing permissions, logo layout PDF rendering, missing signature/stamp placeholders, invalid colors, and PDF generation with saved branding images.
- Backend tests cover historical import staff-only access, encrypted PDF rejection, deterministic price-row parsing, private source refs, commit into price history, hidden historical quotation records, and duplicate import protection.
- Backend tests cover historical import bulk permissions, duplicate-safe Product creation/linking, ready-status validation, bulk skip behavior, and commit exclusion of skipped rows.
- Browser verification confirmed re-uploading the same historical quotation PDF warns without auto-opening the previous import, `View previous import` opens it on demand, and duplicate price-history rows are not added after commit.
- Browser verification confirmed the historical import review header has aligned preview/details cards, suggested-company banner, inline company creation, no horizontal overflow, and the sticky commit bar remains available.
- Browser verification captured historical import review states with the company form closed, open, and created/selected.
- Backend tests cover AI settings serialization, disabled/unavailable behavior, mocked text cleanup, mocked PDF vision cleanup, invalid AI output rejection, auto-cleanup gating, historical candidate apply, and no Product/alias/price-history/quotation side effects.
- `python manage.py check` and `python manage.py test quotations --keepdb` passed after AI-assisted parsing changes; `npm run build` passed with existing non-quotation warnings.
