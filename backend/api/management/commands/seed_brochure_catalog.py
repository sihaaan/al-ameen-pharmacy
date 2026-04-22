"""
Management command: seed_brochure_catalog
==========================================
Seeds the product catalog with ONLY the products visible and named in the
Al Ameen Pharmacy brochure PDF. Every product here has a matching labeled
image in the brochure. No extras.

Usage:
    python manage.py seed_brochure_catalog          # Adds/updates only
    python manage.py seed_brochure_catalog --clear  # Wipes existing catalog first
"""

from django.core.management.base import BaseCommand
from django.utils.text import slugify
from api.models import Brand, Category, Product


# ---------------------------------------------------------------------------
# CATALOG DATA
# ---------------------------------------------------------------------------

BRANDS = [
    "Al Ameen Pharmacy",
    "Generic",
    "Alsco",
    "FirstAid",
    "Medline",
    "Cardinal Health",
    "Smiths Medical",
    "BD Medical",
    "Hollister",
    "DeRoyal",
    "Seward",
    "Welch Allyn",
    "Omron",
    "Seca",
    "Stryker",
    "Ferno",
    "Laerdal",
    "Drive Medical",
    "Invacare",
    "SOMO",
]

# (parent_name, [children])
CATEGORIES = [
    ("First Aid", [
        "First Aid Kits",
        "Wound Care & Dressings",
        "Bandages & Splints",
        "Eye & Ear Care",
        "Resuscitation",
    ]),
    ("Medical Disposables", [
        "Gloves",
        "Gowns & Drapes",
        "Sterilization",
        "General Disposables",
    ]),
    ("Orthopaedic", [
        "Collars & Slings",
        "Splints & Supports",
        "Mobility Aids",
        "Orthopaedic Pillows",
    ]),
    ("Gynaecology", [
        "Gynaecology Instruments",
        "Obstetrics",
    ]),
    ("Plastic Products", [
        "Patient Hygiene",
        "Basins & Bowls",
    ]),
    ("Anaesthesia & Airway", [
        "Airway Management",
        "Oxygen Therapy",
        "Suction & Drainage",
    ]),
    ("Urology", [
        "Catheters",
        "Urine Collection",
        "Irrigation",
    ]),
    ("Medical Devices & Lab", [
        "Laboratory Consumables",
        "Surgical Instruments",
        "Diagnostic Equipment",
    ]),
    ("Furniture & Equipment", [
        "Examination & Treatment",
        "Hospital Furniture",
        "Storage & Trolleys",
    ]),
    ("Emergency & Transport", [
        "Stretchers & Spineboards",
        "Immobilisation",
        "Nebulisers & Therapy",
        "Wheelchairs & Walking Aids",
    ]),
]

# (name, category_path, brand, short_description, pack_size, show_price)
# Every product here has a named, labeled image in the brochure.
PRODUCTS = [
    # ── FIRST AID KITS (Page 2) ────────────────────────────────────────────
    ("First Aid Kit – Plastic Box",   "First Aid > First Aid Kits", "FirstAid",
     "Comprehensive first aid kit in hard plastic carry case. Ideal for offices and workshops.",
     "Stocked kit", False),
    ("First Aid Kit – Metal Box",     "First Aid > First Aid Kits", "FirstAid",
     "Professional first aid kit in wall-mountable metal cabinet.",
     "Stocked kit", False),
    ("First Aid Kit – Wall Cabinet",  "First Aid > First Aid Kits", "Generic",
     "Lockable metal wall-mounted first aid cabinet for clinics and factories.",
     "Cabinet only / Stocked", False),
    ("First Aid Kit – Waist Pack",    "First Aid > First Aid Kits", "Alsco",
     "Compact first aid kit in lightweight waist/fanny pack for field use.",
     "Stocked kit", False),
    ("First Aid Kit – Backpack",      "First Aid > First Aid Kits", "FirstAid",
     "High-visibility first aid backpack with reflective strip, fully stocked.",
     "Stocked kit", False),

    # ── WOUND CARE & DRESSINGS (Page 3 grid) ──────────────────────────────
    ("Sterile Plasters",    "First Aid > Wound Care & Dressings", "Generic",
     "Hypoallergenic sterile adhesive plasters for minor cuts and abrasions.",
     "Box of 100", False),
    ("Antiseptic Wipes",    "First Aid > Wound Care & Dressings", "Generic",
     "Pre-moistened antiseptic wipes for wound cleaning and surface disinfection.",
     "Box of 100", False),
    ("Sterile Bandages",    "First Aid > Wound Care & Dressings", "Generic",
     "Sterile conforming bandages for wound dressing and support.",
     "Pack of 10", False),
    ("Burn Dressing",       "First Aid > Wound Care & Dressings", "Generic",
     "Hydrogel burn dressing that cools and soothes minor to moderate burns.",
     "Single use", False),
    ("Scissors",            "First Aid > Wound Care & Dressings", "Generic",
     "Stainless steel medical-grade bandage scissors with blunt tip.",
     "Single", False),
    ("Forceps",             "First Aid > Wound Care & Dressings", "Generic",
     "Stainless steel dressing forceps for sterile wound management.",
     "Single", False),
    ("Alcohol Swab",        "First Aid > Wound Care & Dressings", "Generic",
     "Isopropyl alcohol 70% swabs for skin disinfection before injections.",
     "Box of 100", False),

    # ── BANDAGES & SPLINTS (Page 3 grid) ──────────────────────────────────
    ("Triangular Bandages", "First Aid > Bandages & Splints", "Generic",
     "Multi-purpose triangular bandage for slings, bandaging and immobilisation.",
     "Pack of 6", False),
    ("Safety Pins",         "First Aid > Bandages & Splints", "Generic",
     "Stainless steel safety pins assorted sizes for bandage fastening.",
     "Pack of 12", False),

    # ── EYE & EAR CARE (Page 3 grid) ──────────────────────────────────────
    ("Eye Wash / Eye Bath", "First Aid > Eye & Ear Care", "Generic",
     "Sterile saline eye wash solution and eye bath cup for eye irrigation.",
     "500ml bottle + cup", False),
    ("Eye Patches",         "First Aid > Eye & Ear Care", "Generic",
     "Sterile adhesive eye patches for eye protection and post-operative care.",
     "Box of 50", False),

    # ── FIRST AID KITS – misc items (Page 3 grid) ─────────────────────────
    ("Guidance Card",       "First Aid > First Aid Kits", "FirstAid",
     "Laminated first aid quick-reference guidance card.",
     "Single", False),
    ("Face Shield",         "First Aid > Resuscitation", "Generic",
     "Disposable face shield for CPR protection.",
     "Single use", False),

    # ── RESUSCITATION (Page 3 bottom) ─────────────────────────────────────
    ("BVM Resuscitator",    "First Aid > Resuscitation", "Laerdal",
     "Bag-valve-mask manual resuscitator, adult size, with oxygen reservoir.",
     "Single", False),
    ("Nebulizer Mask Set",  "First Aid > Resuscitation", "Generic",
     "Nebulizer / oxygen mask set, adult and paediatric, with tubing.",
     "Set", False),

    # ── MEDICAL DISPOSABLES (Page 4) ──────────────────────────────────────
    ("Dressing Kit",              "Medical Disposables > General Disposables", "Generic",
     "Sterile single-use dressing kit with gloves, forceps, gauze and drape.",
     "Single use", False),
    ("Anesthesia Mask",           "Medical Disposables > General Disposables", "Smiths Medical",
     "Transparent PVC anaesthesia/induction face mask, adult and paediatric sizes.",
     "Single use", False),
    ("Tongue Depressor (Wooden)", "Medical Disposables > General Disposables", "Generic",
     "Sterile and non-sterile wooden tongue depressors for oral examination.",
     "Box of 100", False),
    ("Sterilization Reel 200m",   "Medical Disposables > Sterilization", "Generic",
     "Self-sealing flat sterilization reel for autoclave pouches. 200m roll.",
     "200m roll", False),
    ("Gauze Products",            "Medical Disposables > General Disposables", "Generic",
     "Absorbent cotton gauze swabs, bandages and rolls for wound care.",
     "Various sizes", False),
    ("Post Mortem Kit",           "Medical Disposables > General Disposables", "Generic",
     "Complete post mortem preparation kit for morgue and hospital use.",
     "Single use", False),
    ("Surgical Gown (Sterile)",   "Medical Disposables > Gowns & Drapes", "Cardinal Health",
     "Sterile surgical gown, fluid-resistant, with knit cuffs.",
     "Single use", False),
    ("Latex Gloves",              "Medical Disposables > Gloves", "Medline",
     "Powder-free latex examination gloves. Available in S/M/L/XL.",
     "Box of 100", False),
    ("Vinyl Gloves",              "Medical Disposables > Gloves", "Medline",
     "Powder-free vinyl examination gloves. Latex-free alternative.",
     "Box of 100", False),

    # ── ORTHOPAEDIC (Page 4) ───────────────────────────────────────────────
    ("Cervical Collar",  "Orthopaedic > Collars & Slings",   "DeRoyal",
     "Rigid cervical collar for neck immobilisation and trauma management.",
     "Adjustable / Various sizes", False),
    ("SAM Splint",       "Orthopaedic > Splints & Supports", "Seward",
     "Malleable aluminium SAM splint for limb immobilisation in emergencies.",
     "Various sizes", False),
    ("AIR Splint",       "Orthopaedic > Splints & Supports", "Generic",
     "Inflatable transparent PVC air splint for limb immobilisation.",
     "Various sizes", False),
    ("Finger Splint",    "Orthopaedic > Splints & Supports", "Generic",
     "Adjustable aluminium finger splint with foam padding.",
     "Various sizes", False),

    # ── GYNAECOLOGY (Page 4) ───────────────────────────────────────────────
    ("Cervical Scraper",      "Gynaecology > Gynaecology Instruments", "Generic",
     "Disposable wooden cervical spatula / scraper for cervical smear tests.",
     "Box of 100", False),
    ("Vaginal Speculum",      "Gynaecology > Gynaecology Instruments", "Generic",
     "Disposable plastic bivalve vaginal speculum. Available in S/M/L.",
     "Box of 25", False),
    ("Umbilical Cord Clamp",  "Gynaecology > Obstetrics", "Generic",
     "Single-use disposable umbilical cord clamp with safety lock.",
     "Box of 100", False),

    # ── PLASTIC PRODUCTS (Page 5) ──────────────────────────────────────────
    ("Bed Pan – Fracture",     "Plastic Products > Patient Hygiene", "Generic",
     "Low-profile fracture bed pan for patients with limited mobility.",
     "Single", False),
    ("Bed Pan – Pontoon",      "Plastic Products > Patient Hygiene", "Generic",
     "Standard pontoon-style bed pan for bedridden patients.",
     "Single", False),
    ("Wash Basin",             "Plastic Products > Basins & Bowls",  "Generic",
     "Durable polypropylene wash basin for patient hygiene and wound care.",
     "Single", False),
    ("Basin Emesis (Kidney)",  "Plastic Products > Basins & Bowls",  "Generic",
     "Kidney-shaped emesis basin / vomit bowl.",
     "Single", False),
    ("Sitz Bath",              "Plastic Products > Patient Hygiene", "Generic",
     "Portable sitz bath that fits over standard toilet for perineal soaking.",
     "Single", False),
    ("Male Urinal",            "Plastic Products > Patient Hygiene", "Generic",
     "Spill-resistant male urinal bottle for bedridden patients.",
     "Single", False),

    # ── OTHER PRODUCTS (Page 5) ────────────────────────────────────────────
    ("Ear Syringe",                "Medical Devices & Lab > Diagnostic Equipment", "Generic",
     "Rubber bulb ear syringe for ear irrigation and wax removal.",
     "Single", False),
    ("Eye Pads",                   "First Aid > Eye & Ear Care", "Generic",
     "Sterile adhesive eye pads for post-operative and injury eye protection.",
     "Box of 50", False),
    ("CPR Mask",                   "First Aid > Resuscitation", "Generic",
     "Pocket CPR resuscitation mask with one-way valve for safe rescue breathing.",
     "Single", False),
    ("Razer Medical Disp. 2-Sided","Medical Devices & Lab > Diagnostic Equipment", "Generic",
     "Double-sided medical disposable razor for pre-operative skin preparation.",
     "Box of 50", False),
    ("Laryngoscope",               "Medical Devices & Lab > Diagnostic Equipment", "Welch Allyn",
     "Fibre-optic laryngoscope handle with blade set for intubation.",
     "Set", False),

    # ── ANAESTHESIA & AIRWAY (Page 5) ─────────────────────────────────────
    ("Closed Wound Suction Set",  "Anaesthesia & Airway > Suction & Drainage", "Generic",
     "Closed wound drainage system with reservoir for post-operative fluid collection.",
     "Single use", False),
    ("Nasal Oxygen Cannula",      "Anaesthesia & Airway > Oxygen Therapy",    "Generic",
     "Soft nasal oxygen cannula, adult and paediatric, for low-flow oxygen delivery.",
     "Single use", False),
    ("Guedal Airways",            "Anaesthesia & Airway > Airway Management", "Smiths Medical",
     "Oropharyngeal airway (Guedel) in sizes 000–5 for airway maintenance.",
     "Each / Set of 8 sizes", False),
    ("Extension Tube",            "Anaesthesia & Airway > Oxygen Therapy",    "Generic",
     "Oxygen extension tube for connecting mask/cannula to flow meter.",
     "2m length", False),
    ("Suction Catheter",          "Anaesthesia & Airway > Suction & Drainage","Generic",
     "Sterile flexible suction catheter with thumb-control port.",
     "Box of 10", False),
    ("Ryles Tube (NGT)",          "Anaesthesia & Airway > Airway Management", "Generic",
     "Nasogastric Ryles tube for feeding, aspiration and medication delivery.",
     "Single use", False),
    ("Yankuer Set With Handle",   "Anaesthesia & Airway > Suction & Drainage","Generic",
     "Rigid Yankuer suction tip with handle for oral and pharyngeal suctioning.",
     "Single use", False),
    ("Endotracheal Tube Cuffed",  "Anaesthesia & Airway > Airway Management", "Smiths Medical",
     "PVC cuffed endotracheal tube with 15mm connector. Full range of sizes.",
     "Single use", False),
    ("Endotracheal Tube Uncuffed","Anaesthesia & Airway > Airway Management", "Smiths Medical",
     "PVC uncuffed endotracheal tube for paediatric use. Full range of sizes.",
     "Single use", False),

    # ── UROLOGY (Page 5) ───────────────────────────────────────────────────
    ("Nelaton Catheter",               "Urology > Catheters",       "Generic",
     "Straight PVC nelaton catheter for intermittent bladder drainage.",
     "Single use", False),
    ("Urine Bag",                      "Urology > Urine Collection", "Generic",
     "Sterile 2L urine drainage bag with anti-reflux valve and drain outlet.",
     "Single use", False),
    ("Urine Leg Bag",                  "Urology > Urine Collection", "Generic",
     "Discreet 750ml leg urine bag with comfort straps for ambulatory patients.",
     "Single use", False),
    ("Condom Catheter",                "Urology > Catheters",        "Generic",
     "External male condom catheter for urinary incontinence management.",
     "Box of 30", False),
    ("Foley Catheter 2-Way",           "Urology > Catheters",        "Hollister",
     "Two-way latex foley catheter with balloon for continuous bladder drainage.",
     "Single use", False),
    ("Irrigation Kit with Bulb Syringe","Urology > Irrigation",      "Generic",
     "Bladder irrigation set with bulb syringe for catheter maintenance.",
     "Single use", False),

    # ── MEDICAL DEVICES & LAB (Page 6) ────────────────────────────────────
    ("Cover Slip",               "Medical Devices & Lab > Laboratory Consumables", "Generic",
     "Microscope cover slips 24×24mm, 0.13–0.17mm thick.",
     "Box of 100", False),
    ("Cotton Tip Applicator",    "Medical Devices & Lab > Laboratory Consumables", "Generic",
     "Sterile cotton-tipped applicator swabs for specimen collection and wound care.",
     "Box of 200", False),
    ("Wooden Sticks (Applicator)","Medical Devices & Lab > Laboratory Consumables","Generic",
     "Wooden applicator sticks for sample preparation and laboratory use.",
     "Box of 200", False),
    ("Pipettes 1ml & 3ml",       "Medical Devices & Lab > Laboratory Consumables", "Generic",
     "Disposable plastic transfer pipettes 1ml and 3ml for lab sample handling.",
     "Box of 500", False),
    ("Blood Lancet",             "Medical Devices & Lab > Laboratory Consumables", "BD Medical",
     "Safety blood lancet for capillary blood sampling. Retractable blade.",
     "Box of 200", False),
    ("Centrifuge Machine",       "Medical Devices & Lab > Laboratory Consumables", "Generic",
     "Benchtop centrifuge machine for laboratory sample separation.",
     "Single unit", False),
    ("Lab Disposables",          "Medical Devices & Lab > Laboratory Consumables", "Generic",
     "Assorted laboratory disposables including specimen containers and collection tubes.",
     "Various", False),
    ("Pathology Container",      "Medical Devices & Lab > Laboratory Consumables", "Generic",
     "Leak-proof polypropylene pathology specimen containers with screw lid.",
     "Various sizes", False),

    # ── SUTURES / SURGICAL INSTRUMENTS (Page 6) ───────────────────────────
    ("Holloware Set",            "Medical Devices & Lab > Surgical Instruments", "Generic",
     "Stainless steel holloware set including kidney dish, gallipot and dressing jar.",
     "Set", False),
    ("Scissors & Forceps Set",   "Medical Devices & Lab > Surgical Instruments", "Generic",
     "Stainless steel surgical scissors and artery forceps set.",
     "Set", False),
    ("Surgical Instruments Set", "Medical Devices & Lab > Surgical Instruments", "Generic",
     "Complete stainless steel surgical instrument set for minor procedures.",
     "Set", False),

    # ── FURNITURE (Page 6) ────────────────────────────────────────────────
    ("Examination Couch",          "Furniture & Equipment > Examination & Treatment", "SOMO",
     "Padded steel examination couch with adjustable backrest.",
     "Single unit", False),
    ("Emergency Crash Cart",       "Furniture & Equipment > Storage & Trolleys",     "Generic",
     "Multi-drawer emergency crash cart/trolley with locking mechanism.",
     "Single unit", False),
    ("Foot Stool",                 "Furniture & Equipment > Examination & Treatment", "Generic",
     "Steel step stool for patient mounting examination tables.",
     "Single unit", False),
    ("Portable Couch / Massage Bed","Furniture & Equipment > Examination & Treatment","SOMO",
     "Lightweight portable folding couch/massage bed with carry bag.",
     "Single unit", False),
    ("Hospital Bed",               "Furniture & Equipment > Hospital Furniture",     "SOMO",
     "Manual two-crank hospital bed with side rails and IV pole socket.",
     "Single unit", False),
    ("Ward Screen",                "Furniture & Equipment > Hospital Furniture",     "Generic",
     "Three-panel folding ward privacy screen with curtain.",
     "Single unit", False),
    ("Revolving Stool",            "Furniture & Equipment > Hospital Furniture",     "Generic",
     "Height-adjustable revolving stool on castors for clinical workstations.",
     "Single unit", False),

    # ── DIAGNOSTIC / ANAESTHESIA EQUIPMENT (Page 7) ───────────────────────
    ("IV Stand",                "Furniture & Equipment > Examination & Treatment", "Generic",
     "Height-adjustable stainless steel IV pole stand on 5-caster base.",
     "Single unit", False),
    ("Exam Lamp",               "Furniture & Equipment > Examination & Treatment", "Welch Allyn",
     "Mobile examination lamp on floor stand with flexible gooseneck arm.",
     "Single unit", False),
    ("Weight Scale",            "Medical Devices & Lab > Diagnostic Equipment",   "Seca",
     "Clinical weight scale with height rod and BMI chart.",
     "Single unit", False),
    ("Eye Chart Box With Light","Medical Devices & Lab > Diagnostic Equipment",   "Generic",
     "Illuminated Snellen eye chart box for visual acuity testing.",
     "Single unit", False),
    ("Thermometer – Armpit Flat","Medical Devices & Lab > Diagnostic Equipment",  "Generic",
     "Glass armpit (axillary) flat clinical thermometer.",
     "Single", False),
    ("Thermometer – Oral",      "Medical Devices & Lab > Diagnostic Equipment",   "Generic",
     "Glass oral clinical thermometer.",
     "Single", False),
    ("Thermometer – Digital",   "Medical Devices & Lab > Diagnostic Equipment",   "Omron",
     "Fast digital thermometer for oral, axillary or rectal use.",
     "Single", False),
    ("Thermometer – Forehead",  "Medical Devices & Lab > Diagnostic Equipment",   "Omron",
     "Non-contact infrared forehead thermometer for quick temperature screening.",
     "Single", False),
    ("BP Monitor – Floor Type", "Medical Devices & Lab > Diagnostic Equipment",   "Omron",
     "Aneroid sphygmomanometer on floor stand for clinical blood pressure measurement.",
     "Single unit", False),
    ("Stethoscope",             "Medical Devices & Lab > Diagnostic Equipment",   "Welch Allyn",
     "Dual-head stethoscope with chestpiece and earpieces for auscultation.",
     "Single", False),
    ("X-Ray Viewer (Double)",   "Medical Devices & Lab > Diagnostic Equipment",   "Generic",
     "Double panel illuminated X-ray viewer/negatoscope.",
     "Single unit", False),
    ("Thermometer – Ear",       "Medical Devices & Lab > Diagnostic Equipment",   "Omron",
     "Infrared tympanic ear thermometer with probe covers.",
     "Single", False),
    ("Thermal Blanket",         "Emergency & Transport > Immobilisation",          "Generic",
     "Emergency mylar thermal rescue blanket for hypothermia prevention.",
     "Single use", False),
    ("Stretcher – Folding",     "Emergency & Transport > Stretchers & Spineboards","Ferno",
     "Lightweight aluminium folding stretcher for patient transport.",
     "Single unit", False),
    ("Stretcher – Scoop",       "Emergency & Transport > Stretchers & Spineboards","Ferno",
     "Scoop stretcher that splits to slide under a patient without lifting.",
     "Single unit", False),

    # ── EMERGENCY & TRANSPORT (Page 8) ────────────────────────────────────
    ("Stretcher – Automatic Loading","Emergency & Transport > Stretchers & Spineboards","Stryker",
     "Ambulance automatic loading stretcher with height adjustment.",
     "Single unit", False),
    ("Head Immobilizer",          "Emergency & Transport > Immobilisation",          "Laerdal",
     "Foam head immobiliser blocks with straps for spinal board use.",
     "Single", False),
    ("Vacuum Mattress",           "Emergency & Transport > Immobilisation",          "Generic",
     "Full-body vacuum mattress for immobilising trauma patients.",
     "Single unit", False),
    ("Nebulizer Machine",         "Emergency & Transport > Nebulisers & Therapy",    "Omron",
     "Compressor nebulizer machine for aerosol drug delivery.",
     "Single unit", False),
    ("Stretcher – Basket",        "Emergency & Transport > Stretchers & Spineboards","Ferno",
     "Rigid basket stretcher for confined space or water rescue.",
     "Single unit", False),
    ("Stretcher Trolley",         "Emergency & Transport > Stretchers & Spineboards","Ferno",
     "Height-adjustable stretcher trolley for hospital corridor transport.",
     "Single unit", False),
    ("Underarm Crutches",         "Orthopaedic > Mobility Aids",                    "Drive Medical",
     "Height-adjustable aluminium underarm crutches for limited weight bearing.",
     "Pair", False),
    ("Kendrick Extrication Splint","Emergency & Transport > Immobilisation",         "Ferno",
     "KED vest-style extrication device for seated spinal immobilisation.",
     "Single unit", False),
    ("Wheel Chair",               "Orthopaedic > Mobility Aids",                    "Invacare",
     "Foldable steel wheelchair with removable footrests and padded armrests.",
     "Single", False),
    ("Walker Wheels",             "Orthopaedic > Mobility Aids",                    "Invacare",
     "Lightweight aluminium wheeled walker frame with hand brakes.",
     "Single", False),
    ("Spine Board",               "Emergency & Transport > Stretchers & Spineboards","Ferno",
     "Radiolucent plastic spine board with handles and straps.",
     "Single unit", False),
    ("Ortho Pillow",              "Orthopaedic > Orthopaedic Pillows",              "Generic",
     "Memory foam orthopaedic contour pillow for cervical spine support.",
     "Single", False),
]


class Command(BaseCommand):
    help = "Seed the Al Ameen Pharmacy catalog from the brochure (medical & paramedical products)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete ALL existing products, categories, and brands before seeding.",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            self.stdout.write(self.style.WARNING("Clearing existing catalog..."))
            Product.objects.all().delete()
            Category.objects.all().delete()
            Brand.objects.all().delete()
            self.stdout.write(self.style.SUCCESS("Catalog cleared."))

        # ── Brands ──
        self.stdout.write("Creating brands...")
        brand_map = {}
        for brand_name in BRANDS:
            obj, _ = Brand.objects.get_or_create(
                name=brand_name,
                defaults={"slug": slugify(brand_name)},
            )
            brand_map[brand_name] = obj
        self.stdout.write(self.style.SUCCESS(f"  {len(brand_map)} brands ready."))

        # ── Categories ──
        self.stdout.write("Creating categories...")
        cat_map = {}
        display_order = 0
        for parent_name, children in CATEGORIES:
            parent_slug = slugify(parent_name)
            parent, _ = Category.objects.get_or_create(
                slug=parent_slug,
                defaults={
                    "name": parent_name,
                    "description": parent_name,
                    "parent": None,
                    "display_order": display_order,
                    "is_active": True,
                },
            )
            cat_map[parent_name] = parent
            display_order += 1
            for i, child_name in enumerate(children):
                child_slug = slugify(f"{parent_slug}-{child_name}")
                child, _ = Category.objects.get_or_create(
                    slug=child_slug,
                    defaults={
                        "name": child_name,
                        "description": child_name,
                        "parent": parent,
                        "display_order": i,
                        "is_active": True,
                    },
                )
                cat_map[f"{parent_name} > {child_name}"] = child
        self.stdout.write(self.style.SUCCESS(f"  {len(cat_map)} categories ready."))

        # ── Products ──
        self.stdout.write("Creating products...")
        created_count = 0
        skipped_count = 0
        for name, cat_path, brand_name, short_desc, pack_size, show_price in PRODUCTS:
            if Product.objects.filter(name=name).exists():
                skipped_count += 1
                continue

            category = cat_map.get(cat_path)
            brand = brand_map.get(brand_name)
            slug = slugify(name)

            base_slug = slug
            counter = 1
            while Product.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1

            Product.objects.create(
                name=name,
                slug=slug,
                brand=brand,
                category=category,
                short_description=short_desc,
                price="1.00",
                stock_quantity=50,
                status="active",
                requires_manual_review=False,
                is_featured=False,
                show_price=show_price,
                pack_size=pack_size,
            )
            created_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"  {created_count} products created, {skipped_count} skipped (already exist)."
        ))
        self.stdout.write(self.style.SUCCESS(
            f"\nBrochure catalog seeded successfully! Total: {created_count + skipped_count} products.\n"
            "Next: run import_brochure_images to assign correct images."
        ))
