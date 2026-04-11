# Migration to upgrade from legacy schema to v2
# This adds new tables and columns for the v2 schema

from django.db import migrations, models
import django.db.models.deletion
import django.core.validators
from decimal import Decimal


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial_legacy'),
    ]

    operations = [
        # ==================
        # CREATE NEW TABLES
        # ==================

        # Brand table
        migrations.CreateModel(
            name='Brand',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, unique=True)),
                ('slug', models.SlugField(blank=True, max_length=220, unique=True)),
                ('logo', models.ImageField(blank=True, null=True, upload_to='brands/')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),

        # Supplier table
        migrations.CreateModel(
            name='Supplier',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, unique=True)),
                ('slug', models.SlugField(blank=True, max_length=220, unique=True)),
                ('contact_name', models.CharField(blank=True, max_length=200)),
                ('phone', models.CharField(blank=True, max_length=50)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('website', models.URLField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),

        # ==================
        # UPDATE CATEGORY
        # ==================

        # Add slug to Category
        migrations.AddField(
            model_name='category',
            name='slug',
            field=models.SlugField(blank=True, max_length=120, unique=True, null=True),
        ),
        # Add parent for hierarchy
        migrations.AddField(
            model_name='category',
            name='parent',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children', to='api.category'),
        ),
        # Add is_active
        migrations.AddField(
            model_name='category',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
        # Add display_order
        migrations.AddField(
            model_name='category',
            name='display_order',
            field=models.PositiveIntegerField(default=0),
        ),
        # Add updated_at
        migrations.AddField(
            model_name='category',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),

        # ==================
        # UPDATE PRODUCT
        # ==================

        # Add slug
        migrations.AddField(
            model_name='product',
            name='slug',
            field=models.SlugField(blank=True, max_length=220, unique=True, null=True),
        ),
        # Add brand FK
        migrations.AddField(
            model_name='product',
            name='brand',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='products', to='api.brand'),
        ),
        # Add short_description (we'll copy from description later)
        migrations.AddField(
            model_name='product',
            name='short_description',
            field=models.TextField(blank=True, default=''),
        ),
        # Add status
        migrations.AddField(
            model_name='product',
            name='status',
            field=models.CharField(choices=[('draft', 'Draft'), ('active', 'Active'), ('archived', 'Archived')], default='active', max_length=20),
        ),
        # Add is_featured
        migrations.AddField(
            model_name='product',
            name='is_featured',
            field=models.BooleanField(default=False),
        ),
        # Add active_ingredient
        migrations.AddField(
            model_name='product',
            name='active_ingredient',
            field=models.CharField(blank=True, max_length=200),
        ),
        # Add sku
        migrations.AddField(
            model_name='product',
            name='sku',
            field=models.CharField(blank=True, max_length=100),
        ),
        # Add barcode
        migrations.AddField(
            model_name='product',
            name='barcode',
            field=models.CharField(blank=True, max_length=50),
        ),
        # Add requires_manual_review
        migrations.AddField(
            model_name='product',
            name='requires_manual_review',
            field=models.BooleanField(default=False),
        ),
        # Add meta_title
        migrations.AddField(
            model_name='product',
            name='meta_title',
            field=models.CharField(blank=True, max_length=200),
        ),
        # Add meta_description
        migrations.AddField(
            model_name='product',
            name='meta_description',
            field=models.TextField(blank=True),
        ),

        # ==================
        # CREATE ProductImage
        # ==================

        migrations.CreateModel(
            name='ProductImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='products/')),
                ('alt_text', models.CharField(blank=True, max_length=255)),
                ('is_primary', models.BooleanField(default=False)),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('source_type', models.CharField(choices=[('manual_upload', 'Manual Upload'), ('web_scrape', 'Web Scrape'), ('api_import', 'API Import')], default='manual_upload', max_length=20)),
                ('source_url', models.URLField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='images', to='api.product')),
            ],
            options={
                'ordering': ['-is_primary', 'display_order'],
            },
        ),

        # ==================
        # CREATE ProductSupplier
        # ==================

        migrations.CreateModel(
            name='ProductSupplier',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('supplier_sku', models.CharField(blank=True, max_length=100)),
                ('last_purchase_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('is_preferred', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='product_suppliers', to='api.product')),
                ('supplier', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='supplier_products', to='api.supplier')),
            ],
            options={
                'unique_together': {('product', 'supplier')},
            },
        ),

        # Add suppliers M2M to Product
        migrations.AddField(
            model_name='product',
            name='suppliers',
            field=models.ManyToManyField(blank=True, related_name='products', through='api.ProductSupplier', to='api.supplier'),
        ),
    ]
