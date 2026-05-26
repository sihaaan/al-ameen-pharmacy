# Accounting TODO

## Before Production Use

- [ ] Create an `Accounting` Django group in Django admin.
- [ ] Assign the Accounting permissions to the group.
- [ ] Add the accountant as a staff user and assign the group.
- [ ] Upload the latest monthly POS outstanding export.
- [ ] Upload the latest customer category workbook.
- [ ] Verify totals for several known customers against the old POS software.
- [ ] Enter missing customer emails.
- [ ] Download a ZIP and manually inspect several generated PDFs.

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

