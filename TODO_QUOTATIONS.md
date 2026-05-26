# Quotation Module TODO

## Phase 1 Completion Checklist

- [x] Create separate Django app: `quotations`
- [x] Add quotation models and migrations
- [x] Add staff-only quotation permissions
- [x] Add serializers and viewsets
- [x] Add service-layer workflow logic
- [x] Add audit logging
- [x] Add protected PDF generation
- [x] Add backend tests for permissions and workflow rules
- [x] Add React `Quotations` tab inside existing `/admin` dashboard
- [x] Add quotation admin components
- [x] Add frontend API helper
- [x] Add inline quotation error panels with endpoint/status/backend detail
- [x] Verify every quotation sub-tab opens against the local Django API
- [x] Decouple quotation item creation from slow optional public product dropdown loading
- [x] Fix local product list N+1 image query slowdown affecting admin/home performance
- [x] Prevent admin shell/tabs from blocking on full product loading
- [x] Browser-verify product/admin pages after product performance fixes
- [x] Browser-verify full manual quotation workflow
- [x] Browser-verify optional product dropdown failure does not block internal item creation
- [x] Browser-verify anonymous, non-staff, and staff quotation/PDF access
- [x] Clean up temporary browser smoke-test records and users
- [x] Run migrations, backend tests/checks, and frontend build
- [x] Update `QUOTATION_MODULE.md` with final state
- [x] Fix Manual Inquiry requested-line overflow so Delete stays inside the form
- [x] Prevent duplicate quotation creation from repeated Create Quote clicks
- [x] Add idempotent backend handling for creating a quotation from an inquiry
- [x] Add clearer quotation status progress and action button loading states
- [x] Add quotation line saved/unsaved indicators and `Save All Lines`
- [x] Improve PDF branding with configurable pharmacy header, metadata, totals, terms, and signature/stamp area
- [x] Browser-verify workflow/PDF hardening and clean temporary smoke-test data
- [x] Add safe inquiry import preview for pasted text, `.xlsx`, and digitally generated `.pdf`
- [x] Add reviewed imported-inquiry save flow without automatic quotation creation
- [x] Add parser/security tests for text, Excel, PDF, invalid uploads, no-text PDFs, encrypted PDFs, and manual inquiry regression
- [x] Browser-verify pasted text, Excel, PDF, no-text PDF warning, save imported inquiry, and create quote after save
- [x] Harden Excel import header detection, title-row skipping, repeated-header skipping, serial stripping, and quantity/unit splitting
- [x] Add `.xlsb`/`.xls` parser support through `python-calamine`
- [x] Add private source-file refs for successful uploaded inquiry parses
- [x] Add OCR provider abstraction without enabling heavy OCR runtime dependencies
- [x] Add FIRST AID workbook parser regression tests
- [x] Browser/API-verify compact import area is reachable and FIRST AID workbook parses correctly through staff-only upload endpoint
- [x] Add staff-editable `Quotations -> Settings` page for PDF branding and defaults
- [x] Add singleton `QuotationSettings` model and staff-only settings API
- [x] Validate quotation logo uploads by extension, MIME type, file size, and binary signature
- [x] Drive ReportLab PDF branding from saved quotation settings with environment fallbacks
- [x] Fix PDF image rendering for storage-backed logo URLs such as Cloudinary
- [x] Add uploadable signature and stamp images for the PDF approval area
- [x] Add logo layout controls so full brand lockups do not duplicate company-name text
- [x] Add remove actions for uploaded logo/signature/stamp images
- [x] Improve signature/stamp placeholder behavior in generated PDFs
- [x] Add staff-only historical finalized quotation PDF import for price-history backfill
- [x] Store historical import source PDFs in private quotation storage refs, not public URLs
- [x] Add reviewed historical import commit flow that appends company price history only after staff links rows to Products
- [x] Hide historical backfill quotations from normal quotation lists unless explicitly requested
- [x] Add backend tests for historical import permissions, parsing, commit, duplicate prevention, and encrypted PDF rejection
- [x] Add inline searchable company creation to quotation forms so staff do not need to leave the current workflow to add a missing company
- [x] Add checkbox selection and bulk actions to historical import price review rows
- [x] Add duplicate-safe bulk Product create/link behavior for historical import rows
- [x] Add compact historical import review table filters, search, row action menu, row highlighting, and sticky commit bar
- [x] Add checkbox/bulk delete to imported inquiry preview rows
- [x] Polish the historical import review header into separate document preview and import details cards
- [x] Move suggested-company handling into a proper banner/action and keep inline company creation inside the review screen
- [x] Refine historical import company creation wording and balance the document preview/details card height
- [x] Refactor quotation item identity around `Product` while keeping deprecated `QuoteItem` compatibility fields
- [x] Add global and company-specific `ProductAlias` deterministic matching
- [x] Keep draft/internal Products usable in quotations while hidden from public product lists
- [x] Add safe delete/archive behavior for quotation products that have business history
- [x] Add safe delete/deactivate behavior for companies that have quotation history
- [x] Add duplicate detection for historical finalized quotation re-uploads by source hash, same-company quotation number, and similar row/totals fingerprint
- [x] Add optional AI-assisted import parsing cleanup controlled from `Quotations -> Settings`
- [x] Add staff-only AI cleanup endpoints for inquiry previews and staged historical imports
- [x] Add AI candidate review/apply UI so deterministic rows are not replaced until staff approve
- [x] Keep AI parsing separate from Product matching and price-history commit decisions

## Phase 1 Follow-Ups Before Phase 2

- [ ] Have Dad/staff repeat the quotation workflow with real-ish sample data
- [ ] Have staff try inline company creation in inquiry import, manual inquiry, direct quotation creation, and historical imports
- [ ] Have staff try the historical import bulk review flow with a real 20+ row PDF and confirm the toolbar wording is natural
- [ ] Consider adding keyboard shortcuts for row selection/save only after staff confirm the bulk workflow is useful
- [ ] Try `Quotations -> Historical Imports` with a batch of real old finalized PDF quotations and tune parser rules from reviewed failures
- [ ] Decide whether staff need a guarded `Continue anyway` control for rare historical import duplicate false positives
- [ ] Decide where durable private storage should live on Railway before relying on long-term historical source-file retention
- [ ] Browser-verify quotation settings save/logo behavior on the deployed-like environment before merging
- [ ] Add a safe `Download Sample PDF` action to Quotation Settings if staff want a preview without opening an existing quotation
- [ ] Fix the existing admin route guard so hard-refreshing `/admin` waits for auth initialization before redirecting
- [ ] Continue tuning labels/empty states after real staff feedback
- [ ] Consider moving `Price History` and `Audit Logs` into contextual/advanced areas if daily use feels too busy
- [ ] Add frontend smoke tests after the manual workflow is accepted
- [ ] Consider draft PDF watermarking if staff might accidentally send draft quotations
- [ ] Prototype static PDF/image background templates with ReportLab overlays before considering DOCX-to-PDF conversion
- [ ] Tune import parser rules with real company LPO samples after staff review
- [ ] Configure production AI provider env vars and manually verify `AI Clean Parse` / `AI Clean Rows` with a real messy PDF before enabling auto cleanup
- [ ] Review AI parse logs/cost after a few staff trials and adjust page/text/image limits if needed
- [ ] Do one human browser pass with the native file picker because Browser automation could not attach a file to the upload input directly
- [ ] Configure durable private object storage/persistent private storage for Railway before relying on long-term inquiry/historical source-file retention
- [ ] Review PyMuPDF licensing/deployment implications before production launch if the app will be used beyond internal operations
- [ ] After production has run Product-backed quotation workflows safely, plan a later cleanup migration to remove deprecated `QuoteItem` fields/table.
- [ ] Add a small alias-management UI if staff want to review/edit aliases outside row-level "Remember Alias" actions.

## Phase 2 Ideas

- [ ] Company-specific item aliases
- [ ] Global item aliases
- [ ] Deterministic normalized matching
- [ ] Fuzzy matching
- [ ] Confirmed-match learning
- [ ] Optional `pg_trgm` support
- [ ] AI-assisted Product match suggestions
- [ ] Embeddings and pgvector evaluation
- [ ] OCR/scanned LPO parsing if a Railway-safe approach is chosen

## Phase 3 Ideas

- [ ] Gmail API import
- [ ] Gmail draft creation
- [ ] Background worker infrastructure
- [ ] Reporting and analytics
- [ ] More granular quotation roles and Django groups
- [ ] More PDF template styles and optional static background template upload
