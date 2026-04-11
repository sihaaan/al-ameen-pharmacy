# Data migration to populate v2 fields from legacy data

from django.db import migrations
from django.utils.text import slugify


def migrate_product_data(apps, schema_editor):
    """Copy description to short_description, generate slugs."""
    Product = apps.get_model('api', 'Product')

    for product in Product.objects.all():
        # Copy description to short_description if not set
        if not product.short_description and product.description:
            product.short_description = product.description

        # Generate slug if not set
        if not product.slug:
            base_slug = slugify(product.name)
            slug = base_slug
            counter = 1
            while Product.objects.filter(slug=slug).exclude(pk=product.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            product.slug = slug

        product.save()


def migrate_category_data(apps, schema_editor):
    """Generate slugs for categories."""
    Category = apps.get_model('api', 'Category')

    for category in Category.objects.all():
        if not category.slug:
            base_slug = slugify(category.name)
            slug = base_slug
            counter = 1
            while Category.objects.filter(slug=slug).exclude(pk=category.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            category.slug = slug
            category.save()


def reverse_migration(apps, schema_editor):
    """No-op reverse - we don't want to lose data."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0002_upgrade_to_v2'),
    ]

    operations = [
        migrations.RunPython(migrate_product_data, reverse_migration),
        migrations.RunPython(migrate_category_data, reverse_migration),
    ]
