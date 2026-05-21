# Changelog

## Unreleased

### Added
- Started Phase 1 quotation module implementation for the existing admin dashboard.
- Added dedicated quotation module documentation and future-work tracking.
- Added backend `quotations` app with staff-only APIs, workflow services, audit logs, PDF generation, and tests.
- Added React admin dashboard `Quotations` tab with companies, quote items, inquiries, quotation editor, price history, and audit log views.
- Verified quotation migrations, backend tests/checks, and frontend production build.
- Completed final Phase 1 browser smoke verification for existing product/admin pages and the full manual quotation workflow.
- Added configurable quotation PDF branding settings for pharmacy name, Arabic name, address, phone, email, TRN/license, logo, terms, validity, and payment terms.
- Added a clearer quotation editor workflow with status progress, disabled invalid actions, saved/unsaved line indicators, and `Save All Lines`.
- Added staff-only inquiry import previews for pasted text, `.xlsx` files, and digitally generated `.pdf` files.
- Added safe parser modules for deterministic text rules, `openpyxl` workbook parsing, and `pypdf`/`pdfplumber` PDF parsing.
- Added imported inquiry metadata fields for source type, filename, MIME type, SHA-256, parse method, and parse metadata.
- Added reviewed imported-inquiry creation flow that saves inquiry lines without automatically creating a quotation.

### Fixed
- Corrected local frontend API targeting for quotation development so `/admin -> Quotations` calls the local Django API instead of undeployed Railway quotation routes.
- Replaced generic quotation error alerts with inline details showing action, endpoint, HTTP status, and backend response detail.
- Allowed Quote Items to load independently while the optional public product dropdown is still loading or unavailable.
- Fixed product list N+1 image queries that made local `/api/products/` very slow against remote Neon.
- Added a lightweight product summary endpoint for admin dashboard stats so the admin shell is not blocked by full product loading.
- Fixed Manual Inquiry requested-line layout overflow so the Delete button stays inside the card on desktop and smaller screens.
- Prevented duplicate quotation creation from repeated Create Quote clicks with frontend loading states and backend idempotency for inquiry-created quotations.
- Renamed PDF actions to clearer `Download PDF` / `Download Draft PDF` labels and added helper text that PDFs use latest saved quotation data.

### Deferred
- Word-template-based PDF customization was investigated and deferred. Filling DOCX templates is reasonable with `python-docx` or `docxtpl`, but reliable DOCX-to-PDF conversion on Railway/Linux would require LibreOffice/headless conversion or an external service, which is outside the Phase 1 hardening scope.
- OCR/scanned PDF extraction remains deferred. The PDF importer only handles selectable text/tables and returns a clear warning when no selectable text is found.

### Verified
- Home products, product card images, product quick view, product detail, and product gallery still work after the product performance fix.
- Admin dashboard shell renders immediately and Products/Orders tabs still open.
- `/api/products/?compact=true&limit=200` returns compact `id`/`name` rows, and `/api/products/summary/` returns only product-count summary data.
- Staff can complete company, contact, quote item, inquiry, quotation, finalize, PDF, price-history, and revision workflow inside `/admin -> Quotations`.
- Anonymous and non-staff users are blocked from quotation APIs and PDFs; staff users are allowed.
- Manual Inquiry overflow fix, duplicate Create Quote prevention, Save All Lines, branded PDF download, and staff-only PDF/API security were browser-verified after the hardening pass.
- Backend tests cover import permissions, invalid file types, upload size limits, Excel parsing, machine-generated PDF parsing, no-text PDF warnings, encrypted PDF rejection, imported inquiry creation, and manual inquiry regression.
- Browser verification passed for pasted text import, Excel import, digitally generated PDF import, no-text PDF warning, saving imported inquiry, and creating a quotation after save.
