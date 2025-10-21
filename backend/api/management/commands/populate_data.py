from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from api.models import Category, Product


class Command(BaseCommand):
    help = 'Populate database with sample pharmacy products'

    def handle(self, *args, **options):
        self.stdout.write('Creating sample data...')

        # Create categories
        categories_data = [
            {'name': 'Pain Relief', 'description': 'Painkillers and anti-inflammatory medicines'},
            {'name': 'Vitamins & Supplements', 'description': 'Daily vitamins and dietary supplements'},
            {'name': 'Cold & Flu', 'description': 'Medicines for cold, flu, and cough'},
            {'name': 'Digestive Health', 'description': 'Medicines for stomach and digestive issues'},
            {'name': 'First Aid', 'description': 'Bandages, antiseptics, and first aid supplies'},
            {'name': 'Personal Care', 'description': 'Health and hygiene products'},
        ]

        categories = {}
        for cat_data in categories_data:
            category, created = Category.objects.get_or_create(**cat_data)
            categories[category.name] = category
            if created:
                self.stdout.write(f'  + Created category: {category.name}')

        # Create products
        products_data = [
            # Pain Relief
            {
                'name': 'Paracetamol 500mg',
                'description': 'Effective pain relief and fever reducer. Safe for adults and children over 12.',
                'price': 12.50,
                'stock_quantity': 150,
                'category': categories['Pain Relief'],
                'requires_prescription': False,
            },
            {
                'name': 'Ibuprofen 400mg',
                'description': 'Anti-inflammatory pain relief for headaches, muscle pain, and fever.',
                'price': 15.00,
                'stock_quantity': 120,
                'category': categories['Pain Relief'],
                'requires_prescription': False,
            },
            {
                'name': 'Aspirin 100mg',
                'description': 'Low-dose aspirin for heart health and pain relief.',
                'price': 18.00,
                'stock_quantity': 80,
                'category': categories['Pain Relief'],
                'requires_prescription': False,
            },

            # Vitamins & Supplements
            {
                'name': 'Vitamin D3 1000IU',
                'description': 'Essential vitamin D supplement for bone health and immunity. Especially important in Dubai climate.',
                'price': 25.00,
                'stock_quantity': 100,
                'category': categories['Vitamins & Supplements'],
                'requires_prescription': False,
            },
            {
                'name': 'Omega-3 Fish Oil',
                'description': 'High-quality fish oil capsules for heart and brain health.',
                'price': 45.00,
                'stock_quantity': 60,
                'category': categories['Vitamins & Supplements'],
                'requires_prescription': False,
            },
            {
                'name': 'Multivitamin Complex',
                'description': 'Complete daily multivitamin with essential minerals.',
                'price': 35.00,
                'stock_quantity': 90,
                'category': categories['Vitamins & Supplements'],
                'requires_prescription': False,
            },
            {
                'name': 'Vitamin C 1000mg',
                'description': 'High-strength Vitamin C for immune support.',
                'price': 20.00,
                'stock_quantity': 110,
                'category': categories['Vitamins & Supplements'],
                'requires_prescription': False,
            },

            # Cold & Flu
            {
                'name': 'Cold & Flu Relief Tablets',
                'description': 'Multi-symptom relief for cold and flu.',
                'price': 22.00,
                'stock_quantity': 85,
                'category': categories['Cold & Flu'],
                'requires_prescription': False,
            },
            {
                'name': 'Cough Syrup 120ml',
                'description': 'Soothing cough syrup for dry and chesty coughs.',
                'price': 28.00,
                'stock_quantity': 50,
                'category': categories['Cold & Flu'],
                'requires_prescription': False,
            },
            {
                'name': 'Throat Lozenges',
                'description': 'Medicated lozenges for sore throat relief.',
                'price': 15.00,
                'stock_quantity': 95,
                'category': categories['Cold & Flu'],
                'requires_prescription': False,
            },

            # Digestive Health
            {
                'name': 'Antacid Tablets',
                'description': 'Fast relief from heartburn and indigestion.',
                'price': 16.00,
                'stock_quantity': 100,
                'category': categories['Digestive Health'],
                'requires_prescription': False,
            },
            {
                'name': 'Probiotic Capsules',
                'description': 'Supports digestive health and gut flora.',
                'price': 40.00,
                'stock_quantity': 55,
                'category': categories['Digestive Health'],
                'requires_prescription': False,
            },
            {
                'name': 'Laxative Tablets',
                'description': 'Gentle relief for occasional constipation.',
                'price': 18.00,
                'stock_quantity': 70,
                'category': categories['Digestive Health'],
                'requires_prescription': False,
            },

            # First Aid
            {
                'name': 'Adhesive Bandages Box',
                'description': 'Assorted sizes of sterile adhesive bandages.',
                'price': 12.00,
                'stock_quantity': 120,
                'category': categories['First Aid'],
                'requires_prescription': False,
            },
            {
                'name': 'Antiseptic Cream 30g',
                'description': 'Prevents infection in minor cuts and burns.',
                'price': 14.00,
                'stock_quantity': 80,
                'category': categories['First Aid'],
                'requires_prescription': False,
            },
            {
                'name': 'Gauze Pads (Pack of 10)',
                'description': 'Sterile gauze pads for wound care.',
                'price': 10.00,
                'stock_quantity': 90,
                'category': categories['First Aid'],
                'requires_prescription': False,
            },

            # Personal Care
            {
                'name': 'Hand Sanitizer 500ml',
                'description': '70% alcohol hand sanitizer gel.',
                'price': 20.00,
                'stock_quantity': 200,
                'category': categories['Personal Care'],
                'requires_prescription': False,
            },
            {
                'name': 'Digital Thermometer',
                'description': 'Fast and accurate digital thermometer.',
                'price': 35.00,
                'stock_quantity': 45,
                'category': categories['Personal Care'],
                'requires_prescription': False,
            },
        ]

        product_count = 0
        for product_data in products_data:
            product, created = Product.objects.get_or_create(
                name=product_data['name'],
                defaults=product_data
            )
            if created:
                product_count += 1
                self.stdout.write(f'  + Created product: {product.name}')

        self.stdout.write(self.style.SUCCESS(f'\nSuccessfully created {len(categories_data)} categories and {product_count} products!'))
        self.stdout.write(self.style.WARNING('\nNext steps:'))
        self.stdout.write('1. Create a superuser: python manage.py createsuperuser')
        self.stdout.write('2. Run the server: python manage.py runserver')
        self.stdout.write('3. Visit http://127.0.0.1:8000/admin to manage products')
