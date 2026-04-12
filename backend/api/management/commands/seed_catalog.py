"""
Management command to seed the product catalog with UAE pharmacy OTC products.
Run with: python manage.py seed_catalog
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal
from api.models import Brand, Category, Product


class Command(BaseCommand):
    help = 'Seeds the database with UAE pharmacy OTC product catalog'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing catalog data before seeding',
        )

    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write('Clearing existing catalog data...')
            Product.objects.all().delete()
            Category.objects.all().delete()
            Brand.objects.all().delete()
            self.stdout.write(self.style.SUCCESS('Cleared all catalog data'))

        with transaction.atomic():
            brands = self.create_brands()
            categories = self.create_categories()
            products = self.create_products(brands, categories)

        self.stdout.write(self.style.SUCCESS(
            f'Successfully seeded: {len(brands)} brands, {len(categories)} categories, {len(products)} products'
        ))

    def create_brands(self):
        """Create all brands"""
        self.stdout.write('Creating brands...')

        brand_names = [
            # Pain Relief
            'Panadol', 'Abbott', 'Voltaren', 'Advil', 'Tiger Balm', 'Mentholatum',
            # Cold & Flu
            'Strepsils', 'Otrivin', 'Vicks', 'Telfast', 'Claritine', 'Prospan',
            # Digestive
            'Gaviscon', 'Rennie', 'Dulcolax', 'Imodium', 'Nutritionl',
            # Vitamins
            'Centrum', 'Redoxon', 'Solgar', 'Seven Seas', 'Sunshine Nutrition',
            'Vitabiotics', 'Caltrate',
            # First Aid
            'Hansaplast', '3M Nexcare', 'Dettol', 'Betadine', 'Life',
            # Skincare
            'Cetaphil', 'Eucerin', 'Bioderma', 'Neutrogena',
            # Oral Care
            'Sensodyne', 'Listerine',
            # Eye Care
            'Systane', 'Refresh',
            # Baby Care
            'Pampers', 'Sudocrem', "Johnson's",
        ]

        brands = {}
        for name in brand_names:
            brand, created = Brand.objects.get_or_create(name=name)
            brands[name] = brand
            if created:
                self.stdout.write(f'  Created brand: {name}')

        return brands

    def create_categories(self):
        """Create hierarchical categories"""
        self.stdout.write('Creating categories...')

        # Define category structure: {parent: [subcategories]}
        category_structure = {
            'Pain Relief': ['Tablets & Capsules', 'Topical', 'Patches'],
            'Cold, Flu & Allergy': ['Cough Syrups', 'Lozenges & Throat Care', 'Nasal Care', 'Antihistamines'],
            'Digestive Health': ['Antacids & Heartburn', 'Laxatives', 'Anti-Diarrhea', 'Probiotics'],
            'Vitamins & Supplements': ['Multivitamins', 'Vitamin C & Immune', 'Vitamin D', 'Omega & Fish Oil', 'Iron & Minerals'],
            'First Aid': ['Bandages & Dressings', 'Antiseptics', 'Wound Care', 'First Aid Kits'],
            'Skincare': ['Moisturizers', 'Cleansers', 'Sun Protection', 'Lip Care'],
            'Oral Care': ['Toothpaste', 'Mouthwash', 'Dental Accessories'],
            'Eye & Ear Care': ['Eye Drops', 'Contact Lens Care', 'Ear Care'],
            'Baby Care': ['Baby Formula', 'Diapers & Wipes', 'Baby Skincare', 'Feeding Accessories'],
            'Personal Care': ['Hand Sanitizers', 'Feminine Care', 'Deodorants'],
            'Sports Nutrition': ['Protein Powders', 'Energy & Performance', 'Amino Acids'],
            'Medical Devices': ['Thermometers', 'Blood Pressure Monitors', 'Glucose Monitors'],
        }

        categories = {}
        display_order = 0

        for parent_name, subcats in category_structure.items():
            # Create parent category
            parent, created = Category.objects.get_or_create(
                name=parent_name,
                defaults={
                    'description': f'{parent_name} products',
                    'display_order': display_order,
                    'is_active': True,
                }
            )
            categories[parent_name] = parent
            display_order += 1
            if created:
                self.stdout.write(f'  Created category: {parent_name}')

            # Create subcategories
            sub_order = 0
            for subcat_name in subcats:
                subcat, created = Category.objects.get_or_create(
                    name=subcat_name,
                    parent=parent,
                    defaults={
                        'description': f'{subcat_name} in {parent_name}',
                        'display_order': sub_order,
                        'is_active': True,
                    }
                )
                # Store with full path key for easy lookup
                categories[f'{parent_name} > {subcat_name}'] = subcat
                sub_order += 1
                if created:
                    self.stdout.write(f'    Created subcategory: {subcat_name}')

        return categories

    def create_products(self, brands, categories):
        """Create all products"""
        self.stdout.write('Creating products...')

        products_data = [
            # PAIN RELIEF - Tablets & Capsules
            {
                'name': 'Panadol Extra',
                'brand': 'Panadol',
                'category': 'Pain Relief > Tablets & Capsules',
                'short_description': 'Fast-acting pain relief for headaches and body pain',
                'dosage': '500mg/65mg',
                'pack_size': '24 tablets',
                'active_ingredient': 'Paracetamol, Caffeine',
                'price': '22.00',
                'stock_quantity': 100,
                'is_featured': True,
            },
            {
                'name': 'Panadol Advance',
                'brand': 'Panadol',
                'category': 'Pain Relief > Tablets & Capsules',
                'short_description': 'Rapid absorption formula for effective pain relief',
                'dosage': '500mg',
                'pack_size': '48 tablets',
                'active_ingredient': 'Paracetamol',
                'price': '26.00',
                'stock_quantity': 80,
                'is_featured': True,
            },
            {
                'name': 'Brufen 400',
                'brand': 'Abbott',
                'category': 'Pain Relief > Tablets & Capsules',
                'short_description': 'Anti-inflammatory relief for muscle and joint pain',
                'dosage': '400mg',
                'pack_size': '30 tablets',
                'active_ingredient': 'Ibuprofen',
                'price': '30.00',
                'stock_quantity': 75,
                'is_featured': True,
            },
            {
                'name': 'Advil Liquid Gel',
                'brand': 'Advil',
                'category': 'Pain Relief > Tablets & Capsules',
                'short_description': 'Fast-acting liquid gel capsules for pain relief',
                'dosage': '200mg',
                'pack_size': '20 capsules',
                'active_ingredient': 'Ibuprofen',
                'price': '33.00',
                'stock_quantity': 60,
            },
            # PAIN RELIEF - Topical
            {
                'name': 'Voltaren Emulgel',
                'brand': 'Voltaren',
                'category': 'Pain Relief > Topical',
                'short_description': 'Targeted relief gel for joint and muscle pain',
                'dosage': '1%',
                'pack_size': '100g',
                'active_ingredient': 'Diclofenac',
                'price': '50.00',
                'stock_quantity': 70,
                'is_featured': True,
            },
            {
                'name': 'Voltaren Spray',
                'brand': 'Voltaren',
                'category': 'Pain Relief > Topical',
                'short_description': 'Quick-drying spray for localized pain relief',
                'dosage': '4%',
                'pack_size': '25g',
                'active_ingredient': 'Diclofenac',
                'price': '60.00',
                'stock_quantity': 45,
            },
            {
                'name': 'Deep Heat Cream',
                'brand': 'Mentholatum',
                'category': 'Pain Relief > Topical',
                'short_description': 'Warming relief cream for muscular aches',
                'dosage': '',
                'pack_size': '67g',
                'active_ingredient': 'Menthol, Methyl Salicylate',
                'price': '30.00',
                'stock_quantity': 55,
            },
            {
                'name': 'Tiger Balm Red',
                'brand': 'Tiger Balm',
                'category': 'Pain Relief > Topical',
                'short_description': 'Traditional warming ointment for muscle soreness',
                'dosage': '',
                'pack_size': '19g',
                'active_ingredient': 'Camphor, Menthol',
                'price': '18.00',
                'stock_quantity': 90,
            },
            # COLD, FLU & ALLERGY
            {
                'name': 'Panadol Cold + Flu',
                'brand': 'Panadol',
                'category': 'Cold, Flu & Allergy > Antihistamines',
                'short_description': 'Multi-symptom relief for cold and flu',
                'dosage': '500mg',
                'pack_size': '24 tablets',
                'active_ingredient': 'Paracetamol, Phenylephrine',
                'price': '26.00',
                'stock_quantity': 85,
                'is_featured': True,
            },
            {
                'name': 'Strepsils Orange',
                'brand': 'Strepsils',
                'category': 'Cold, Flu & Allergy > Lozenges & Throat Care',
                'short_description': 'Soothing lozenges for sore throat relief',
                'dosage': '1.2mg/0.6mg',
                'pack_size': '24 lozenges',
                'active_ingredient': 'Amylmetacresol, Dichlorobenzyl',
                'price': '22.00',
                'stock_quantity': 100,
                'is_featured': True,
            },
            {
                'name': 'Strepsils Honey & Lemon',
                'brand': 'Strepsils',
                'category': 'Cold, Flu & Allergy > Lozenges & Throat Care',
                'short_description': 'Honey-flavored throat lozenges with antibacterial action',
                'dosage': '1.2mg/0.6mg',
                'pack_size': '24 lozenges',
                'active_ingredient': 'Amylmetacresol, Dichlorobenzyl',
                'price': '22.00',
                'stock_quantity': 95,
            },
            {
                'name': 'Otrivin Nasal Spray',
                'brand': 'Otrivin',
                'category': 'Cold, Flu & Allergy > Nasal Care',
                'short_description': 'Fast relief from nasal congestion',
                'dosage': '0.1%',
                'pack_size': '10ml',
                'active_ingredient': 'Xylometazoline',
                'price': '28.00',
                'stock_quantity': 80,
                'is_featured': True,
            },
            {
                'name': 'Vicks VapoRub',
                'brand': 'Vicks',
                'category': 'Cold, Flu & Allergy > Cough Syrups',
                'short_description': 'Topical ointment for cough and congestion relief',
                'dosage': '',
                'pack_size': '50g',
                'active_ingredient': 'Menthol, Camphor, Eucalyptus',
                'price': '25.00',
                'stock_quantity': 90,
            },
            {
                'name': 'Telfast 180',
                'brand': 'Telfast',
                'category': 'Cold, Flu & Allergy > Antihistamines',
                'short_description': 'Non-drowsy allergy relief for 24 hours',
                'dosage': '180mg',
                'pack_size': '15 tablets',
                'active_ingredient': 'Fexofenadine',
                'price': '50.00',
                'stock_quantity': 65,
                'is_featured': True,
            },
            {
                'name': 'Claritine',
                'brand': 'Claritine',
                'category': 'Cold, Flu & Allergy > Antihistamines',
                'short_description': 'Once-daily allergy symptom relief',
                'dosage': '10mg',
                'pack_size': '10 tablets',
                'active_ingredient': 'Loratadine',
                'price': '33.00',
                'stock_quantity': 70,
            },
            {
                'name': 'Prospan Cough Syrup',
                'brand': 'Prospan',
                'category': 'Cold, Flu & Allergy > Cough Syrups',
                'short_description': 'Natural ivy leaf extract for productive cough',
                'dosage': '',
                'pack_size': '100ml',
                'active_ingredient': 'Ivy Leaf Extract',
                'price': '40.00',
                'stock_quantity': 55,
            },
            # DIGESTIVE HEALTH
            {
                'name': 'Gaviscon Double Action',
                'brand': 'Gaviscon',
                'category': 'Digestive Health > Antacids & Heartburn',
                'short_description': 'Long-lasting relief from heartburn and indigestion',
                'dosage': '',
                'pack_size': '300ml',
                'active_ingredient': 'Sodium Alginate, Calcium Carbonate',
                'price': '50.00',
                'stock_quantity': 60,
                'is_featured': True,
            },
            {
                'name': 'Gaviscon Advance Sachets',
                'brand': 'Gaviscon',
                'category': 'Digestive Health > Antacids & Heartburn',
                'short_description': 'Convenient sachets for on-the-go heartburn relief',
                'dosage': '10ml',
                'pack_size': '12 sachets',
                'active_ingredient': 'Sodium Alginate',
                'price': '40.00',
                'stock_quantity': 50,
            },
            {
                'name': 'Rennie Spearmint',
                'brand': 'Rennie',
                'category': 'Digestive Health > Antacids & Heartburn',
                'short_description': 'Chewable antacid tablets for fast relief',
                'dosage': '',
                'pack_size': '24 tablets',
                'active_ingredient': 'Calcium Carbonate, Magnesium Carbonate',
                'price': '22.00',
                'stock_quantity': 75,
            },
            {
                'name': 'Dulcolax',
                'brand': 'Dulcolax',
                'category': 'Digestive Health > Laxatives',
                'short_description': 'Gentle overnight relief from constipation',
                'dosage': '5mg',
                'pack_size': '30 tablets',
                'active_ingredient': 'Bisacodyl',
                'price': '28.00',
                'stock_quantity': 55,
            },
            {
                'name': 'Imodium Original',
                'brand': 'Imodium',
                'category': 'Digestive Health > Anti-Diarrhea',
                'short_description': 'Fast-acting diarrhea relief',
                'dosage': '2mg',
                'pack_size': '12 capsules',
                'active_ingredient': 'Loperamide',
                'price': '32.00',
                'stock_quantity': 45,
            },
            {
                'name': 'Probiotic Complex',
                'brand': 'Nutritionl',
                'category': 'Digestive Health > Probiotics',
                'short_description': 'Daily probiotic supplement for gut health',
                'dosage': '5 billion CFU',
                'pack_size': '30 capsules',
                'active_ingredient': 'Lactobacillus',
                'price': '62.00',
                'stock_quantity': 40,
            },
            # VITAMINS & SUPPLEMENTS
            {
                'name': 'Centrum Adults',
                'brand': 'Centrum',
                'category': 'Vitamins & Supplements > Multivitamins',
                'short_description': 'Complete daily multivitamin for adults',
                'dosage': '',
                'pack_size': '60 tablets',
                'active_ingredient': 'Multivitamin Complex',
                'price': '82.00',
                'stock_quantity': 50,
                'is_featured': True,
            },
            {
                'name': 'Centrum Women',
                'brand': 'Centrum',
                'category': 'Vitamins & Supplements > Multivitamins',
                'short_description': 'Tailored nutrition for women\'s health needs',
                'dosage': '',
                'pack_size': '60 tablets',
                'active_ingredient': 'Multivitamin + Iron',
                'price': '88.00',
                'stock_quantity': 45,
            },
            {
                'name': 'Vitamin C 1000mg Effervescent',
                'brand': 'Redoxon',
                'category': 'Vitamins & Supplements > Vitamin C & Immune',
                'short_description': 'Effervescent tablets for immune support',
                'dosage': '1000mg',
                'pack_size': '15 tablets',
                'active_ingredient': 'Ascorbic Acid',
                'price': '30.00',
                'stock_quantity': 80,
                'is_featured': True,
            },
            {
                'name': 'Vitamin D3 1000IU',
                'brand': 'Sunshine Nutrition',
                'category': 'Vitamins & Supplements > Vitamin D',
                'short_description': 'Essential vitamin for bone and immune health',
                'dosage': '1000IU',
                'pack_size': '100 softgels',
                'active_ingredient': 'Cholecalciferol',
                'price': '50.00',
                'stock_quantity': 65,
                'is_featured': True,
            },
            {
                'name': 'Omega-3 Fish Oil',
                'brand': 'Seven Seas',
                'category': 'Vitamins & Supplements > Omega & Fish Oil',
                'short_description': 'Pure fish oil for heart and brain health',
                'dosage': '1000mg',
                'pack_size': '60 capsules',
                'active_ingredient': 'EPA, DHA',
                'price': '72.00',
                'stock_quantity': 55,
            },
            {
                'name': 'Iron + Folic Acid',
                'brand': 'Vitabiotics',
                'category': 'Vitamins & Supplements > Iron & Minerals',
                'short_description': 'Iron supplement with folic acid for energy',
                'dosage': '14mg',
                'pack_size': '30 tablets',
                'active_ingredient': 'Iron, Folic Acid',
                'price': '40.00',
                'stock_quantity': 50,
            },
            {
                'name': 'Zinc 50mg',
                'brand': 'Solgar',
                'category': 'Vitamins & Supplements > Iron & Minerals',
                'short_description': 'Essential mineral for immune function',
                'dosage': '50mg',
                'pack_size': '100 tablets',
                'active_ingredient': 'Zinc Gluconate',
                'price': '62.00',
                'stock_quantity': 45,
            },
            {
                'name': 'Calcium + Vitamin D',
                'brand': 'Caltrate',
                'category': 'Vitamins & Supplements > Iron & Minerals',
                'short_description': 'Bone health support with vitamin D',
                'dosage': '600mg/400IU',
                'pack_size': '60 tablets',
                'active_ingredient': 'Calcium, Vitamin D3',
                'price': '60.00',
                'stock_quantity': 50,
            },
            # FIRST AID
            {
                'name': 'Hansaplast Plasters Assorted',
                'brand': 'Hansaplast',
                'category': 'First Aid > Bandages & Dressings',
                'short_description': 'Assorted waterproof plasters for minor cuts',
                'dosage': '',
                'pack_size': '40 strips',
                'active_ingredient': '',
                'price': '22.00',
                'stock_quantity': 85,
            },
            {
                'name': 'Nexcare Flexible Bandages',
                'brand': '3M Nexcare',
                'category': 'First Aid > Bandages & Dressings',
                'short_description': 'Flexible fabric bandages for active lifestyles',
                'dosage': '',
                'pack_size': '30 strips',
                'active_ingredient': '',
                'price': '25.00',
                'stock_quantity': 70,
            },
            {
                'name': 'Dettol Antiseptic Liquid',
                'brand': 'Dettol',
                'category': 'First Aid > Antiseptics',
                'short_description': 'Multi-use antiseptic for wound cleaning',
                'dosage': '',
                'pack_size': '500ml',
                'active_ingredient': 'Chloroxylenol',
                'price': '32.00',
                'stock_quantity': 75,
                'is_featured': True,
            },
            {
                'name': 'Betadine Solution',
                'brand': 'Betadine',
                'category': 'First Aid > Antiseptics',
                'short_description': 'Antiseptic solution for wound disinfection',
                'dosage': '10%',
                'pack_size': '120ml',
                'active_ingredient': 'Povidone-Iodine',
                'price': '28.00',
                'stock_quantity': 65,
            },
            {
                'name': 'Home First Aid Kit',
                'brand': 'Life',
                'category': 'First Aid > First Aid Kits',
                'short_description': 'Complete home first aid kit with essentials',
                'dosage': '',
                'pack_size': '1 kit',
                'active_ingredient': '',
                'price': '52.00',
                'stock_quantity': 30,
            },
            # SKINCARE
            {
                'name': 'Cetaphil Gentle Cleanser',
                'brand': 'Cetaphil',
                'category': 'Skincare > Cleansers',
                'short_description': 'Soap-free cleanser for sensitive skin',
                'dosage': '',
                'pack_size': '236ml',
                'active_ingredient': '',
                'price': '72.00',
                'stock_quantity': 55,
                'is_featured': True,
            },
            {
                'name': 'Cetaphil Moisturizing Lotion',
                'brand': 'Cetaphil',
                'category': 'Skincare > Moisturizers',
                'short_description': 'Lightweight daily moisturizer for all skin types',
                'dosage': '',
                'pack_size': '473ml',
                'active_ingredient': '',
                'price': '105.00',
                'stock_quantity': 45,
            },
            {
                'name': 'Eucerin Aquaphor',
                'brand': 'Eucerin',
                'category': 'Skincare > Moisturizers',
                'short_description': 'Multi-purpose healing ointment for dry skin',
                'dosage': '',
                'pack_size': '50g',
                'active_ingredient': 'Panthenol',
                'price': '62.00',
                'stock_quantity': 40,
            },
            {
                'name': 'Bioderma Sensibio H2O',
                'brand': 'Bioderma',
                'category': 'Skincare > Cleansers',
                'short_description': 'Micellar water for sensitive skin cleansing',
                'dosage': '',
                'pack_size': '250ml',
                'active_ingredient': '',
                'price': '82.00',
                'stock_quantity': 50,
            },
            {
                'name': 'Neutrogena Sunscreen SPF50',
                'brand': 'Neutrogena',
                'category': 'Skincare > Sun Protection',
                'short_description': 'Lightweight sunscreen for daily protection',
                'dosage': 'SPF50',
                'pack_size': '88ml',
                'active_ingredient': '',
                'price': '62.00',
                'stock_quantity': 60,
            },
            # ORAL CARE
            {
                'name': 'Sensodyne Rapid Relief',
                'brand': 'Sensodyne',
                'category': 'Oral Care > Toothpaste',
                'short_description': 'Fast relief toothpaste for sensitive teeth',
                'dosage': '',
                'pack_size': '75ml',
                'active_ingredient': 'Stannous Fluoride',
                'price': '28.00',
                'stock_quantity': 80,
                'is_featured': True,
            },
            {
                'name': 'Sensodyne Repair & Protect',
                'brand': 'Sensodyne',
                'category': 'Oral Care > Toothpaste',
                'short_description': 'Rebuilds and strengthens sensitive teeth',
                'dosage': '',
                'pack_size': '100g',
                'active_ingredient': 'NovaMin',
                'price': '32.00',
                'stock_quantity': 70,
            },
            {
                'name': 'Listerine Cool Mint',
                'brand': 'Listerine',
                'category': 'Oral Care > Mouthwash',
                'short_description': 'Antiseptic mouthwash for fresh breath',
                'dosage': '',
                'pack_size': '500ml',
                'active_ingredient': 'Essential Oils',
                'price': '32.00',
                'stock_quantity': 75,
            },
            {
                'name': 'Listerine Total Care',
                'brand': 'Listerine',
                'category': 'Oral Care > Mouthwash',
                'short_description': 'Complete oral care with 6 benefits',
                'dosage': '',
                'pack_size': '500ml',
                'active_ingredient': 'Essential Oils, Fluoride',
                'price': '36.00',
                'stock_quantity': 65,
            },
            # EYE CARE
            {
                'name': 'Systane Eye Drops',
                'brand': 'Systane',
                'category': 'Eye & Ear Care > Eye Drops',
                'short_description': 'Lubricating drops for dry eye relief',
                'dosage': '',
                'pack_size': '10ml',
                'active_ingredient': 'Polyethylene Glycol',
                'price': '38.00',
                'stock_quantity': 55,
                'is_featured': True,
            },
            {
                'name': 'Systane Ultra',
                'brand': 'Systane',
                'category': 'Eye & Ear Care > Eye Drops',
                'short_description': 'Extended protection for severe dry eyes',
                'dosage': '',
                'pack_size': '10ml',
                'active_ingredient': 'Polyethylene Glycol',
                'price': '60.00',
                'stock_quantity': 45,
            },
            {
                'name': 'Refresh Tears',
                'brand': 'Refresh',
                'category': 'Eye & Ear Care > Eye Drops',
                'short_description': 'Moisturizing relief for mild dry eyes',
                'dosage': '',
                'pack_size': '15ml',
                'active_ingredient': 'Carboxymethylcellulose',
                'price': '40.00',
                'stock_quantity': 50,
            },
            # BABY CARE
            {
                'name': 'Pampers Active Baby Size 4',
                'brand': 'Pampers',
                'category': 'Baby Care > Diapers & Wipes',
                'short_description': 'Up to 12 hours of dryness protection',
                'dosage': '',
                'pack_size': '44 diapers',
                'active_ingredient': '',
                'price': '62.00',
                'stock_quantity': 40,
            },
            {
                'name': 'Sudocrem Antiseptic Cream',
                'brand': 'Sudocrem',
                'category': 'Baby Care > Baby Skincare',
                'short_description': 'Healing cream for diaper rash and skin irritation',
                'dosage': '',
                'pack_size': '125g',
                'active_ingredient': 'Zinc Oxide',
                'price': '40.00',
                'stock_quantity': 55,
            },
            {
                'name': "Johnson's Baby Shampoo",
                'brand': "Johnson's",
                'category': 'Baby Care > Baby Skincare',
                'short_description': "Gentle no-tears formula for baby's delicate hair",
                'dosage': '',
                'pack_size': '500ml',
                'active_ingredient': '',
                'price': '28.00',
                'stock_quantity': 65,
            },
        ]

        created_products = []
        for data in products_data:
            brand = brands.get(data['brand'])
            category = categories.get(data['category'])

            if not brand:
                self.stdout.write(self.style.WARNING(f"  Brand not found: {data['brand']}"))
                continue
            if not category:
                self.stdout.write(self.style.WARNING(f"  Category not found: {data['category']}"))
                continue

            product, created = Product.objects.get_or_create(
                name=data['name'],
                defaults={
                    'brand': brand,
                    'category': category,
                    'short_description': data['short_description'],
                    'dosage': data.get('dosage', ''),
                    'pack_size': data.get('pack_size', ''),
                    'active_ingredient': data.get('active_ingredient', ''),
                    'price': Decimal(data['price']),
                    'stock_quantity': data.get('stock_quantity', 50),
                    'is_featured': data.get('is_featured', False),
                    'requires_prescription': False,
                    'status': 'active',
                }
            )

            if created:
                created_products.append(product)
                self.stdout.write(f'  Created product: {data["name"]}')
            else:
                self.stdout.write(f'  Product exists: {data["name"]}')

        return created_products
