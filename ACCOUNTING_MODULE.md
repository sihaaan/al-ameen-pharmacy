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
- Support Classic and Professional customer-facing statement PDF styles.
- Split POS `Bill No.` values into `Invoice No.` and `LPO / Reference No.` while retaining the raw bill value internally.

V1 does **not** send emails.

## Permissions

Accounting is separate from quotation access.

- Superusers can access Accounting.
- Staff users in the `Accounting` Django group can access Accounting.
- Staff users with `accounting.view_accounting_module` can access Accounting.
- Normal staff without Accounting access cannot see the tab or use the API.
- Anonymous users are blocked.
- Django Admin user edit pages include an `Accounting access` checkbox. Checking it adds the user to the Accounting access path behind the scenes; unchecking it removes only Accounting-specific access and leaves unrelated groups/permissions alone.
- Users still need `Staff status` to open the React Admin Dashboard. Superusers always have Accounting access regardless of the checkbox.

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
7. Save missing customer emails directly from the Due Customers table, or open the customer detail drawer for deeper review.
8. Save email/category/ignored status if needed.
9. Download an individual Classic or Professional statement PDF, or download the ZIP of due statements.
10. Manually attach statement files to emails outside the system.

## Parser Notes

The current POS CSV repeats report/header metadata in every row. For the provided export, the parser reads invoice data from the repeated header/data section:

`Code | Party | Place | Bill No. | Date | Amount | 0-30 | 30-60 | 60-90 | Over 90 | TOTAL | Days`

Customer matching priority:

1. Customer code.
2. Exact normalized customer name.
3. Conservative normalized name match only.

No aggressive fuzzy matching is used in V1.

Category workbook matching uses the same conservative approach. If the category workbook includes a customer code column, customer code is used before customer name. Category uploads can also be applied to an existing import after the outstanding file was already parsed. If a duplicate outstanding file is uploaded with a category workbook, no duplicate import is created, but the category workbook is applied to the existing import/customer profiles.

If `Days` is missing or invalid, the parser calculates days from invoice date to report date. If report date is missing, upload date is used with a warning.

The parser splits raw POS bill numbers for display:

- `570170-284750-0-` -> `Invoice No. 570170`, `LPO / Reference No. 284750-0`
- `320815--` -> `Invoice No. 320815`, blank `LPO / Reference No.`
- `571920-UA3IJ2-` -> `Invoice No. 571920`, `LPO / Reference No. UA3IJ2`

## Statement Output

Statement PDFs are generated on demand from database rows. Generated PDFs are not stored permanently in V1.

Two PDF styles are available:

- **Classic Statement PDF**: compact accounting-style output for practical ledger review.
- **Professional Statement PDF**: polished customer-facing statement using visual patterns from the quotation PDF, with cleaner header, customer info, ageing summary, totals, and payment reminder sections.

Both styles show customer-facing information only: branding/contact details, statement date, customer name/code, invoice rows, ageing totals, total outstanding, overdue amount, and payment reminder text. Internal-only fields such as parser warnings, `Email missing`, `Category Unknown`, ignored status, and system status are not printed on the customer statement.

ZIP downloads default to the Professional style and include due, non-ignored customers from the selected import. The UI also offers a Classic ZIP download.

Large imports are batched in V1. `ACCOUNTING_STATEMENT_ZIP_SYNC_LIMIT` (default `75`) is used as the batch size. If an import has more due customers than the batch size, `Download All Due` returns one protected ZIP containing smaller part ZIPs, each with up to that many statement PDFs. This keeps the accountant's workflow close to one-click while avoiding a single huge flat archive. Staff can still select visible rows for a smaller selected ZIP, and ignored customers are always excluded.

## Security And Storage

- Accounting APIs are under `/api/accounting/`.
- All Accounting APIs require Accounting permission.
- Uploaded source files are parsed and discarded.
- Generated statement PDFs and ZIPs are streamed through protected backend endpoints.
- Accounting data is not exposed through public product, cart, order, or quotation APIs.
- Source files are not stored permanently in V1. The system keeps filename, SHA-256 hash, parsed invoice rows, metadata, and warnings only.

## Deferred

- Automatic email sending.
- Gmail/SMTP integration.
- Reminder send logs and duplicate-send prevention.
- Scheduled monthly jobs.
- Excel statement output.
- Private long-term storage of uploaded source files.
- AI parsing or fuzzy customer matching.
- Accounting payment reconciliation.
- Delete/retest import workflow.
- Cached/background ZIP generation if full-import statement generation still feels too slow after batched ZIP output.

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

