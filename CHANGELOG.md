# Changelog

## Unreleased

### Added
- Started Phase 1 quotation module implementation for the existing admin dashboard.
- Added dedicated quotation module documentation and future-work tracking.
- Added backend `quotations` app with staff-only APIs, workflow services, audit logs, PDF generation, and tests.
- Added React admin dashboard `Quotations` tab with companies, quote items, inquiries, quotation editor, price history, and audit log views.
- Verified quotation migrations, backend tests/checks, and frontend production build.
- Completed final Phase 1 browser smoke verification for existing product/admin pages and the full manual quotation workflow.

### Fixed
- Corrected local frontend API targeting for quotation development so `/admin -> Quotations` calls the local Django API instead of undeployed Railway quotation routes.
- Replaced generic quotation error alerts with inline details showing action, endpoint, HTTP status, and backend response detail.
- Allowed Quote Items to load independently while the optional public product dropdown is still loading or unavailable.
- Fixed product list N+1 image queries that made local `/api/products/` very slow against remote Neon.
- Added a lightweight product summary endpoint for admin dashboard stats so the admin shell is not blocked by full product loading.

### Verified
- Home products, product card images, product quick view, product detail, and product gallery still work after the product performance fix.
- Admin dashboard shell renders immediately and Products/Orders tabs still open.
- `/api/products/?compact=true&limit=200` returns compact `id`/`name` rows, and `/api/products/summary/` returns only product-count summary data.
- Staff can complete company, contact, quote item, inquiry, quotation, finalize, PDF, price-history, and revision workflow inside `/admin -> Quotations`.
- Anonymous and non-staff users are blocked from quotation APIs and PDFs; staff users are allowed.
