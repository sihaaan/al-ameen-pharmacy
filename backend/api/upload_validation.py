from pathlib import Path

from django.conf import settings
from rest_framework import serializers


SAFE_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
SAFE_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}


def validate_image_upload(image, *, label="Image", max_bytes_setting="PRODUCT_IMAGE_MAX_UPLOAD_BYTES"):
    if not image:
        return image

    max_bytes = int(getattr(settings, max_bytes_setting, 2 * 1024 * 1024))
    if image.size > max_bytes:
        max_mb = max(1, max_bytes // (1024 * 1024))
        raise serializers.ValidationError(f"{label} is too large. Maximum size is {max_mb} MB.")

    extension = Path(getattr(image, "name", "") or "").suffix.lower().lstrip(".")
    if extension not in SAFE_IMAGE_EXTENSIONS:
        raise serializers.ValidationError(f"Unsupported {label.lower()} type. Upload png, jpg, jpeg, or webp only.")

    content_type = getattr(image, "content_type", "")
    if content_type and content_type not in SAFE_IMAGE_CONTENT_TYPES:
        raise serializers.ValidationError(f"Unsupported {label.lower()} content type.")

    header = image.read(512)
    image.seek(0)
    if extension == "webp":
        looks_valid = header.startswith(b"RIFF") and b"WEBP" in header[:16]
    elif extension == "png":
        looks_valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    else:
        looks_valid = header.startswith(b"\xff\xd8\xff")
    if not looks_valid:
        raise serializers.ValidationError(f"Uploaded {label.lower()} does not look like a valid image.")

    return image
