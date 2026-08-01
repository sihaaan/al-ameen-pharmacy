import hashlib
import re
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, storages
from django.utils import timezone
from django.utils.functional import cached_property


SAFE_EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,12}$")
PRIVATE_EVIDENCE_REF_PREFIX = "inquiry_sources/v1/"
PRIVATE_EVIDENCE_VERSION_NAMESPACE_RE = re.compile(r"^inquiry_sources/v\d+/")
PRIVATE_EVIDENCE_LEGACY_REF_RE = re.compile(
    r"^inquiry_sources(?:/[A-Za-z0-9._-]+)+$"
)
PRIVATE_EVIDENCE_REF_RE = re.compile(
    r"^inquiry_sources/v1/\d{4}/\d{2}/\d{2}/(?P<sha256>[0-9a-f]{64})(?P<extension>\.[a-z0-9]{1,12})?$"
)
PRIVATE_EVIDENCE_STORAGE_ALIAS = "quotation_evidence"
MAX_PRIVATE_EVIDENCE_REF_LENGTH = 500


class PrivateEvidenceStorageError(OSError):
    """Base error for private evidence that could not be read or written safely."""


class PrivateEvidenceStorageUnavailable(PrivateEvidenceStorageError):
    """The configured private evidence backend failed rather than returning not-found."""


class PrivateEvidenceIntegrityError(PrivateEvidenceStorageError):
    """Stored evidence did not match its content identity or storage contract."""


def should_store_import_sources():
    return bool(getattr(settings, "QUOTATION_IMPORT_STORE_SOURCE_FILES", True))


def private_storage_root():
    configured = getattr(settings, "QUOTATION_PRIVATE_STORAGE_ROOT", None)
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR) / "private_media" / "quotations"


class QuotationEvidenceFileSystemStorage(FileSystemStorage):
    """Private local fallback whose root follows QUOTATION_PRIVATE_STORAGE_ROOT."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("base_url", None)
        kwargs.setdefault("file_permissions_mode", 0o600)
        kwargs.setdefault("directory_permissions_mode", 0o700)
        super().__init__(*args, **kwargs)

    @cached_property
    def base_location(self):
        return self._location if self._location is not None else private_storage_root()

    @cached_property
    def base_url(self):
        return None

    def _clear_cached_properties(self, setting, **kwargs):
        if setting == "QUOTATION_PRIVATE_STORAGE_ROOT":
            self.__dict__.pop("base_location", None)
            self.__dict__.pop("location", None)
        super()._clear_cached_properties(setting, **kwargs)


def get_private_evidence_storage():
    try:
        return storages[PRIVATE_EVIDENCE_STORAGE_ALIAS]
    except Exception as exc:
        raise PrivateEvidenceStorageUnavailable(
            "The private evidence storage backend is not configured correctly."
        ) from exc


def max_private_evidence_bytes():
    return int(
        getattr(
            settings,
            "QUOTATION_PRIVATE_EVIDENCE_MAX_BYTES",
            getattr(settings, "QUOTATION_IMPORT_MAX_UPLOAD_BYTES", 5 * 1024 * 1024),
        )
    )


def _safe_extension(filename):
    extension = Path(filename or "").suffix.lower()
    if SAFE_EXTENSION_RE.fullmatch(extension):
        return extension
    return ".bin"


def _safe_relative_key(value):
    key = str(value or "")
    if (
        not key
        or key != key.strip()
        or len(key) > MAX_PRIVATE_EVIDENCE_REF_LENGTH
        or "\x00" in key
        or "\\" in key
        or ":" in key
        or any(ord(character) < 32 or ord(character) == 127 for character in key)
    ):
        return None
    path = PurePosixPath(key)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    normalized = path.as_posix()
    if normalized != key:
        return None
    return normalized


def _versioned_ref_details(source_file_ref):
    key = _safe_relative_key(source_file_ref)
    if not key or not key.startswith(PRIVATE_EVIDENCE_REF_PREFIX):
        return None
    match = PRIVATE_EVIDENCE_REF_RE.fullmatch(key)
    if not match:
        return None
    return key, match.group("sha256")


def _legacy_ref_key(source_file_ref):
    key = _safe_relative_key(source_file_ref)
    if (
        not key
        or PRIVATE_EVIDENCE_VERSION_NAMESPACE_RE.match(key)
        or not PRIVATE_EVIDENCE_LEGACY_REF_RE.fullmatch(key)
    ):
        return None
    return key


def is_valid_private_ref(source_file_ref):
    if not source_file_ref:
        return True
    return bool(
        _versioned_ref_details(source_file_ref)
        or _legacy_ref_key(source_file_ref)
    )


def _bounded_read(source):
    limit = max_private_evidence_bytes()
    data = source.read(limit + 1)
    if len(data) > limit:
        raise PrivateEvidenceIntegrityError(
            "Private evidence exceeds the configured read limit."
        )
    return data


def _read_storage_key(storage, key):
    try:
        with storage.open(key, "rb") as source:
            return _bounded_read(source)
    except FileNotFoundError:
        return None
    except PrivateEvidenceStorageError:
        raise
    except Exception as exc:
        raise PrivateEvidenceStorageUnavailable(
            "The private evidence storage backend is unavailable."
        ) from exc


def _verify_sha256(data, expected_sha256):
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        raise PrivateEvidenceIntegrityError(
            "Private evidence failed its SHA-256 integrity check."
        )
    return data


def store_import_source(data, *, filename, sha256):
    if not should_store_import_sources():
        return ""

    source_bytes = bytes(data)
    if len(source_bytes) > max_private_evidence_bytes():
        raise PrivateEvidenceIntegrityError(
            "Private evidence exceeds the configured storage limit."
        )
    actual_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if str(sha256 or "").lower() != actual_sha256:
        raise PrivateEvidenceIntegrityError(
            "Source SHA-256 does not match the uploaded evidence bytes."
        )

    date_path = timezone.now().strftime("%Y/%m/%d")
    storage_name = (
        f"{PRIVATE_EVIDENCE_REF_PREFIX}{date_path}/"
        f"{actual_sha256}{_safe_extension(filename)}"
    )
    storage = get_private_evidence_storage()

    try:
        already_exists = storage.exists(storage_name)
    except Exception as exc:
        raise PrivateEvidenceStorageUnavailable(
            "The private evidence storage backend is unavailable."
        ) from exc

    if already_exists:
        existing = _read_storage_key(storage, storage_name)
        if existing is None:
            raise PrivateEvidenceStorageUnavailable(
                "Private evidence disappeared while it was being verified."
            )
        _verify_sha256(existing, actual_sha256)
        return storage_name

    try:
        saved_name = storage.save(storage_name, ContentFile(source_bytes))
    except Exception as exc:
        raise PrivateEvidenceStorageUnavailable(
            "The private evidence storage backend could not save the source."
        ) from exc

    if saved_name != storage_name:
        safe_saved_name = _safe_relative_key(saved_name)
        requested_path = PurePosixPath(storage_name)
        saved_path = PurePosixPath(safe_saved_name) if safe_saved_name else None
        requested_digest = requested_path.name.split(".", 1)[0]
        safe_alternate = bool(
            safe_saved_name
            and safe_saved_name.startswith(PRIVATE_EVIDENCE_REF_PREFIX)
            and saved_path.parent == requested_path.parent
            and saved_path.name.startswith(requested_digest)
        )
        if not safe_alternate:
            raise PrivateEvidenceIntegrityError(
                "The private evidence storage backend returned an unsafe object key."
            )

        # A concurrent content-identical writer may have won the canonical key.
        alternate = _read_storage_key(storage, safe_saved_name)
        if alternate is None:
            raise PrivateEvidenceStorageUnavailable(
                "The private evidence storage backend lost the saved source."
            )
        _verify_sha256(alternate, actual_sha256)
        canonical = _read_storage_key(storage, storage_name)
        if canonical is not None:
            _verify_sha256(canonical, actual_sha256)
            return storage_name
        raise PrivateEvidenceIntegrityError(
            "The private evidence storage backend changed the requested object key."
        )

    saved = _read_storage_key(storage, storage_name)
    if saved is None:
        raise PrivateEvidenceStorageUnavailable(
            "Private evidence was not readable after it was saved."
        )
    _verify_sha256(saved, actual_sha256)
    return storage_name


def resolve_private_ref(source_file_ref):
    """Resolve a safe relative ref beneath the legacy local root, when applicable."""

    key = _safe_relative_key(source_file_ref)
    if not key:
        return None
    if not (_versioned_ref_details(key) or _legacy_ref_key(key)):
        return None
    root = private_storage_root().resolve()
    candidate = (root / key).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def read_private_ref(source_file_ref, *, expected_sha256=""):
    expected_sha256 = str(expected_sha256 or "").lower()
    versioned = _versioned_ref_details(source_file_ref)
    if versioned:
        storage_name, ref_sha256 = versioned
        if expected_sha256 and expected_sha256 != ref_sha256:
            raise PrivateEvidenceIntegrityError(
                "Private evidence reference does not match its recorded SHA-256."
            )
        data = _read_storage_key(get_private_evidence_storage(), storage_name)
        if data is not None:
            return _verify_sha256(data, ref_sha256)

        # During a future provider cutover, a versioned object written by the
        # previous local backend remains a safe fallback after a definite
        # active-backend miss because its full digest is embedded in the key.
        local_path = resolve_private_ref(storage_name)
        if local_path:
            try:
                if local_path.is_file():
                    with local_path.open("rb") as source:
                        return _verify_sha256(_bounded_read(source), ref_sha256)
            except FileNotFoundError:
                pass
            except PrivateEvidenceStorageError:
                raise
            except OSError as exc:
                raise PrivateEvidenceStorageUnavailable(
                    "The previous local private evidence storage is unavailable."
                ) from exc
        return None

    legacy_key = _legacy_ref_key(source_file_ref)
    if not legacy_key:
        return None

    # Existing unversioned refs remain local-first so copying old evidence to a
    # new backend cannot silently replace the original source before cutover.
    path = resolve_private_ref(legacy_key)
    local_integrity_error = None
    if path:
        try:
            if path.is_file():
                with path.open("rb") as source:
                    data = _bounded_read(source)
                if expected_sha256:
                    try:
                        return _verify_sha256(data, expected_sha256)
                    except PrivateEvidenceIntegrityError as exc:
                        local_integrity_error = exc
                else:
                    return data
        except FileNotFoundError:
            pass
        except PrivateEvidenceStorageError:
            raise
        except OSError as exc:
            raise PrivateEvidenceStorageUnavailable(
                "The legacy private evidence storage is unavailable."
            ) from exc

    # A copied legacy object may also exist under its unchanged key in the new
    # backend. Backend failures propagate and are never mistaken for not-found.
    data = _read_storage_key(get_private_evidence_storage(), legacy_key)
    if data is None:
        if local_integrity_error:
            raise local_integrity_error
        return None
    return _verify_sha256(data, expected_sha256) if expected_sha256 else data
