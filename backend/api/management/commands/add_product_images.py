"""
Management command to add product images.
Uses placeholder images or downloads from external sources.

Run with: python manage.py add_product_images
Options:
  --product=SLUG   Add image to specific product
  --all            Add images to all products without images
  --placeholder    Use placeholder images (no external download)
"""
from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from django.db.models import Count
from api.models import Product, ProductImage
import urllib.request
import urllib.error
import hashlib
import time


# Brand color mappings for visually distinct placeholders
BRAND_COLORS = {
    'panadol': '1E88E5',      # Blue
    'abbott': '8E24AA',        # Purple
    'voltaren': 'FF6F00',      # Orange
    'advil': '43A047',         # Green
    'tiger-balm': 'D32F2F',    # Red
    'mentholatum': '00ACC1',   # Cyan
    'strepsils': 'F9A825',     # Amber
    'otrivin': '5E35B1',       # Deep Purple
    'vicks': '1565C0',         # Darker Blue
    'telfast': '00897B',       # Teal
    'claritine': 'C2185B',     # Pink
    'prospan': '558B2F',       # Light Green
    'gaviscon': 'EF6C00',      # Dark Orange
    'rennie': '7B1FA2',        # Purple
    'dulcolax': 'AD1457',      # Dark Pink
    'imodium': '00695C',       # Dark Teal
    'centrum': 'F57C00',       # Orange
    'redoxon': 'FF8F00',       # Amber
    'solgar': '6A1B9A',        # Purple
    'seven-seas': '0277BD',    # Light Blue
    'sunshine-nutrition': 'FFB300',  # Yellow
    'vitabiotics': '2E7D32',   # Green
    'caltrate': '0288D1',      # Blue
    'hansaplast': 'D84315',    # Deep Orange
    '3m-nexcare': 'E53935',    # Red
    'dettol': '388E3C',        # Green
    'betadine': 'BF360C',      # Brown
    'life': '1976D2',          # Blue
    'cetaphil': '26A69A',      # Teal
    'eucerin': '5C6BC0',       # Indigo
    'bioderma': 'EC407A',      # Pink
    'neutrogena': 'FF7043',    # Deep Orange
    'sensodyne': '42A5F5',     # Light Blue
    'listerine': '26C6DA',     # Cyan
    'systane': '7E57C2',       # Deep Purple
    'refresh': '66BB6A',       # Light Green
    'pampers': '29B6F6',       # Light Blue
    'sudocrem': 'FFCA28',      # Amber
    'johnsons': 'FFEE58',      # Yellow
}

# Category icons/colors for fallback
CATEGORY_COLORS = {
    'pain-relief': 'E53935',
    'cold-flu-allergy': '1E88E5',
    'digestive-health': '43A047',
    'vitamins-supplements': 'FF8F00',
    'first-aid': 'D32F2F',
    'skincare': 'EC407A',
    'oral-care': '00ACC1',
    'eye-ear-care': '7E57C2',
    'baby-care': 'FFCA28',
    'personal-care': '8E24AA',
    'sports-nutrition': '00897B',
    'medical-devices': '546E7A',
}


class Command(BaseCommand):
    help = 'Add images to products in the catalog'

    def add_arguments(self, parser):
        parser.add_argument(
            '--product',
            type=str,
            help='Add image to specific product by slug',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Add images to all products without images',
        )
        parser.add_argument(
            '--placeholder',
            action='store_true',
            help='Use placeholder images (default)',
        )

    def handle(self, *args, **options):
        self.stats = {
            'processed': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0,
        }

        if options['product']:
            # Single product
            try:
                product = Product.objects.get(slug=options['product'])
                self.add_image_to_product(product)
            except Product.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Product not found: {options["product"]}'))
                return
        elif options['all']:
            # All products without images
            products = Product.objects.annotate(
                image_count=Count('images')
            ).filter(image_count=0, status='active')

            self.stdout.write(f'Found {products.count()} products without images')

            for product in products:
                self.add_image_to_product(product)
                time.sleep(0.1)  # Be nice to external services
        else:
            self.stdout.write('Please specify --product=SLUG or --all')
            return

        # Summary
        self.stdout.write(self.style.SUCCESS(f'\n=== IMAGE IMPORT SUMMARY ==='))
        self.stdout.write(f'Processed: {self.stats["processed"]}')
        self.stdout.write(f'Success: {self.stats["success"]}')
        self.stdout.write(f'Failed: {self.stats["failed"]}')
        self.stdout.write(f'Skipped: {self.stats["skipped"]}')

    def add_image_to_product(self, product):
        """Add a placeholder image to a product"""
        self.stats['processed'] += 1

        # Check if already has images
        if product.images.exists():
            self.stdout.write(f'  Skipped: {product.name} (already has images)')
            self.stats['skipped'] += 1
            return

        # Generate placeholder image
        try:
            image_content = self.generate_placeholder_image(product)
            if image_content:
                # Create ProductImage record
                filename = f"{product.slug}.png"
                product_image = ProductImage(
                    product=product,
                    alt_text=f"{product.name} product image",
                    is_primary=True,
                    display_order=0,
                    source_type='placeholder',
                )
                product_image.image.save(filename, ContentFile(image_content), save=True)

                self.stdout.write(self.style.SUCCESS(f'  Added image: {product.name}'))
                self.stats['success'] += 1
            else:
                self.stdout.write(self.style.ERROR(f'  Failed: {product.name} (no image data)'))
                self.stats['failed'] += 1
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Failed: {product.name} ({str(e)})'))
            self.stats['failed'] += 1

    def generate_placeholder_image(self, product):
        """Generate a placeholder image using placehold.co"""
        # Determine color based on brand or category
        color = self.get_product_color(product)

        # Create display text (brand + abbreviated name)
        text = self.get_display_text(product)

        # URL encode the text
        import urllib.parse
        encoded_text = urllib.parse.quote(text)

        # Generate placeholder URL
        # Using placehold.co which is reliable and free
        url = f"https://placehold.co/800x800/{color}/white/png?text={encoded_text}"

        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0 (AlAmeen Pharmacy Catalog)'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.read()
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'    Placeholder API failed: {e}'))
            return None

    def get_product_color(self, product):
        """Get appropriate color for product based on brand or category"""
        # Try brand color first
        if product.brand:
            brand_slug = product.brand.slug.lower() if product.brand.slug else ''
            if brand_slug in BRAND_COLORS:
                return BRAND_COLORS[brand_slug]

        # Try category color
        if product.category:
            # Get root category
            cat = product.category
            while cat.parent:
                cat = cat.parent
            cat_slug = cat.slug.lower() if cat.slug else ''
            if cat_slug in CATEGORY_COLORS:
                return CATEGORY_COLORS[cat_slug]

        # Default color (teal - matches site theme)
        return '0D9488'

    def get_display_text(self, product):
        """Get abbreviated text for placeholder image"""
        # Get brand abbreviation
        brand_abbr = ''
        if product.brand:
            brand_name = product.brand.name
            if len(brand_name) <= 8:
                brand_abbr = brand_name
            else:
                # First word or abbreviation
                words = brand_name.split()
                if len(words) > 1:
                    brand_abbr = ''.join(w[0] for w in words[:3])
                else:
                    brand_abbr = brand_name[:6]

        # Get product name abbreviation
        name = product.name
        # Remove brand name from product name if present
        if product.brand:
            name = name.replace(product.brand.name, '').strip()

        # Abbreviate if too long
        if len(name) > 15:
            words = name.split()
            if len(words) > 2:
                name = ' '.join(words[:2])
            else:
                name = name[:15]

        # Combine
        if brand_abbr and name:
            return f"{brand_abbr}\\n{name}"
        elif brand_abbr:
            return brand_abbr
        else:
            return name[:20] if name else "Product"
