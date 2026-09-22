# Tax invoice generator

The **Tax Invoices** tab in the quotation workspace creates independent invoices from a quotation upload, pasted quotation text, or manually entered items. It does not turn proformas into tax invoices or change quotation, delivery or accounting records.

## Staff workflow

1. Open Tax Invoices and choose **New tax invoice**.
2. Scan a PDF, photo or spreadsheet quotation, then apply the preview; or select a customer and add items manually.
3. Confirm the billing name/address, customer TRN when registered, invoice/supply dates and optional quotation/LPO references. Customer details are copied into the draft and may be corrected there without changing the company record.
4. Review quantity, unit, unit price excluding VAT, whole-line discount in AED and VAT (0% or 5%). The parser leaves unrecognized prices/rates blank. Discounts must be checked against the source; the parser does not infer discounts from totals.
5. **Save draft**, check the totals or preview PDF, confirm review and **Issue tax invoice**.
6. Reopen an invoice from the list to download its original PDF. Opening automatically scrolls to the editor; the bottom action bar remains reachable on long invoices.

## Data and issuance

- Migration `0052_tax_invoices` creates separate invoice, line and numbering tables. Apply it before deploying the new frontend.
- API base: `/api/quotations/tax-invoices/`. Endpoints require the existing quotation staff permission.
- `POST parse-document/` returns a preview only; it reuses the existing file/text/AI extraction and LPO metadata filtering. Source evidence stays in the existing private import storage. The signed source token is bound to the parsing staff member and expires after 24 hours; a saved draft retains its source metadata.
- Draft writes include all lines and run in one transaction. `expected_revision` prevents stale windows from overwriting edits. PATCH header edits are supported; supplied line arrays replace all lines.
- All values are AED. Each quantity × unit price is rounded to two decimals using decimal half-up rounding, then the line discount is subtracted, and VAT is rounded to two decimals. Invoice totals sum these line amounts. Missing prices/VAT can be saved, but block issuance.
- Issuing requires the customer billing address and the pharmacy's configured name, address and valid 15-digit TRN. The pharmacy TRN prints regardless of the optional quotation TRN display setting.
- `POST {id}/issue/` locks the draft and the yearly numbering counter. Numbers use `TI-YYYY-000001`; retries on an already issued invoice return the same record. PDF failure rolls back numbering and issuance.
- Issued records cannot be edited or deleted through the API. Their PDF bytes and supplier details are retained, so future branding/settings/customer changes cannot alter the original. `GET {id}/pdf/` never issues a draft.
- No email is sent. Payment collection, ledger posting, foreign currency conversion and credit notes are outside this generator's scope. This is a PDF invoice feature, not an e-invoicing integration.

## Verification

Backend coverage includes decimal totals, incomplete/invalid lines, stale revisions, staff permissions, actual quotation PDF extraction, signed source ownership, read-only PDF previews, issuance retries, numbering rollback, fixed issued PDFs and list filtering. Existing delivery/parser regression tests are retained. Frontend tests cover manual entry, scan preview/application, company detail loading, review gating, issued locking, open/scroll and error recovery.

Local QA uses a separate SQLite database with sample customers. Four-item and 55-item PDFs were rendered and checked for readable white headers, item/price alignment, repeated headings and page breaks. Production configuration and records are not altered by the QA setup.
