# Orders and delivery notes

The staff workspace is under **Admin Dashboard → Quotations → Orders & Delivery Notes**.

## Record acceptance and delivery

1. Review the quotation outcome or its LPO and record the accepted line quantities. An uploaded LPO alone is not proof of accepted quantities.
2. Open the accepted order and choose **Create delivery note for remaining items**. Accepted items and quantities fill automatically. Detected shipping/contact details from the confirmed LPO carry into the draft; review these and enter any missing invoice reference or instructions. A note can cover only part of the order.
3. Save a draft or choose **Save & issue delivery note**. Issuing reserves these quantities against that order, preventing another note from dispatching them twice. Drafts do not reserve order quantities.
4. Download the PDF. It uses the quotation's configured logo, colours and typography, with no prices, VAT amounts or invoice totals. It includes the customer details, references, quantity/unit table and a customer signature/stamp area. Draft and cancelled PDFs are labelled. Downloading is read-only.
5. After delivery, enter the receiver, receipt date and quantities actually received. Add a filing reference for the signed note and any shortages or damage. Receipt information is entered by staff; this does not capture an electronic customer signature or upload proof of delivery.

If 10 units were issued and the customer received 6, the remaining 4 become available for a new delivery note. The first receipt and its shortage remain in history.

### Approve an uploaded LPO and prepare a DO

The **Review Outcome** action opens **Review customer order**, arranged in three steps: choose the LPO, review the items, and approve the order. Upload a PDF or Excel LPO, paste its text, expand the Gmail suggestions, or choose **Enter order manually**. **Read LPO & review items** uses AI cleanup when available and stages suggested matches for review. Parsing does not save acceptance or issue a delivery note. Search the item list or filter **Needs review**, then edit the accepted quantities, prices, decisions and optional item notes. Filtering does not discard edits.

Choose **Approve & prepare DO** to save those decisions and open a delivery draft containing the quantities still available to deliver. Edit this delivery's quantities or remove items before issuing it. For example, approve an LPO for 10 boxes and issue a DO for 6: the accepted order remains 10, with 4 left for a later delivery. Approval saves first; if preparing the draft fails, the UI explains that acceptance was saved and lets staff retry.

**Save review** saves decisions without preparing a delivery note. Follow-up fields and saved results are in an optional expandable section. Review links retain the selected quotation and review page when refreshed; refresh does not save unsaved edits.

This approval is the staff member's confirmation that the customer accepted the selected items. Creating or downloading an unapproved draft alone does not establish acceptance. Issuing a DO means those accepted quantities are being dispatched; only confirming receipt completes delivery. New or replacement products that were not quoted need review in the quotation workflow before they can be included in a linked DO; the AI does not rewrite the sent quotation.

## Understand the order data

| Status | Meaning |
| --- | --- |
| Accepted | Accepted quantities exist and have not been issued or received. |
| Awaiting receipt | At least one issued note awaits receipt confirmation. |
| Partially delivered | Some accepted quantities have confirmed receipts; others remain outstanding. |
| Completed | Every accepted line has been fully received. This means delivery completion, not invoice payment. |
| Acceptance needs review | A won/partial quotation, confirmed LPO or draft note exists without usable accepted line quantities. |
| Cancelled | The quotation was cancelled. |

The order detail shows accepted, received, awaiting-receipt and remaining quantities per item, with its unit. It never sums unlike units into an order quantity. Status counts, search, filters and pagination are available in the workspace. Counts reflect the current search across all matching orders.

Past orders become reliable delivery data only after their acceptance and receipts are recorded. The system does not infer fulfilment from a sent quotation, an uploaded LPO, a generated PDF or an invoice reference. E-commerce checkout orders retain their existing separate workflow.

**New standalone delivery note** supports customers whose order was handled outside the quotation workflow. These documents have their own draft/issued/receipt status, but do not invent an accepted quotation or appear in quotation-order completion totals.

### Fill a standalone DO from an LPO

Choose **New standalone delivery note → Fill from an LPO**, upload a PDF/Excel file or image, or paste the purchase order text, then choose **Read LPO**. AI reads item rows and purchaser/shipping/contact fields when enabled. The shared LPO parser removes page footers and totals from item rows, separates product codes from item names, and retains size, strength, pack, quantity and unit information. If AI is unavailable or changes reliable extracted quantities, the original extraction remains available with a warning.

Review the detected customer, address, requested date and items, then choose **Fill delivery note** (or **Replace items with this LPO** for an existing draft). Only an unambiguous company name selects the customer automatically. All delivery fields and standalone item rows remain editable. Missing quantities stay blank for staff to correct, and the requested date does not replace the actual delivery date. A staged preview never creates or issues a note; discarding it preserves existing edits.

Save the reviewed draft or issue it when ready. Its source filename, private evidence reference, original parsed rows and staff attribution are retained separately from edited delivery values. Linked quotation DOs continue through **Manage order → Read LPO & review items → Approve & prepare DO** so the approved quantities and existing deliveries remain authoritative.

## Corrections and access

Issued notes are locked. Cancel an incorrect note with a reason and create its replacement. Cancellation keeps the note, recorded receipt and audit history, and removes its quantities from order progress. Use this to correct a recording error; it is not a stock-return or credit-note workflow.

The backend checks quantities again when issuing, under the same quotation lock used for acceptance changes. It rejects over-issue, unrelated quotation lines, duplicate line selections, invalid quantities, future receipt dates and edits to issued notes. Acceptance cannot be reduced below quantities already issued or received. Active delivery notes must be cancelled before cancelling their quotation. Source quotations and lines are protected from deletion. Staff access is required for PDFs and all delivery endpoints; Django admin exposes delivery history read-only.

## API and rollout

Apply migration **quotations.0044_delivery_notes** to the intended deployment database and deploy the matching backend and frontend together. This change does not backfill historical acceptance or delivery data and does not send any email.

The LPO autofill workflow additionally requires **quotations.0048_deliverynote_lpo_import**. POST file or text, optionally use_ai, to **/api/quotations/delivery-notes/parse_lpo/**. It returns a signed, staff-bound preview valid for 24 hours without creating a note. Save its import_token as lpo_import_token with reviewed standalone fields/lines; invalid or expired tokens are rejected. The private source reference is not included in the note's public serializer.

| Endpoint under `/api/quotations/` | Purpose |
| --- | --- |
| `delivery-orders/` and `delivery-orders/{id}/` | Read order progress; filter list by `search`, `status`, and `page`. |
| `delivery-notes/` | List or create a draft; list filters: `search`, `status`, `quotation`, `page`. |
| `delivery-notes/{id}/` | Read a note or PATCH a draft and its full line list. |
| `delivery-notes/{id}/issue/` | POST to issue the saved draft. |
| `delivery-notes/{id}/confirm-receipt/` | POST receiver, date, optional reference/notes, and actual line quantities. |
| `delivery-notes/{id}/cancel/` | POST a required cancellation reason. |
| `delivery-notes/{id}/pdf/` | Read-only, authenticated PDF download. |

For partial receipts, supply `lines: [{id, received_quantity}, ...]` covering every note line exactly once. Without `lines`, the confirmation records all issued quantities as received. The UI always submits the reviewed quantities. Repeated issue/receipt requests for an already transitioned note return its existing state without recording a duplicate transition.
