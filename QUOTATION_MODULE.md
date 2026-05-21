# Quotation Module

## Business Problem

Al Ameen Pharmacy receives company inquiries and LPOs for pharmacy items. Staff currently use separate, unstructured Excel files per company to look up previous prices, match requested item names, copy prices into quotations, and manually update history after sending.

The quotation module brings that workflow into the existing pharmacy website admin dashboard so staff can manage companies, inquiries, quote items, company-specific pricing history, generated quotations, PDFs, and finalization without Git, Docker, localhost, or a second application.

## Phase 1 MVP Scope

Phase 1 is limited to the existing Django/DRF backend and existing React admin dashboard at `/admin`.

Included:
- Separate Django app named `quotations`
- Staff-only DRF APIs
- Company and contact management
- Private quote item catalog with optional link to public `Product`
- Manual inquiry creation and inquiry lines
- Quotation creation and line editing
- Company-specific previous price history
- Finalization workflow
- Audit logs for important quotation actions
- Protected PDF generation streamed from the backend
- React admin dashboard tab: `Admin Dashboard -> Quotations`

Not included in Phase 1:
- AI matching
- OpenAI embeddings
- pgvector migrations
- Item aliases
- Gmail API import
- Gmail draft creation
- Background workers
- Document parsing
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

Create a quote item:
- Go to `Quotations -> Quote Items`
- Fill `Quote Item Name`
- Optionally link a public product; leave blank for private/internal quote items
- Optionally fill internal code, brand, generic name, strength, dosage form, pack size, and unit
- Click `Save Quote Item`

Create an inquiry:
- Go to `Quotations -> Inquiries`
- Select a company and optional contact
- Enter a subject and source text
- Add one or more requested lines
- For each line, either select a quote item to confirm the match or leave it unmatched
- Click `Create Inquiry`

Create a quotation from an inquiry:
- In `Quotations -> Inquiries`, click `Create Quote` beside the inquiry
- The quotation editor opens
- Confirm each line is matched to a quote item
- Enter quantity, unit, unit price, and VAT rate as needed
- Click `Save` on edited lines

Create a quotation directly:
- Go to `Quotations -> Quotations`
- Select a company and optional contact
- Click `Create Quotation`
- Add lines manually in the quotation editor

Finalize a quotation:
- In the quotation editor, confirm every non-ignored line has:
  - matched quote item
  - `Confirmed` match status
  - positive quantity
  - positive unit price
- Click `Finalize`
- Confirm the quote status changes to `Finalized`
- Confirm the editor shows the locked-quote notice

Download the PDF:
- Open a quotation in the quotation editor
- Click `PDF`
- Confirm a PDF downloads with the quotation number as the filename

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
- [ ] Inquiry line can be matched to a quote item
- [ ] Quotation can be created from inquiry
- [ ] Quotation can be created directly from a company
- [ ] Quotation line can be added, edited, and deleted while editable
- [ ] Previous price history can be viewed from the editor or Price History tab
- [ ] Invalid quote cannot be finalized
- [ ] Valid quote can be finalized
- [ ] Finalized quote is visibly locked
- [ ] Finalized quote cannot be edited
- [ ] Finalized quote appends price history once
- [ ] Finalized/sent quote can create a new draft revision
- [ ] PDF downloads for staff
- [ ] Anonymous users cannot access quotation API endpoints
- [ ] Normal customer users cannot access quotation API endpoints
- [ ] Staff users can access quotation API endpoints

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

`QuoteItem` is intentionally separate from the public ecommerce `Product` model. It has an optional nullable link to `Product` so private/internal/customer-specific quotation items do not need to become storefront products.

## API Endpoints Added

Implemented base path: `/api/quotations/`

Implemented endpoints:
- `/companies/`
- `/contacts/`
- `/items/`
- `/inquiries/`
- `/inquiry-lines/`
- `/quotes/`
- `/quote-lines/`
- `/price-history/`
- `/audit-logs/`

Implemented custom actions:
- `POST /inquiries/{id}/create_quote/`
- `GET /companies/{id}/price_history/`
- `POST /quotes/{id}/submit_review/`
- `POST /quotes/{id}/approve/`
- `POST /quotes/{id}/finalize/`
- `POST /quotes/{id}/mark_sent/`
- `POST /quotes/{id}/revise/`
- `POST /quotes/{id}/cancel/`
- `GET /quotes/{id}/pdf/`

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
- `AuditLogPanel`
- `QuotationErrorNotice`

The module appears only inside the existing React admin dashboard at `/admin` as a top-level `Quotations` tab beside `Overview`, `Products`, and `Orders`.

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
- `python manage.py test quotations --keepdb` passes 8 tests.
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
- `python manage.py test quotations --keepdb` passed 8 tests.
- `python manage.py check` passed with no issues.
- `npm run build` passed with the existing non-quotation warnings in `OrderManagement.js` and `ProductDetail.js`, plus the existing Browserslist data-age notice.

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

Phase 1 PDFs are generated on demand from stored quotation snapshot fields and streamed from staff-only API endpoints.

Sensitive quotation PDFs are not stored in public Cloudinary URLs in Phase 1.

## Phase 2/3 Roadmap

Phase 2:
- Company-specific aliases
- Global aliases
- Deterministic item matching
- Fuzzy matching
- Confirmed-match learning
- Optional `pg_trgm`
- AI-assisted matching and reranking
- Embeddings and pgvector evaluation
- Document parsing for uploaded inquiries/LPOs

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
- Item matching is manual in Phase 1.
- Existing public product/cart/order/auth flows must not be coupled to quotation internals.
- Quotation UI errors now use inline diagnostic panels. Existing non-quotation admin/product/order screens still use their older alert patterns.
- Directly loading `/admin` in a fresh browser session can briefly redirect to `/login` before the existing auth context finishes reading local storage. Navigating to `/admin` from inside the app works. This is an existing admin route-guard timing issue, not quotation-specific, but it is worth cleaning up later.
- Required fields use browser validation but do not yet show visible `Required` markers in every form.
- The quotation module currently has seven sub-tabs. This is complete for MVP, but daily use may be easier if `Price History` and `Audit Logs` move into contextual panels or an advanced area.
- `QuotationEditor`, `InquiryManager`, and backend `models.py`/`views.py` are acceptable for Phase 1 but are natural split points if the module grows.
- Staff can download PDFs for draft quotations. This is useful for review, but the business may later prefer watermarking draft PDFs or allowing PDFs only after approval/finalization.
- Frontend build still reports pre-existing hook dependency warnings in `OrderManagement.js` and `ProductDetail.js`.
- Automated browser editing of quotation lines was most reliable when saving imported inquiry lines from bottom to top. The UI passed the final smoke test, but before Phase 2 it would be wise to harden line editing with clearer per-row saved states or a `Save All Lines` action.

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
- Harden the quotation line editor with clearer per-row saved states or a `Save All Lines` action if manual testing shows any confusion.
- Add visible required-field markers and a clearer first-use empty state for each subsection.
- Consider simplifying the sub-tabs for daily use: keep `Dashboard`, `Companies`, `Quote Items`, `Inquiries`, and `Quotations` primary; tuck `Price History` and `Audit Logs` into contextual panels.
- Add a small branded/draft indication to generated PDFs if staff will download draft quotes.
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

Partially completed:
- None

Next:
- Have Dad/staff manually repeat create/edit/finalize/PDF workflow in `/admin -> Quotations` with real-ish sample data
- Consider line-editor UX hardening before Phase 2 if row-by-row saves feel easy to miss
- Add real company/item data
- Continue with Phase 2 only after Phase 1 is accepted

Warnings:
- Do not add quotation models to `backend/api/models.py`
- Do not create public quotation routes
- Do not store sensitive quotation PDFs in public Cloudinary URLs
- Do not implement AI, Gmail API, pgvector, aliases, document parsing, reporting, or background workers in Phase 1
- Do not break product catalog, cart, checkout, orders, admin product management, admin order management, or JWT auth flows
- Current frontend build still has pre-existing hook dependency warnings in `OrderManagement.js` and `ProductDetail.js`; quotation-specific build warnings were fixed
