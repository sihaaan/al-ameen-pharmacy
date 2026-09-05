# Orders and delivery notes

The staff workspace is under **Admin Dashboard → Quotations → Orders & Delivery Notes**.

## Record acceptance and delivery

1. Review the quotation outcome or its LPO and record the accepted line quantities. An uploaded LPO alone is not proof of accepted quantities.
2. Open the accepted order and choose **Create delivery note for remaining items**. Review quantities and fill the delivery address, LPO number, invoice reference, contact details and instructions. A note can cover only part of the order.
3. Save a draft or choose **Save & issue delivery note**. Issuing reserves these quantities against that order, preventing another note from dispatching them twice. Drafts do not reserve order quantities.
4. Download the PDF. It uses the quotation's configured logo, colours and typography, with no prices, VAT amounts or invoice totals. It includes the customer details, references, quantity/unit table and a customer signature/stamp area. Draft and cancelled PDFs are labelled. Downloading is read-only.
5. After delivery, enter the receiver, receipt date and quantities actually received. Add a filing reference for the signed note and any shortages or damage. Receipt information is entered by staff; this does not capture an electronic customer signature or upload proof of delivery.

If 10 units were issued and the customer received 6, the remaining 4 become available for a new delivery note. The first receipt and its shortage remain in history.

### Approve an uploaded LPO and prepare a DO

In **Review Outcome → PO Assistant**, upload a PDF or Excel LPO, or paste its text. **Parse PO** uses AI cleanup when available and stages suggested matches for review. Parsing does not save acceptance or issue a delivery note. Review which quoted items were ordered and edit their accepted quantities, prices and outcomes.

Choose **Approve & prepare DO** to save those decisions and open a delivery draft containing the quantities still available to deliver. Edit this delivery's quantities or remove items before issuing it. For example, approve an LPO for 10 boxes and issue a DO for 6: the accepted order remains 10, with 4 left for a later delivery. Approval saves first; if preparing the draft fails, the UI explains that acceptance was saved and lets staff retry.

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

## Corrections and access

Issued notes are locked. Cancel an incorrect note with a reason and create its replacement. Cancellation keeps the note, recorded receipt and audit history, and removes its quantities from order progress. Use this to correct a recording error; it is not a stock-return or credit-note workflow.

The backend checks quantities again when issuing, under the same quotation lock used for acceptance changes. It rejects over-issue, unrelated quotation lines, duplicate line selections, invalid quantities, future receipt dates and edits to issued notes. Acceptance cannot be reduced below quantities already issued or received. Active delivery notes must be cancelled before cancelling their quotation. Source quotations and lines are protected from deletion. Staff access is required for PDFs and all delivery endpoints; Django admin exposes delivery history read-only.

## API and rollout

Apply migration **quotations.0044_delivery_notes** to the intended deployment database and deploy the matching backend and frontend together. This change does not backfill historical acceptance or delivery data and does not send any email.

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
