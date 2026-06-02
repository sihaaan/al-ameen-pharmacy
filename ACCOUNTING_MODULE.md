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
- Generate one protected ledger-style statement PDF or Excel workbook per due customer.
- Generate a ZIP of all due, non-ignored statement PDFs or Excel workbooks.
- Show read-only email preview text and attachment filename.
- Support one customer-facing Statement of Account PDF style.
- Split POS `Bill No.` values into `Invoice No.` and `LPO / Reference No.` while retaining the raw bill value internally.
- Display Accounting dates in UAE business format (`dd/mm/yyyy`) while keeping database dates as real date fields.

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
3. Review the import history. No previous import is opened automatically.
4. Click `Open / Review` on an existing import, or upload the monthly outstanding export.
5. Optionally upload/update the category workbook.
6. Review parsed row count, customer count, due customer count, missing-email count, and warnings.
7. Filter due customers, missing emails, categories, ignored customers, or all rows.
8. Save missing customer emails directly from the Due Customers table, or open the customer detail drawer for deeper review.
9. Apply an optional invoice date range if the customer statement should exclude older or newer invoices.
10. Save email/category/ignored status if needed.
11. Download an individual Statement of Account PDF/Excel workbook, or download the ZIP of due statements.
12. Manually attach statement files to emails outside the system.

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

Parser safeguards reject unsupported files, malformed Excel workbooks, CSV rows with unexpected column explosions, and files above the configured row/size limits with clear validation errors instead of server errors. The newer POS export format `Agewise Outstanding from ... to ...` is supported and uses the final `to` date as the report date. To keep large imports fast on Neon, invoice rows store compact source trace metadata rather than repeating the POS report header/footer cells for every row.

The parser splits raw POS bill numbers for display:

- `570170-284750-0-` -> `Invoice No. 570170`, `LPO / Reference No. 284750-0`
- `320815--` -> `Invoice No. 320815`, blank `LPO / Reference No.`
- `571920-UA3IJ2-` -> `Invoice No. 571920`, `LPO / Reference No. UA3IJ2`

## Statement Output

Statement PDFs and Excel workbooks are generated on demand from database rows. Generated files are not stored permanently in V1.

The customer-facing PDF and Excel workbook are ledger-style Statements of Account. The main table intentionally does not print ageing buckets. Instead it shows:

- Invoice Date
- Doc Type
- Invoice No.
- LPO / Reference No.
- Debit
- Credit
- Balance
- Days

Rows are sorted by invoice date ascending, then invoice/reference order. `Balance` is a cumulative running balance calculated as previous balance + debit - credit. Positive values appear as Debit. Negative values appear as Credit using the absolute value. `Days` is calculated from the invoice date to the statement/report date when available, otherwise to the generated statement date.

The summary section shows Total Debit, Total Credit, Net Value / Total Outstanding, and Final Balance. Internal-only fields such as parser warnings, `Email missing`, `Category Unknown`, ignored status, and system status are not printed on the customer statement.

The Accounting dashboard still keeps ageing buckets internally for due calculations, filters, and review. Ageing data is not removed from the database or internal UI.

If a statement date range is applied, the dashboard customer list, detail drawer, individual PDF/Excel workbook, and ZIP-generated statements use only invoice rows in that date range. The statement prints the selected statement period clearly in `dd/mm/yyyy` format. If no date range is applied, the statement period is calculated from the minimum and maximum invoice dates included for that customer.

Excel workbooks are formatted for accountant review with a simple branded header, title block, customer/account information, ledger table, formatted currency/date columns, totals block, reminder note, worksheet autofilter, freeze panes, and print-friendly landscape page setup. The workbook intentionally avoids structured Excel Table objects because Microsoft Excel can repair files that combine table metadata with worksheet-level filters.

ZIP downloads include due, non-ignored customers from the selected import.

Large imports are batched in V1. `ACCOUNTING_STATEMENT_ZIP_SYNC_LIMIT` (default `75`) is used as the batch size. If an import has more due customers than the batch size, `Download All Due` returns one protected ZIP containing smaller part ZIPs, each with up to that many statement PDFs or Excel workbooks. This keeps the accountant's workflow close to one-click while avoiding a single huge flat archive. Staff can still select visible rows for a smaller selected ZIP, and ignored customers are always excluded.

## Security And Storage

- Accounting APIs are under `/api/accounting/`.
- All Accounting APIs require Accounting permission.
- Uploaded source files are parsed and discarded.
- Generated statement PDFs and ZIPs are streamed through protected backend endpoints.
- Generated statement Excel workbooks and Excel ZIPs are streamed through protected backend endpoints.
- Accounting data is not exposed through public product, cart, order, or quotation APIs.
- Source files are not stored permanently in V1. The system keeps filename, SHA-256 hash, parsed invoice rows, metadata, and warnings only.

## Deferred

- Automatic email sending.
- Gmail/SMTP integration.
- Reminder send logs and duplicate-send prevention.
- Async/background import with progress tracking if monthly CSV upload time grows beyond the current synchronous request window.
- Scheduled monthly jobs.
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

