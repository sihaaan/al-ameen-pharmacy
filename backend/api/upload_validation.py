from io import BytesIO
from pathlib import Path
import warnings

from django.conf import settings
from PIL import Image as PILImage
from PIL import UnidentifiedImageError
from rest_framework import serializers


SAFE_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
SAFE_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
IMAGE_FORMAT_BY_EXTENSION = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "webp": "WEBP",
}
IMAGE_MIME_BY_FORMAT = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}
DEFAULT_MAX_IMAGE_PIXELS = 25_000_000
DEFAULT_MAX_IMAGE_DIMENSION = 12_000


def validate_image_upload(image, *, label="Image", max_bytes_setting="PRODUCT_IMAGE_MAX_UPLOAD_BYTES"):
    if not image:
        return image

    max_bytes = max(1, int(getattr(settings, max_bytes_setting, 2 * 1024 * 1024)))
    try:
        declared_size = int(getattr(image, "size", 0) or 0)
        if declared_size > max_bytes:
            max_mb = max(1, max_bytes // (1024 * 1024))
            raise serializers.ValidationError(
                f"{label} is too large. Maximum size is {max_mb} MB."
            )

        extension = Path(getattr(image, "name", "") or "").suffix.lower().lstrip(".")
        expected_format = IMAGE_FORMAT_BY_EXTENSION.get(extension)
        if not expected_format:
            raise serializers.ValidationError(
                f"Unsupported {label.lower()} type. Upload png, jpg, jpeg, or webp only."
            )

        content_type = str(getattr(image, "content_type", "") or "").split(";", 1)[0].strip().lower()
        if content_type and content_type not in SAFE_IMAGE_CONTENT_TYPES:
            raise serializers.ValidationError(f"Unsupported {label.lower()} content type.")

        try:
            image.seek(0)
            data = image.read(max_bytes + 1)
        except (AttributeError, OSError, ValueError) as exc:
            raise serializers.ValidationError(
                f"Uploaded {label.lower()} could not be read safely."
            ) from exc
        if len(data) > max_bytes:
            max_mb = max(1, max_bytes // (1024 * 1024))
            raise serializers.ValidationError(
                f"{label} is too large. Maximum size is {max_mb} MB."
            )
        if not data:
            raise serializers.ValidationError(f"Uploaded {label.lower()} is empty.")

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", PILImage.DecompressionBombWarning)
                with PILImage.open(BytesIO(data)) as decoded:
                    decoded_format = str(decoded.format or "").upper()
                    width, height = decoded.size
                    frame_count = int(getattr(decoded, "n_frames", 1) or 1)
                    if decoded_format != expected_format:
                        raise serializers.ValidationError(
                            f"Uploaded {label.lower()} content does not match its .{extension} filename."
                        )
                    decoded_mime = IMAGE_MIME_BY_FORMAT.get(decoded_format)
                    if content_type and content_type != decoded_mime:
                        raise serializers.ValidationError(
                            f"Uploaded {label.lower()} content does not match its declared content type."
                        )
                    if width <= 0 or height <= 0:
                        raise serializers.ValidationError(
                            f"Uploaded {label.lower()} has invalid dimensions."
                        )
                    if max(width, height) > DEFAULT_MAX_IMAGE_DIMENSION:
                        raise serializers.ValidationError(
                            f"Uploaded {label.lower()} dimensions are too large."
                        )
                    if width * height > DEFAULT_MAX_IMAGE_PIXELS:
                        raise serializers.ValidationError(
                            f"Uploaded {label.lower()} has too many pixels."
                        )
                    if frame_count != 1:
                        raise serializers.ValidationError(
                            f"Uploaded {label.lower()} must be a single-frame image."
                        )
                    decoded.verify()

                # ``verify`` checks the container, then a fresh decoder loads
                # all pixels so truncated payloads cannot pass on headers alone.
                with PILImage.open(BytesIO(data)) as decoded:
                    decoded.seek(0)
                    decoded.load()
        except serializers.ValidationError:
            raise
        except (PILImage.DecompressionBombError, PILImage.DecompressionBombWarning):
            raise serializers.ValidationError(
                f"Uploaded {label.lower()} exceeds the safe pixel limit."
            )
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise serializers.ValidationError(
                f"Uploaded {label.lower()} is not a complete valid PNG, JPEG, or WebP image."
            ) from exc

        return image
    finally:
        try:
            image.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
