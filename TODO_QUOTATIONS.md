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
- [x] Decouple Quote Items from slow optional public product dropdown loading
- [x] Fix local product list N+1 image query slowdown affecting admin/home performance
- [x] Prevent admin shell/tabs from blocking on full product loading
- [x] Browser-verify product/admin pages after product performance fixes
- [x] Browser-verify full manual quotation workflow
- [x] Browser-verify optional product dropdown failure does not block private quote item creation
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

## Phase 1 Follow-Ups Before Phase 2

- [ ] Have Dad/staff repeat the quotation workflow with real-ish sample data
- [ ] Fix the existing admin route guard so hard-refreshing `/admin` waits for auth initialization before redirecting
- [ ] Continue tuning labels/empty states after real staff feedback
- [ ] Consider moving `Price History` and `Audit Logs` into contextual/advanced areas if daily use feels too busy
- [ ] Add frontend smoke tests after the manual workflow is accepted
- [ ] Consider draft PDF watermarking if staff might accidentally send draft quotations
- [ ] Decide whether DOCX template customization should be implemented through LibreOffice, an external conversion service, or kept as a manual admin-only export workflow
- [ ] Tune import parser rules with real company LPO samples after staff review

## Phase 2 Ideas

- [ ] Company-specific item aliases
- [ ] Global item aliases
- [ ] Deterministic normalized matching
- [ ] Fuzzy matching
- [ ] Confirmed-match learning
- [ ] Optional `pg_trgm` support
- [ ] AI-assisted match suggestions
- [ ] Embeddings and pgvector evaluation
- [ ] OCR/scanned LPO parsing if a Railway-safe approach is chosen

## Phase 3 Ideas

- [ ] Gmail API import
- [ ] Gmail draft creation
- [ ] Background worker infrastructure
- [ ] Reporting and analytics
- [ ] More granular quotation roles and Django groups
- [ ] Richer PDF templates and branding
