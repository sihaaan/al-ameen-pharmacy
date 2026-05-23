import os
import re
from pathlib import Path

from django.conf import settings
from django.utils import timezone


SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def should_store_import_sources():
    return bool(getattr(settings, "QUOTATION_IMPORT_STORE_SOURCE_FILES", True))


def private_storage_root():
    configured = getattr(settings, "QUOTATION_PRIVATE_STORAGE_ROOT", None)
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR) / "private_media" / "quotations"


def safe_filename(filename):
    name = Path(filename or "inquiry-upload").name
    name = SAFE_FILENAME_RE.sub("_", name).strip("._")
    return name or "inquiry-upload"


def store_import_source(data, *, filename, sha256):
    if not should_store_import_sources():
        return ""
    date_path = timezone.now().strftime("%Y/%m/%d")
    root = private_storage_root()
    folder = root / "inquiry_sources" / date_path
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = safe_filename(filename)
    storage_name = f"{sha256[:16]}_{safe_name}"
    path = folder / storage_name
    path.write_bytes(data)
    return os.path.relpath(path, root).replace("\\", "/")


def resolve_private_ref(source_file_ref):
    if not source_file_ref:
        return None
    root = private_storage_root().resolve()
    candidate = (root / source_file_ref).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def read_private_ref(source_file_ref):
    path = resolve_private_ref(source_file_ref)
    if not path or not path.exists() or not path.is_file():
        return None
    return path.read_bytes()
