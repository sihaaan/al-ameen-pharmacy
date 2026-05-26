# Accounting TODO

## Before Production Use

- [ ] Create or edit the accountant user in Django admin.
- [ ] Ensure the accountant user has `Staff status`.
- [ ] Check `Accounting access` on the Django user edit page.
- [ ] Upload the latest monthly POS outstanding export.
- [ ] Upload the latest customer category workbook.
- [ ] Verify totals for several known customers against the old POS software.
- [ ] Enter missing customer emails.
- [ ] Download both Classic and Professional statement PDFs for several customers.
- [ ] For large imports, select a manageable batch of due customers and download a selected ZIP.
- [ ] Download a ZIP and manually inspect several generated PDFs.
- [ ] Mark internal branch/customers as ignored where statements are not required.

## Future Improvements

- [ ] Add email sending with explicit review and send confirmation.
- [ ] Store reminder send logs.
- [ ] Prevent duplicate reminder sends for the same import/customer.
- [ ] Add configurable overdue threshold.
- [ ] Add email template settings.
- [ ] Add Excel statement export if accountants request it.
- [ ] Add a private object storage option for source files if long-term retention becomes necessary.
- [ ] Add conservative customer alias mapping for category/customer name cleanup.
- [ ] Add dashboard export to CSV/XLSX.
- [ ] Add statement style setting if accountants settle on one default.
- [ ] Add import delete/retest workflow only after clear production data retention rules.
- [ ] Add cached/background full-import ZIP generation if accountants need one-click ZIPs for hundreds of customers.

