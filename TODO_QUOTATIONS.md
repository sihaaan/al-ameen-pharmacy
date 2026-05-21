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

## Phase 1 Follow-Ups Before Phase 2

- [ ] Have Dad/staff repeat the quotation workflow with real-ish sample data
- [ ] Consider adding clearer quotation line save states or a `Save All Lines` action
- [ ] Fix the existing admin route guard so hard-refreshing `/admin` waits for auth initialization before redirecting
- [ ] Add visible required-field markers and clearer empty states
- [ ] Consider moving `Price History` and `Audit Logs` into contextual/advanced areas if daily use feels too busy
- [ ] Add frontend smoke tests after the manual workflow is accepted

## Phase 2 Ideas

- [ ] Company-specific item aliases
- [ ] Global item aliases
- [ ] Deterministic normalized matching
- [ ] Fuzzy matching
- [ ] Confirmed-match learning
- [ ] Optional `pg_trgm` support
- [ ] AI-assisted match suggestions
- [ ] Embeddings and pgvector evaluation
- [ ] LPO/inquiry document parsing

## Phase 3 Ideas

- [ ] Gmail API import
- [ ] Gmail draft creation
- [ ] Background worker infrastructure
- [ ] Reporting and analytics
- [ ] More granular quotation roles and Django groups
- [ ] Richer PDF templates and branding
