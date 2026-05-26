# Accounting Module

## Business Problem

The pharmacy's existing POS/accounts software exports one large monthly agewise outstanding file. The accountant currently has to open each customer separately, generate a statement, attach it to an email, and repeat the process manually.

The Accounting module prepares those monthly overdue statement files inside the existing admin dashboard.

## V1 Scope

- Protected `Admin Dashboard -> Accounting` section.
- Upload monthly agewise outstanding CSV/XLSX exports.
- Optionally upload the customer category workbook.
- Parse and discard uploaded source files after extracting rows, hash, metadata, and warnings.
- Group invoices by accounting customer.
- Persist customer category, email, ignored status, and notes.
- Identify customers due by invoice `Days > 30` or balances in `30-60`, `60-90`, or `Over 90`.
- Generate one protected statement PDF per due customer.
- Generate a ZIP of all due, non-ignored statement PDFs.
- Show read-only email preview text and attachment filename.

V1 does **not** send emails.

## Permissions

Accounting is separate from quotation access.

- Superusers can access Accounting.
- Staff users in the `Accounting` Django group can access Accounting.
- Staff users with `accounting.view_accounting_module` can access Accounting.
- Normal staff without Accounting access cannot see the tab or use the API.
- Anonymous users are blocked.

Custom permissions:

- `view_accounting_module`
- `upload_accounting_statement`
- `generate_accounting_statement`
- `edit_accounting_customer`
- `download_accounting_statement`

## Workflow

1. Log in as a staff user with Accounting access.
2. Open `Admin Dashboard -> Accounting`.
3. Upload the monthly outstanding export.
4. Optionally upload/update the category workbook.
5. Review parsed row count, customer count, due customer count, and warnings.
6. Filter due customers, missing emails, categories, ignored customers, or all rows.
7. Open a customer detail.
8. Save email/category/ignored status if needed.
9. Download an individual statement PDF or the ZIP of due statements.
10. Manually attach statement files to emails outside the system.

## Parser Notes

The current POS CSV repeats report/header metadata in every row. For the provided export, the parser reads invoice data from the repeated header/data section:

`Code | Party | Place | Bill No. | Date | Amount | 0-30 | 30-60 | 60-90 | Over 90 | TOTAL | Days`

Customer matching priority:

1. Customer code.
2. Exact normalized customer name.
3. Conservative normalized name match only.

No aggressive fuzzy matching is used in V1.

If `Days` is missing or invalid, the parser calculates days from invoice date to report date. If report date is missing, upload date is used with a warning.

## Statement Output

Statement PDFs are generated on demand from database rows. Generated PDFs are not stored permanently in V1.

ZIP downloads include due, non-ignored customers from the selected import.

## Security And Storage

- Accounting APIs are under `/api/accounting/`.
- All Accounting APIs require Accounting permission.
- Uploaded source files are parsed and discarded.
- Generated statement PDFs and ZIPs are streamed through protected backend endpoints.
- Accounting data is not exposed through public product, cart, order, or quotation APIs.

## Deferred

- Automatic email sending.
- Gmail/SMTP integration.
- Reminder send logs and duplicate-send prevention.
- Scheduled monthly jobs.
- Excel statement output.
- Private long-term storage of uploaded source files.
- AI parsing or fuzzy customer matching.
- Accounting payment reconciliation.

## Continuation Notes

Run checks after changes:

```bash
cd backend
python manage.py check
python manage.py test

cd ../frontend
npm run build
```

Do not add email sending in V1. The email preview is informational only.

