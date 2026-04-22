"""
Management command: import_brochure_images
==========================================
Extracts images from the Al Ameen brochure PDF and assigns them to products.
Images are saved via Django's storage backend (Cloudinary in production).

Usage:
    python manage.py import_brochure_images
    python manage.py import_brochure_images --pdf /path/to/brochure.pdf
    python manage.py import_brochure_images --dry-run
    python manage.py import_brochure_images --force   # re-assign even if images exist
"""

import io
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image as PILImage

from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from api.models import Product, ProductImage


# ---------------------------------------------------------------------------
# PAGE → PRODUCT SLUG MAPPING
# (page_index_0based, image_index_on_page): product_slug
# Determined by reading the brochure PDF page by page.
# ---------------------------------------------------------------------------
PAGE_IMAGE_MAP = {
    # ── Page 2 (idx 1): First Aid kit photos ────────────────────────────────
    # Verified by visual inspection of extracted images
    (1, 0): "first-aid-kit-metal-box",       # white metal box "First Aid Kit"
    (1, 1): "first-aid-kit-backpack",         # kit contents spread — backpack kit
    (1, 2): "first-aid-kit-wall-cabinet",     # glass/aluminum wall cabinet
    (1, 3): "first-aid-kit-plastic-box",      # red plastic box open
    (1, 4): "first-aid-kit-plastic-box",      # plastic box variant
    (1, 5): "first-aid-kit-wall-cabinet",     # wall cabinet variant
    (1, 6): "first-aid-kit-backpack",         # green backpack
    (1, 7): "first-aid-kit-plastic-box",      # plastic box small
    (1, 8): "first-aid-kit-wall-cabinet",     # white wall cabinet
    (1, 9): "first-aid-kit-backpack",         # red backpack bag
    (1, 10): "first-aid-kit-plastic-box",     # two plastic boxes
    (1, 11): "first-aid-kit-wall-cabinet",    # large white wall cabinet
    (1, 12): "first-aid-kit-waist-pack",      # waist/fanny pack
    (1, 13): "first-aid-kit-metal-box",       # aluminium case

    # ── Page 3 (idx 2): First Aid items ─────────────────────────────────────
    # p3_i0 = composite grid image (all labeled items) — use for sterile plasters
    # p3_i1, p3_i2 = medicine/pill photos (not product images — skip)
    # p3_i3..p3_i12 = individual product images
    (2, 0): "sterile-plasters",              # composite grid — first product shown
    (2, 1): None,                            # medicine bottles (not a product image)
    (2, 2): None,                            # pills (not a product image)
    (2, 3): "sterile-bandages",              # bandage rolls
    (2, 4): "first-aid-kit-waist-pack",      # Alsco waist pack kit
    (2, 5): "gauze-products",                # gauze swabs
    (2, 6): "scissors",                      # straight scissors
    (2, 7): "scissors",                      # bandage scissors
    (2, 8): "scissors-forceps-set",          # scissors + forceps set (3 pieces)
    (2, 9): "forceps",                       # forceps/tweezers set
    (2, 10): "bvm-resuscitator",             # BVM resuscitator
    (2, 11): "bvm-resuscitator",             # BVM resuscitator (another style)
    (2, 12): "alcohol-swab",                 # alcohol swab

    # ── Page 4 (idx 3): Medical Disposables + Ortho + Gynaecology ───────────
    # Verified by visual inspection
    (3, 0): "latex-gloves",                  # latex gloves on hand
    (3, 1): "nebulizer-mask-set",            # oxygen/nebulizer mask with tubing
    (3, 2): "anesthesia-mask",               # anesthesia induction mask
    (3, 3): "tongue-depressor-wooden",       # wooden tongue depressors
    (3, 4): "sterilization-reel-200m",       # sterilization reels (blue rolls)
    (3, 5): "gauze-products",                # autoclave indicator tape rolls
    (3, 6): "post-mortem-kit",               # post mortem kit (shroud, tags)
    (3, 7): "surgical-gown-sterile",         # surgeons in gowns
    (3, 8): "gauze-products",                # cotton gauze swabs bag
    (3, 9): "vinyl-gloves",                  # vinyl gloves being put on
    (3, 10): "cervical-collar",              # cervical collar worn by man
    (3, 11): "sam-splint",                   # SAM splint orange/blue roll
    (3, 12): "cervical-scraper",             # wooden sticks (cervical spatulas)
    (3, 13): "vaginal-speculum",             # vaginal speculum (clear plastic)
    (3, 14): "umbilical-cord-clamp",         # umbilical cord clamps (circular)
    (3, 15): "air-splint",                   # inflatable air splint on leg
    (3, 16): "finger-splint",                # finger splints box

    # ── Page 5 (idx 4): Airway + Plastic Products + Urology ─────────────────
    # NOTE: PDF image order differs from brochure visual layout.
    # Verified by visual inspection of every extracted image.
    (4, 0): "yankuer-set-with-handle",       # Yankuer suction tip with tubing
    (4, 1): "ryles-tube-ngt",                # Ryles/NGT tube (orange tip)
    (4, 2): "suction-catheter",              # suction catheters multi-colour
    (4, 3): "endotracheal-tube-cuffed",      # cuffed ET tubes (two curved)
    (4, 4): "endotracheal-tube-uncuffed",    # uncuffed ET tubes (straight set)
    (4, 5): "bed-pan-fracture",              # fracture bed pan (pink/beige flat)
    (4, 6): "sitz-bath",                     # sitz bath with bag attachment
    (4, 7): "wash-basin",                    # square wash basin (pink)
    (4, 8): "basin-emesis-kidney",           # kidney-shaped emesis basins
    (4, 9): "male-urinal",                   # male urinal bottle (white)
    (4, 10): "bed-pan-pontoon",              # pontoon bed pan (pink round)
    (4, 11): "eye-pads",                     # sterile eye pads in packets
    (4, 12): "post-mortem-kit",              # post mortem kit (second image)
    (4, 13): "ear-syringe",                  # rubber bulb ear syringe
    (4, 14): "cpr-mask",                     # pocket CPR mask with case
    (4, 15): "razer-medical-disp-2-sided",   # double-sided disposable razor
    (4, 16): "laryngoscope",                 # laryngoscope handle + blade set
    (4, 17): "closed-wound-suction-set",     # closed wound drain with reservoir
    (4, 18): "nasal-oxygen-cannula",         # nasal cannula adult + paediatric
    (4, 19): "guedal-airways",               # Guedel airways (4 colours)
    (4, 20): "extension-tube",               # oxygen extension tube
    (4, 21): "nelaton-catheter",             # nelaton catheters (multi-colour)
    (4, 22): "foley-catheter-2-way",         # Foley 2-way balloon catheter
    (4, 23): "urine-bag",                    # standard urine drainage bag
    (4, 24): "condom-catheter",              # condom catheters (3 pieces)
    (4, 25): "urine-bag",                    # urine bag (second image)
    (4, 26): "urine-leg-bag",                # urine leg bag with straps
    (4, 27): "irrigation-kit-with-bulb-syringe",  # irrigation kit with bulb syringe

    # ── Page 6 (idx 5): Medical Devices & Lab + Furniture ───────────────────
    # Verified by visual inspection
    (5, 0): "cover-slip",                    # cover glass boxes (24x24mm)
    (5, 1): "cotton-tip-applicator",         # cotton tip applicators (3 sticks)
    (5, 2): "holloware-set",                 # stainless steel holloware set
    (5, 3): "scissors-forceps-set",          # scissors & forceps (many pieces)
    (5, 4): "surgical-instruments-set",      # surgical instruments set (laid out)
    (5, 5): "wooden-sticks-applicator",      # wooden sticks (applicators)
    (5, 6): "pipettes-1ml-3ml",              # plastic transfer pipettes
    (5, 7): "blood-lancet",                  # blue safety lancets
    (5, 8): "pathology-container",           # round specimen containers
    (5, 9): "lab-disposables",               # lab specimen containers (various)
    (5, 10): "centrifuge-machine",           # benchtop centrifuge
    (5, 11): "examination-couch",            # blue padded examination couch
    (5, 12): "portable-couch-massage-bed",   # white portable folding couch
    (5, 13): "emergency-crash-cart",         # blue/grey emergency crash cart
    (5, 14): None,                           # operating table — not in product list
    (5, 15): "hospital-bed",                 # hospital bed with side table
    (5, 16): "foot-stool",                   # two-step steel foot stool
    (5, 17): "revolving-stool",              # revolving stool on castors
    (5, 18): None,                           # instrument trolley — not in product list
    (5, 19): "ward-screen",                  # 4-panel blue ward screen

    # ── Page 7 (idx 6): Diagnostic equipment ────────────────────────────────
    # Verified by visual inspection
    (6, 0): "stethoscope",                   # dual-head stethoscope (green)
    (6, 1): "thermometer-ear",               # ear thermometer (tympanic)
    (6, 2): "iv-stand",                      # IV drip stand (4-hook)
    (6, 3): "exam-lamp",                     # exam lamp on floor stand
    (6, 4): "weight-scale",                  # clinical weight scales
    (6, 5): "eye-chart-box-with-light",      # illuminated Snellen eye chart
    (6, 6): "stretcher-folding",             # folding stretcher (orange)
    (6, 7): "stretcher-scoop",               # scoop stretcher (aluminium)
    (6, 8): "x-ray-viewer-double",           # single-panel X-ray viewer
    (6, 9): "thermometer-forehead",          # non-contact forehead thermometer
    (6, 10): "thermal-blanket",              # gold mylar thermal blanket
    (6, 11): "thermometer-armpit-flat",      # glass axillary thermometers (2)
    (6, 12): "thermometer-oral",             # glass oral thermometer (flat)
    (6, 13): "thermometer-digital",          # digital thermometer (blue tip)
    (6, 14): "bp-monitor-floor-type",        # desk sphygmomanometer
    (6, 15): "bp-monitor-floor-type",        # floor-type BP monitor on stand

    # ── Page 8 (idx 7): Emergency & Transport ───────────────────────────────
    # Verified by visual inspection
    (7, 0): "stretcher-automatic-loading",   # ambulance auto-loading stretcher
    (7, 1): "nebulizer-machine",             # compressor nebulizer machine
    (7, 2): "stretcher-basket",              # basket/Stokes stretcher (red)
    (7, 3): "stretcher-trolley",             # hospital stretcher trolley (orange)
    (7, 4): "underarm-crutches",             # underarm crutches set (5 sizes)
    (7, 5): "kendrick-extrication-splint",   # KED vest extrication device
    (7, 6): "wheel-chair",                   # folding steel wheelchair (blue)
    (7, 7): "walker-wheels",                 # wheeled walker frame
    (7, 8): "spine-board",                   # orange plastic spine board
    (7, 9): "ortho-pillow",                  # orthopaedic contour pillow
    (7, 10): "head-immobilizer",             # head immobiliser blocks (red)
    (7, 11): None,                           # no p8_i11 in extracted images
    (7, 12): "vacuum-mattress",              # vacuum mattress (narrow blue/red)
}

# Skip images smaller than this (logos, rule lines, watermarks)
MIN_WIDTH = 80
MIN_HEIGHT = 80


class Command(BaseCommand):
    help = "Extract images from the Al Ameen brochure PDF and assign them to products."

    def add_arguments(self, parser):
        parser.add_argument(
            "--pdf",
            default=None,
            help="Path to the brochure PDF. Defaults to project root auto-detect.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview extraction without saving anything.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing images.",
        )

    def handle(self, *args, **options):
        pdf_path = options["pdf"]
        dry_run = options["dry_run"]
        force = options["force"]

        if not pdf_path:
            base = Path(__file__).resolve().parents[5]
            candidates = list(base.glob("*.pdf"))
            brochure = [p for p in candidates if "ameen" in p.name.lower() or "brochure" in p.name.lower()]
            if brochure:
                pdf_path = str(brochure[0])
            else:
                self.stderr.write(self.style.ERROR(
                    "PDF not found in project root. Use --pdf /path/to/file.pdf"
                ))
                return

        self.stdout.write(f"PDF: {pdf_path}")
        doc = fitz.open(pdf_path)
        self.stdout.write(f"Pages: {len(doc)}\n")

        stats = {"extracted": 0, "saved": 0, "skipped": 0, "unmatched": 0, "missing_product": 0}

        for page_num in range(len(doc)):
            page = doc[page_num]
            image_list = page.get_images(full=True)
            if not image_list:
                continue

            self.stdout.write(f"Page {page_num + 1}: {len(image_list)} raw images")
            page_img_index = 0

            for img_info in image_list:
                xref = img_info[0]
                try:
                    base_image = doc.extract_image(xref)
                    img_bytes = base_image["image"]
                    img_ext = base_image["ext"].lower()

                    # Validate dimensions
                    try:
                        pil_img = PILImage.open(io.BytesIO(img_bytes))
                        w, h = pil_img.size
                    except Exception:
                        page_img_index += 1
                        continue

                    if w < MIN_WIDTH or h < MIN_HEIGHT:
                        page_img_index += 1
                        continue

                    stats["extracted"] += 1
                    key = (page_num, page_img_index)
                    product_slug = PAGE_IMAGE_MAP.get(key)

                    status_str = product_slug or "(unmapped)"
                    self.stdout.write(f"  [{page_num},{page_img_index}] {w}x{h} -> {status_str}")

                    if product_slug is None:
                        stats["unmatched"] += 1
                        page_img_index += 1
                        continue

                    try:
                        product = Product.objects.get(slug=product_slug)
                    except Product.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"    No product: {product_slug}"))
                        stats["missing_product"] += 1
                        page_img_index += 1
                        continue

                    # Skip if already has images and not forcing
                    if not force and product.images.exists():
                        self.stdout.write(f"    Skipped (has images)")
                        stats["skipped"] += 1
                        page_img_index += 1
                        continue

                    if dry_run:
                        self.stdout.write(self.style.SUCCESS(
                            f"    [DRY RUN] Would save to '{product.name}'"
                        ))
                        page_img_index += 1
                        continue

                    # Convert to PNG for consistency
                    if img_ext not in ("png", "jpg", "jpeg"):
                        buf = io.BytesIO()
                        pil_img.convert("RGB").save(buf, format="PNG")
                        img_bytes = buf.getvalue()
                        img_ext = "png"

                    # Save via Django storage (goes to Cloudinary in production)
                    filename = f"{product_slug}_brochure_p{page_num}_{page_img_index}.{img_ext}"
                    is_primary = not product.images.filter(is_primary=True).exists()

                    product_image = ProductImage(
                        product=product,
                        alt_text=product.name,
                        is_primary=is_primary,
                        display_order=product.images.count(),
                        source_type="manufacturer",
                    )
                    product_image.image.save(filename, ContentFile(img_bytes), save=True)
                    stats["saved"] += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"    OK: saved '{product.name}' (primary={is_primary})"
                    ))

                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"  Error [{page_num},{page_img_index}]: {e}"))

                page_img_index += 1

        doc.close()
        self.stdout.write("\n" + "-" * 50)
        self.stdout.write(self.style.SUCCESS(
            f"Extracted: {stats['extracted']}\n"
            f"Saved:     {stats['saved']}\n"
            f"Skipped:   {stats['skipped']} (already had images)\n"
            f"Unmatched: {stats['unmatched']} (no mapping)\n"
            f"No product: {stats['missing_product']}\n"
        ))
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing saved."))
