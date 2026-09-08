# Company pricing and catalogue identity

The quotation editor fills an untouched blank selling price from the customer's latest eligible accepted price, falling back to its latest eligible quoted price. `QUOTATION_COMPANY_PRICE_AUTOFILL_ENABLED=1` enables this independently of progressive loading. The environment default is off for staged rollout; setting it back to `0` disables the new editor flow without removing saved provenance or corrections.

## Staff workflow

- Select the customer and confirm the product. Equivalent reordered descriptions can resolve to a unique existing identity; ambiguous variants and duplicate IDs require selection.
- The blue clock inside a price field identifies a historical recommendation. The grey pencil identifies a manual price. The amber warning identifies a price requiring review. Open the icon for the source quotation, date, amount, unit and historical quantity. Keyboard users can open it with Enter and close it with Escape.
- Edit any suggested price. Saving records the original suggestion and entered amount, including edits made before the first save. Saving does not record customer acceptance. Finalisation adds quoted history; the existing outcome workflow records accepted prices.
- “Previous price is outdated” retires recommendations at or before the time of that saved decision in the same company/product/unit/currency context. It does not delete history, affect another company, or fall back to an even older accepted price.
- “Wrong product” invalidates the link and derived price. A manually edited price stays flagged until the employee explicitly checks it against the corrected product. Finalisation is blocked until that review is resolved. Confirmed corrections use the existing company-alias workflow; they do not retrain an AI model or ban a product globally.

Only eligible history for the same currency and equivalent unit spelling is reused. There is no currency conversion or assumed box/piece conversion. Cancelled and revised quotations, conflicting historical variants, provisional identities and the current quotation are excluded. Customer-stated prices extracted from Gmail remain evidence, not selling prices.

## Catalogue workflow

Product creation checks existing identities first. The standard product form and quotation bulk-creation flow request an AI naming preview before their save operation, outside the quotation transaction lock. AI may reorder or format supported wording and rank retrieved IDs; unsupported attribute changes and invented IDs are rejected. A provider failure retains the original wording and deterministic matching.

New products are provisional across the shared backend creation service. Each ambiguous bulk row needs its own acknowledgement. Exact duplicate-ID and identifier conflicts cannot be bypassed by “create anyway.” Provisional items remain manually quotable but cannot become trusted automatic matches or historical price recommendations until reviewed.

**Quotations → Products / Items → Catalogue identity review** lists provisional records and duplicate candidates, with historical usage counts and missing-link/acceptance counts. The owner can request an AI naming proposal, keep a distinct identity or explicitly consolidate equivalent identities under a verified master. Consolidation preserves old quotation line IDs, product links, wording, and historical amounts. Future recommendations discover the approved master's linked histories; they still enforce company, currency and unit boundaries. It never moves prices between companies.

The review is incremental. It does not automatically merge legacy products or companies, repair unlinked historical rows, infer unrecorded acceptances, or promise error-free recognition. Unsupported spellings remain reviewable candidates. Multiple ingredient strengths are treated conservatively because changing which ingredient owns a strength changes the product.

Pricing decisions appear in the private quotation audit log. Detailed feedback and recommendation retirements are also read-only in Django admin. Identity-changing catalogue edits return a product to provisional review; consolidated identities cannot silently be changed to a different variant.

## Deployment and performance

Migrations: API `0004` and `0005`, quotations `0045`. New non-null fields retain database defaults so the preceding application version can continue inserting rows during a rolling deploy. Use the existing Railway pre-deploy migration runner and direct migration connection.

Admin route and tab modules now load on demand. Quotation deep links no longer request the product summary and full retail-order list for the unused overview. The production build's initial main bundle fell from approximately 263 KB to 111 KB gzipped; including their additional chunks, the quotation list needs approximately 127 KB (52% less) and the editor 156 KB (41% less). The comparison also rebuilds the same source with eager imports to isolate the code-splitting effect. This is a payload measurement, not an end-to-end page-load timing claim.

Products / Items displays 50 catalogue rows per page. Search still covers the full loaded catalogue, including products outside the visible page, and returns to page one when changed. This bounds rendering work independently of the catalogue size; it does not reduce the catalogue API response size.

Regression coverage includes accepted-first/fallback selection, customer/unit/currency isolation, source persistence, edits before first save, late-response protection, manual-price preservation, wrong-product recovery, retirement of old recommendations, owner permissions, provisional creation, reviewed consolidation and rejection of unsupported AI attributes.
