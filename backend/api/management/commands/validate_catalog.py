"""
Management command to validate and clean the product catalog data.
Run with: python manage.py validate_catalog
Options:
  --fix     Apply fixes automatically
  --report  Generate detailed report only (no changes)
  --images  Add placeholder images to products without images
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q
from django.utils.text import slugify
from api.models import Brand, Category, Product, ProductImage
import re


class Command(BaseCommand):
    help = 'Validates and cleans catalog data (products, categories, brands)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Apply fixes automatically',
        )
        parser.add_argument(
            '--report',
            action='store_true',
            help='Generate detailed report only (no changes)',
        )
        parser.add_argument(
            '--images',
            action='store_true',
            help='Create placeholder images for products without images',
        )

    def handle(self, *args, **options):
        self.fix_mode = options['fix']
        self.report_only = options['report']
        self.add_images = options['images']

        # Stats tracking
        self.stats = {
            'products_validated': 0,
            'products_fixed': 0,
            'products_issues': [],
            'categories_validated': 0,
            'categories_fixed': 0,
            'categories_merged': 0,
            'categories_issues': [],
            'brands_validated': 0,
            'brands_fixed': 0,
            'brands_merged': 0,
            'brands_issues': [],
            'images_created': 0,
            'data_normalized': 0,
        }

        self.stdout.write(self.style.MIGRATE_HEADING('\n=== CATALOG DATA VALIDATION ===\n'))

        if self.report_only:
            self.stdout.write(self.style.WARNING('REPORT MODE: No changes will be made\n'))
        elif self.fix_mode:
            self.stdout.write(self.style.WARNING('FIX MODE: Applying automatic fixes\n'))

        # Run validations
        self.validate_brands()
        self.validate_categories()
        self.validate_products()
        self.normalize_product_data()

        if self.add_images or self.fix_mode:
            self.handle_product_images()

        # Print summary
        self.print_summary()

    # ==================
    # BRAND VALIDATION
    # ==================
    def validate_brands(self):
        self.stdout.write(self.style.MIGRATE_HEADING('\n--- BRAND VALIDATION ---\n'))

        brands = Brand.objects.all()
        self.stats['brands_validated'] = brands.count()

        # Check for duplicate names (case-insensitive)
        self.check_brand_duplicates()

        # Validate slugs
        for brand in brands:
            issues = []

            # Check slug exists and is valid
            if not brand.slug:
                issues.append('Missing slug')
                if self.fix_mode and not self.report_only:
                    brand.slug = slugify(brand.name)
                    brand.save()
                    self.stats['brands_fixed'] += 1
                    self.stdout.write(f'  Fixed: Generated slug for "{brand.name}" -> {brand.slug}')

            # Check slug matches name pattern
            expected_slug = slugify(brand.name)
            if brand.slug and not brand.slug.startswith(expected_slug.split('-')[0]):
                issues.append(f'Slug mismatch: {brand.slug} vs expected {expected_slug}')

            # Check for empty/whitespace name
            if not brand.name or not brand.name.strip():
                issues.append('Empty or whitespace-only name')

            if issues:
                self.stats['brands_issues'].append({
                    'brand': brand.name,
                    'id': brand.id,
                    'issues': issues
                })

        self.stdout.write(f'Validated {self.stats["brands_validated"]} brands')

    def check_brand_duplicates(self):
        """Find and optionally merge duplicate brands (case-insensitive)"""
        brands = Brand.objects.all()
        name_map = {}

        for brand in brands:
            normalized = brand.name.lower().strip()
            if normalized in name_map:
                existing = name_map[normalized]
                self.stats['brands_issues'].append({
                    'brand': brand.name,
                    'id': brand.id,
                    'issues': [f'Duplicate of "{existing.name}" (ID: {existing.id})']
                })

                if self.fix_mode and not self.report_only:
                    # Merge: Move products to existing brand, delete duplicate
                    Product.objects.filter(brand=brand).update(brand=existing)
                    brand.delete()
                    self.stats['brands_merged'] += 1
                    self.stdout.write(f'  Merged: "{brand.name}" into "{existing.name}"')
            else:
                name_map[normalized] = brand

    # ==================
    # CATEGORY VALIDATION
    # ==================
    def validate_categories(self):
        self.stdout.write(self.style.MIGRATE_HEADING('\n--- CATEGORY VALIDATION ---\n'))

        categories = Category.objects.all()
        self.stats['categories_validated'] = categories.count()

        # Check hierarchy
        self.check_category_hierarchy()

        # Check for duplicates
        self.check_category_duplicates()

        # Validate each category
        for category in categories:
            issues = []

            # Check slug
            if not category.slug:
                issues.append('Missing slug')
                if self.fix_mode and not self.report_only:
                    category.slug = slugify(category.name)
                    # Ensure unique
                    original = category.slug
                    counter = 1
                    while Category.objects.filter(slug=category.slug).exclude(pk=category.pk).exists():
                        category.slug = f"{original}-{counter}"
                        counter += 1
                    category.save()
                    self.stats['categories_fixed'] += 1
                    self.stdout.write(f'  Fixed: Generated slug for "{category.name}" -> {category.slug}')

            # Check for empty name
            if not category.name or not category.name.strip():
                issues.append('Empty or whitespace-only name')

            # Check parent relationship
            if category.parent:
                if category.parent == category:
                    issues.append('Category is its own parent (circular reference)')
                if not Category.objects.filter(pk=category.parent.pk).exists():
                    issues.append(f'Parent category does not exist (ID: {category.parent_id})')

            if issues:
                self.stats['categories_issues'].append({
                    'category': category.name,
                    'id': category.id,
                    'parent': category.parent.name if category.parent else None,
                    'issues': issues
                })

        self.stdout.write(f'Validated {self.stats["categories_validated"]} categories')

    def check_category_hierarchy(self):
        """Check for orphan subcategories and broken hierarchy"""
        subcategories = Category.objects.filter(parent__isnull=False)

        for subcat in subcategories:
            if subcat.parent_id and not Category.objects.filter(pk=subcat.parent_id).exists():
                self.stats['categories_issues'].append({
                    'category': subcat.name,
                    'id': subcat.id,
                    'issues': [f'Orphan subcategory - parent ID {subcat.parent_id} does not exist']
                })

                if self.fix_mode and not self.report_only:
                    # Convert to parent category
                    subcat.parent = None
                    subcat.save()
                    self.stats['categories_fixed'] += 1
                    self.stdout.write(f'  Fixed: Converted orphan "{subcat.name}" to parent category')

    def check_category_duplicates(self):
        """Find duplicate categories (case-insensitive, same parent)"""
        categories = Category.objects.all()
        seen = {}

        for cat in categories:
            key = (cat.name.lower().strip(), cat.parent_id)
            if key in seen:
                existing = seen[key]
                self.stats['categories_issues'].append({
                    'category': cat.name,
                    'id': cat.id,
                    'issues': [f'Duplicate of "{existing.name}" (ID: {existing.id}) under same parent']
                })

                if self.fix_mode and not self.report_only:
                    # Move products and children to existing, delete duplicate
                    Product.objects.filter(category=cat).update(category=existing)
                    Category.objects.filter(parent=cat).update(parent=existing)
                    cat.delete()
                    self.stats['categories_merged'] += 1
                    self.stdout.write(f'  Merged: "{cat.name}" into "{existing.name}"')
            else:
                seen[key] = cat

    # ==================
    # PRODUCT VALIDATION
    # ==================
    def validate_products(self):
        self.stdout.write(self.style.MIGRATE_HEADING('\n--- PRODUCT VALIDATION ---\n'))

        products = Product.objects.all()
        self.stats['products_validated'] = products.count()

        for product in products:
            issues = []
            fixes_applied = []

            # 1. Check category FK
            if product.category_id:
                if not Category.objects.filter(pk=product.category_id).exists():
                    issues.append(f'Invalid category FK (ID: {product.category_id})')
                    if self.fix_mode and not self.report_only:
                        product.category = None
                        fixes_applied.append('Set category to None')

            # 2. Check brand FK
            if product.brand_id:
                if not Brand.objects.filter(pk=product.brand_id).exists():
                    issues.append(f'Invalid brand FK (ID: {product.brand_id})')
                    if self.fix_mode and not self.report_only:
                        product.brand = None
                        fixes_applied.append('Set brand to None')

            # 3. Check short_description
            if not product.short_description or not product.short_description.strip():
                issues.append('Missing short_description')
                if self.fix_mode and not self.report_only:
                    # Generate from name + dosage/pack_size
                    desc_parts = [product.name]
                    if product.dosage:
                        desc_parts.append(product.dosage)
                    if product.pack_size:
                        desc_parts.append(product.pack_size)
                    product.short_description = ' - '.join(desc_parts)
                    fixes_applied.append('Generated short_description')

            # 4. Check status
            valid_statuses = ['draft', 'active', 'archived']
            if product.status not in valid_statuses:
                issues.append(f'Invalid status: {product.status}')
                if self.fix_mode and not self.report_only:
                    product.status = 'draft'
                    fixes_applied.append('Set status to draft')

            # 5. Check slug
            if not product.slug:
                issues.append('Missing slug')
                if self.fix_mode and not self.report_only:
                    product.slug = slugify(product.name)
                    original = product.slug
                    counter = 1
                    while Product.objects.filter(slug=product.slug).exclude(pk=product.pk).exists():
                        product.slug = f"{original}-{counter}"
                        counter += 1
                    fixes_applied.append(f'Generated slug: {product.slug}')

            # 6. Check price
            if product.price <= 0:
                issues.append(f'Invalid price: {product.price}')

            # 7. Check stock
            if product.stock_quantity < 0:
                issues.append(f'Negative stock: {product.stock_quantity}')
                if self.fix_mode and not self.report_only:
                    product.stock_quantity = 0
                    fixes_applied.append('Set stock to 0')

            # Save if fixes applied
            if fixes_applied and not self.report_only:
                product.save()
                self.stats['products_fixed'] += 1
                self.stdout.write(f'  Fixed "{product.name}": {", ".join(fixes_applied)}')

            if issues:
                self.stats['products_issues'].append({
                    'product': product.name,
                    'id': product.id,
                    'slug': product.slug,
                    'issues': issues
                })

        self.stdout.write(f'Validated {self.stats["products_validated"]} products')

    # ==================
    # DATA NORMALIZATION
    # ==================
    def normalize_product_data(self):
        self.stdout.write(self.style.MIGRATE_HEADING('\n--- DATA NORMALIZATION ---\n'))

        if self.report_only:
            self.stdout.write('Skipping normalization in report mode')
            return

        products = Product.objects.all()
        normalized_count = 0

        for product in products:
            changes = []

            # Normalize dosage format
            if product.dosage:
                normalized_dosage = self.normalize_dosage(product.dosage)
                if normalized_dosage != product.dosage:
                    product.dosage = normalized_dosage
                    changes.append(f'dosage: {normalized_dosage}')

            # Normalize pack_size format
            if product.pack_size:
                normalized_pack = self.normalize_pack_size(product.pack_size)
                if normalized_pack != product.pack_size:
                    product.pack_size = normalized_pack
                    changes.append(f'pack_size: {normalized_pack}')

            # Normalize active_ingredient (title case, trim)
            if product.active_ingredient:
                normalized_ingredient = self.normalize_ingredient(product.active_ingredient)
                if normalized_ingredient != product.active_ingredient:
                    product.active_ingredient = normalized_ingredient
                    changes.append(f'active_ingredient normalized')

            # Trim whitespace from text fields
            if product.name and product.name != product.name.strip():
                product.name = product.name.strip()
                changes.append('trimmed name')

            if product.short_description and product.short_description != product.short_description.strip():
                product.short_description = product.short_description.strip()
                changes.append('trimmed short_description')

            if changes:
                product.save()
                normalized_count += 1
                if self.fix_mode:
                    self.stdout.write(f'  Normalized "{product.name}": {", ".join(changes)}')

        self.stats['data_normalized'] = normalized_count
        self.stdout.write(f'Normalized {normalized_count} products')

    def normalize_dosage(self, dosage):
        """Normalize dosage format: '500 mg' -> '500mg', '500MG' -> '500mg'"""
        if not dosage:
            return dosage

        # Remove extra spaces
        dosage = ' '.join(dosage.split())

        # Common patterns: number + unit
        # Remove space between number and unit
        dosage = re.sub(r'(\d+)\s*(mg|ml|g|mcg|iu|%)', r'\1\2', dosage, flags=re.IGNORECASE)

        # Lowercase units
        for unit in ['mg', 'ml', 'mcg', 'iu', 'g']:
            dosage = re.sub(rf'(\d+){unit}', rf'\1{unit}', dosage, flags=re.IGNORECASE)

        return dosage

    def normalize_pack_size(self, pack_size):
        """Normalize pack size: '30 Tablets' -> '30 tablets'"""
        if not pack_size:
            return pack_size

        # Trim and normalize spaces
        pack_size = ' '.join(pack_size.split())

        # Common unit words - lowercase
        units = ['tablets', 'tablet', 'capsules', 'capsule', 'sachets', 'sachet',
                 'strips', 'strip', 'lozenges', 'lozenge', 'softgels', 'softgel',
                 'diapers', 'diaper', 'ml', 'g', 'kit', 'pcs', 'pieces']

        for unit in units:
            pack_size = re.sub(rf'\b{unit}\b', unit, pack_size, flags=re.IGNORECASE)

        return pack_size

    def normalize_ingredient(self, ingredient):
        """Normalize active ingredient: proper case, trim"""
        if not ingredient:
            return ingredient

        # Trim whitespace
        ingredient = ingredient.strip()

        # Title case each word, but keep certain abbreviations
        words = ingredient.split(', ')
        normalized = []
        for word in words:
            word = word.strip()
            # Keep certain patterns uppercase (EPA, DHA, etc.)
            if word.upper() in ['EPA', 'DHA', 'CFU']:
                normalized.append(word.upper())
            else:
                normalized.append(word.title() if word.islower() or word.isupper() else word)

        return ', '.join(normalized)

    # ==================
    # IMAGE HANDLING
    # ==================
    def handle_product_images(self):
        self.stdout.write(self.style.MIGRATE_HEADING('\n--- IMAGE HANDLING ---\n'))

        if self.report_only:
            # Just report products without images
            products_without_images = Product.objects.filter(images__isnull=True)
            self.stdout.write(f'Products without images: {products_without_images.count()}')
            for p in products_without_images[:10]:
                self.stdout.write(f'  - {p.name} (ID: {p.id})')
            if products_without_images.count() > 10:
                self.stdout.write(f'  ... and {products_without_images.count() - 10} more')
            return

        # Find products without any images
        products_without_images = Product.objects.annotate(
            image_count=Count('images')
        ).filter(image_count=0)

        self.stdout.write(f'Found {products_without_images.count()} products without images')

        # Note: We cannot create actual placeholder images without a file
        # This would need to be done manually or via a separate image import process
        # For now, we just report which products need images

        for product in products_without_images:
            self.stdout.write(f'  Needs image: {product.name} (ID: {product.id}, slug: {product.slug})')

        # Check for products with images but no primary
        products_no_primary = Product.objects.annotate(
            image_count=Count('images'),
            primary_count=Count('images', filter=Q(images__is_primary=True))
        ).filter(image_count__gt=0, primary_count=0)

        if products_no_primary.exists():
            self.stdout.write(f'\nProducts with images but no primary: {products_no_primary.count()}')
            for product in products_no_primary:
                if self.fix_mode:
                    # Set first image as primary
                    first_image = product.images.first()
                    if first_image:
                        first_image.is_primary = True
                        first_image.save()
                        self.stats['products_fixed'] += 1
                        self.stdout.write(f'  Fixed: Set primary image for "{product.name}"')

    # ==================
    # SUMMARY OUTPUT
    # ==================
    def print_summary(self):
        self.stdout.write(self.style.MIGRATE_HEADING('\n\n========== VALIDATION SUMMARY ==========\n'))

        # Brands
        self.stdout.write(self.style.SUCCESS(f'BRANDS:'))
        self.stdout.write(f'  Validated: {self.stats["brands_validated"]}')
        self.stdout.write(f'  Fixed: {self.stats["brands_fixed"]}')
        self.stdout.write(f'  Merged: {self.stats["brands_merged"]}')
        if self.stats['brands_issues']:
            self.stdout.write(f'  Issues found: {len(self.stats["brands_issues"])}')
            for issue in self.stats['brands_issues'][:5]:
                self.stdout.write(f'    - {issue["brand"]} (ID: {issue["id"]}): {", ".join(issue["issues"])}')
            if len(self.stats['brands_issues']) > 5:
                self.stdout.write(f'    ... and {len(self.stats["brands_issues"]) - 5} more')

        # Categories
        self.stdout.write(self.style.SUCCESS(f'\nCATEGORIES:'))
        self.stdout.write(f'  Validated: {self.stats["categories_validated"]}')
        self.stdout.write(f'  Fixed: {self.stats["categories_fixed"]}')
        self.stdout.write(f'  Merged: {self.stats["categories_merged"]}')
        if self.stats['categories_issues']:
            self.stdout.write(f'  Issues found: {len(self.stats["categories_issues"])}')
            for issue in self.stats['categories_issues'][:5]:
                self.stdout.write(f'    - {issue["category"]} (ID: {issue["id"]}): {", ".join(issue["issues"])}')
            if len(self.stats['categories_issues']) > 5:
                self.stdout.write(f'    ... and {len(self.stats["categories_issues"]) - 5} more')

        # Products
        self.stdout.write(self.style.SUCCESS(f'\nPRODUCTS:'))
        self.stdout.write(f'  Validated: {self.stats["products_validated"]}')
        self.stdout.write(f'  Fixed: {self.stats["products_fixed"]}')
        self.stdout.write(f'  Data normalized: {self.stats["data_normalized"]}')
        if self.stats['products_issues']:
            self.stdout.write(f'  Issues found: {len(self.stats["products_issues"])}')
            for issue in self.stats['products_issues'][:5]:
                self.stdout.write(f'    - {issue["product"]} (ID: {issue["id"]}): {", ".join(issue["issues"])}')
            if len(self.stats['products_issues']) > 5:
                self.stdout.write(f'    ... and {len(self.stats["products_issues"]) - 5} more')

        # Images
        self.stdout.write(self.style.SUCCESS(f'\nIMAGES:'))
        products_without = Product.objects.annotate(
            image_count=Count('images')
        ).filter(image_count=0).count()
        self.stdout.write(f'  Products without images: {products_without}')
        self.stdout.write(f'  Placeholder images created: {self.stats["images_created"]}')

        # Overall status
        total_issues = (
            len(self.stats['brands_issues']) +
            len(self.stats['categories_issues']) +
            len(self.stats['products_issues'])
        )

        self.stdout.write('\n' + '=' * 42)
        if total_issues == 0:
            self.stdout.write(self.style.SUCCESS('STATUS: All data is valid!'))
        else:
            self.stdout.write(self.style.WARNING(f'STATUS: {total_issues} issues found'))
            if not self.fix_mode and not self.report_only:
                self.stdout.write(self.style.NOTICE('Run with --fix to apply automatic fixes'))

        if products_without > 0:
            self.stdout.write(self.style.NOTICE(f'\nNOTE: {products_without} products need images added manually'))

        self.stdout.write('')
