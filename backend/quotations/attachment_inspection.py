"""Bounded, provider-neutral attachment safety and fidelity inspection.

These checks do not try to identify malware and never evaluate spreadsheet
formulas or PDF actions.  Definite container/resource violations fail closed;
business-fidelity indicators are returned as review warnings.
"""

from __future__ import annotations

import base64
import binascii
from io import BytesIO
import math
from pathlib import PurePosixPath
import re
import stat
import zlib
import zipfile

from defusedxml import ElementTree as SafeElementTree
from django.conf import settings
from django.core.exceptions import ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import (
    BooleanObject,
    DictionaryObject,
    IndirectObject,
    NullObject,
    StreamObject,
)


PDF_MIME = "application/pdf"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSB_MIME = "application/vnd.ms-excel.sheet.binary.macroenabled.12"
XLS_MIME = "application/vnd.ms-excel"
OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

GENERIC_MIME_TYPES = {
    "",
    "application/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
    "binary/octet-stream",
}
EXPECTED_MIME_TYPES = {
    ".pdf": {PDF_MIME},
    ".xlsx": {XLSX_MIME},
    ".xlsb": {XLSB_MIME},
    ".xls": {XLS_MIME},
}
OOXML_MAIN_CONTENT_TYPES = {
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        # ISO/IEC 29500 Strict workbooks use this content type. They are still
        # genuine .xlsx packages, so rejecting them would be a false positive.
        "application/vnd.ms-excel.sheet.main+xml",
    },
    ".xlsb": {
        "application/vnd.ms-excel.sheet.binary.macroenabled.main",
    },
}
OOXML_MAIN_PARTS = {
    ".xlsx": "xl/workbook.xml",
    ".xlsb": "xl/workbook.bin",
}
OOXML_VBA_PROJECT_PART = "xl/vbaproject.bin"
OOXML_VBA_PROJECT_CONTENT_TYPES = {
    "application/vnd.ms-office.vbaproject",
}
OOXML_VBA_PROJECT_RELATIONSHIP_TYPES = {
    "http://schemas.microsoft.com/office/2006/relationships/vbaproject",
}

DEFAULT_MAX_ARCHIVE_ENTRIES = 2048
HARD_MAX_ARCHIVE_ENTRIES = 10_000
DEFAULT_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
HARD_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_MEMBER_BYTES = 32 * 1024 * 1024
HARD_MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
SUSPICIOUS_COMPRESSION_RATIO = 250
MIN_RATIO_WARNING_BYTES = 1024 * 1024
MAX_DETAILED_XML_INSPECTION_BYTES = 4 * 1024 * 1024

DEFAULT_MAX_PDF_OBJECTS = 20_000
HARD_MAX_PDF_OBJECTS = 50_000
DEFAULT_MAX_PDF_STREAMS = 5_000
HARD_MAX_PDF_STREAMS = 10_000
DEFAULT_MAX_PDF_DECODED_STREAM_BYTES = 32 * 1024 * 1024
HARD_MAX_PDF_DECODED_STREAM_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_PDF_TOTAL_DECODED_STREAM_BYTES = 64 * 1024 * 1024
HARD_MAX_PDF_TOTAL_DECODED_STREAM_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_PDF_PAGE_DIMENSION_POINTS = 10_000
HARD_MAX_PDF_PAGE_DIMENSION_POINTS = 20_000
DEFAULT_MAX_PDF_PAGE_AREA_POINTS = 16_000_000
HARD_MAX_PDF_PAGE_AREA_POINTS = 32_000_000
DEFAULT_MAX_PDF_RENDER_PIXELS = 25_000_000
HARD_MAX_PDF_RENDER_PIXELS = 50_000_000
DEFAULT_MAX_PDF_IMAGE_PIXELS = 25_000_000
HARD_MAX_PDF_IMAGE_PIXELS = 50_000_000
DEFAULT_MAX_PDF_IMAGE_MASK_PIXELS = 50_000_000
HARD_MAX_PDF_IMAGE_MASK_PIXELS = 50_000_000
DEFAULT_MAX_PDF_PAGE_IMAGE_MASK_PIXELS = 100_000_000
HARD_MAX_PDF_PAGE_IMAGE_MASK_PIXELS = 100_000_000
DEFAULT_MAX_PDF_TOTAL_IMAGE_MASK_PIXELS = 200_000_000
HARD_MAX_PDF_TOTAL_IMAGE_MASK_PIXELS = 200_000_000
DEFAULT_MAX_PDF_TEXT_CHARS_PER_PAGE = 250_000
HARD_MAX_PDF_TEXT_CHARS_PER_PAGE = 1_000_000
DEFAULT_MAX_PDF_TOTAL_TEXT_CHARS = 1_000_000
HARD_MAX_PDF_TOTAL_TEXT_CHARS = 4_000_000
DEFAULT_MAX_PDF_WORDS_PER_PAGE = 50_000
HARD_MAX_PDF_WORDS_PER_PAGE = 200_000
DEFAULT_MAX_PDF_TOTAL_WORDS = 250_000
HARD_MAX_PDF_TOTAL_WORDS = 500_000
DEFAULT_MAX_PDF_TABLE_ROWS = 20_000
HARD_MAX_PDF_TABLE_ROWS = 50_000
DEFAULT_MAX_PDF_TABLE_CELLS = 100_000
HARD_MAX_PDF_TABLE_CELLS = 250_000


class PDFResourceLimitError(ValidationError):
    """A PDF exceeded a deterministic resource boundary.

    Callers must not downgrade this exception to a parser-fallback warning.
    """


def _bounded_setting(name, default, hard_max):
    try:
        configured = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        configured = default
    return min(max(1, configured), hard_max)


def max_archive_entries():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_ARCHIVE_ENTRIES",
        DEFAULT_MAX_ARCHIVE_ENTRIES,
        HARD_MAX_ARCHIVE_ENTRIES,
    )


def max_archive_uncompressed_bytes():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_ARCHIVE_UNCOMPRESSED_BYTES",
        DEFAULT_MAX_ARCHIVE_UNCOMPRESSED_BYTES,
        HARD_MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    )


def max_archive_member_bytes():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_ARCHIVE_MEMBER_BYTES",
        DEFAULT_MAX_ARCHIVE_MEMBER_BYTES,
        HARD_MAX_ARCHIVE_MEMBER_BYTES,
    )


def max_pdf_objects():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_OBJECTS",
        DEFAULT_MAX_PDF_OBJECTS,
        HARD_MAX_PDF_OBJECTS,
    )


def max_pdf_streams():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_STREAMS",
        DEFAULT_MAX_PDF_STREAMS,
        HARD_MAX_PDF_STREAMS,
    )


def max_pdf_decoded_stream_bytes():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_DECODED_STREAM_BYTES",
        DEFAULT_MAX_PDF_DECODED_STREAM_BYTES,
        HARD_MAX_PDF_DECODED_STREAM_BYTES,
    )


def max_pdf_total_decoded_stream_bytes():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_TOTAL_DECODED_STREAM_BYTES",
        DEFAULT_MAX_PDF_TOTAL_DECODED_STREAM_BYTES,
        HARD_MAX_PDF_TOTAL_DECODED_STREAM_BYTES,
    )


def max_pdf_page_dimension_points():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_PAGE_DIMENSION_POINTS",
        DEFAULT_MAX_PDF_PAGE_DIMENSION_POINTS,
        HARD_MAX_PDF_PAGE_DIMENSION_POINTS,
    )


def max_pdf_page_area_points():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_PAGE_AREA_POINTS",
        DEFAULT_MAX_PDF_PAGE_AREA_POINTS,
        HARD_MAX_PDF_PAGE_AREA_POINTS,
    )


def max_pdf_render_pixels():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_RENDER_PIXELS",
        DEFAULT_MAX_PDF_RENDER_PIXELS,
        HARD_MAX_PDF_RENDER_PIXELS,
    )


def max_pdf_image_pixels():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_IMAGE_PIXELS",
        DEFAULT_MAX_PDF_IMAGE_PIXELS,
        HARD_MAX_PDF_IMAGE_PIXELS,
    )


def pdf_image_mask_limits_enabled():
    value = getattr(
        settings,
        "QUOTATION_IMPORT_PDF_IMAGE_MASK_LIMITS_ENABLED",
        True,
    )
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def max_pdf_image_mask_pixels():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_IMAGE_MASK_PIXELS",
        DEFAULT_MAX_PDF_IMAGE_MASK_PIXELS,
        HARD_MAX_PDF_IMAGE_MASK_PIXELS,
    )


def max_pdf_page_image_mask_pixels():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_PAGE_IMAGE_MASK_PIXELS",
        DEFAULT_MAX_PDF_PAGE_IMAGE_MASK_PIXELS,
        HARD_MAX_PDF_PAGE_IMAGE_MASK_PIXELS,
    )


def max_pdf_total_image_mask_pixels():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_TOTAL_IMAGE_MASK_PIXELS",
        DEFAULT_MAX_PDF_TOTAL_IMAGE_MASK_PIXELS,
        HARD_MAX_PDF_TOTAL_IMAGE_MASK_PIXELS,
    )


def max_pdf_text_chars_per_page():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_TEXT_CHARS_PER_PAGE",
        DEFAULT_MAX_PDF_TEXT_CHARS_PER_PAGE,
        HARD_MAX_PDF_TEXT_CHARS_PER_PAGE,
    )


def max_pdf_total_text_chars():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_TOTAL_TEXT_CHARS",
        DEFAULT_MAX_PDF_TOTAL_TEXT_CHARS,
        HARD_MAX_PDF_TOTAL_TEXT_CHARS,
    )


def max_pdf_words_per_page():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_WORDS_PER_PAGE",
        DEFAULT_MAX_PDF_WORDS_PER_PAGE,
        HARD_MAX_PDF_WORDS_PER_PAGE,
    )


def max_pdf_total_words():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_TOTAL_WORDS",
        DEFAULT_MAX_PDF_TOTAL_WORDS,
        HARD_MAX_PDF_TOTAL_WORDS,
    )


def max_pdf_table_rows():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_TABLE_ROWS",
        DEFAULT_MAX_PDF_TABLE_ROWS,
        HARD_MAX_PDF_TABLE_ROWS,
    )


def max_pdf_table_cells():
    return _bounded_setting(
        "QUOTATION_IMPORT_MAX_PDF_TABLE_CELLS",
        DEFAULT_MAX_PDF_TABLE_CELLS,
        HARD_MAX_PDF_TABLE_CELLS,
    )


def max_pdf_inspection_pages():
    configured = max(
        int(getattr(settings, "QUOTATION_IMPORT_MAX_PDF_PAGES", 10)),
        int(getattr(settings, "QUOTATION_AI_PARSE_MAX_PDF_PAGES", 10)),
        int(getattr(settings, "QUOTATION_AI_NATIVE_MAX_PDF_PAGES", 25)),
    )
    return min(max(1, configured), 100)


def validate_pdf_text_count(length, *, current_total=0, page_number=None):
    length = max(0, int(length or 0))
    if page_number is not None and length > max_pdf_text_chars_per_page():
        raise PDFResourceLimitError(
            f"PDF page {page_number} contains too much extracted text to process safely."
        )
    total = int(current_total or 0) + length
    if total > max_pdf_total_text_chars():
        raise PDFResourceLimitError(
            "PDF extracted text exceeds the safe total processing limit."
        )
    return total


def validate_pdf_text_output(value, *, current_total=0, page_number=None):
    """Bound materialized PDF/OCR text before it is joined or row-parsed."""

    return validate_pdf_text_count(
        len(str(value or "")),
        current_total=current_total,
        page_number=page_number,
    )


def validate_pdf_word_output(word_count, *, current_total=0, page_number=None):
    count = max(0, int(word_count or 0))
    if page_number is not None and count > max_pdf_words_per_page():
        raise PDFResourceLimitError(
            f"PDF page {page_number} contains too many positioned words to process safely."
        )
    total = int(current_total or 0) + count
    if total > max_pdf_total_words():
        raise PDFResourceLimitError(
            "PDF positioned-word output exceeds the safe total processing limit."
        )
    return total


def validate_pdf_page_geometry(
    width,
    height,
    *,
    page_number=None,
    user_unit=1,
    render_scale=None,
):
    """Validate effective page geometry before text traversal or rasterization."""

    try:
        width = abs(float(width))
        height = abs(float(height))
        user_unit = abs(float(user_unit or 1))
    except (TypeError, ValueError, OverflowError) as exc:
        raise PDFResourceLimitError("PDF contains invalid page geometry.") from exc
    if not all(math.isfinite(value) and value > 0 for value in (width, height, user_unit)):
        raise PDFResourceLimitError("PDF contains invalid page geometry.")
    effective_width = width * user_unit
    effective_height = height * user_unit
    label = f"PDF page {page_number}" if page_number is not None else "PDF page"
    if max(effective_width, effective_height) > max_pdf_page_dimension_points():
        raise PDFResourceLimitError(
            f"{label} dimensions exceed the safe processing limit."
        )
    area = effective_width * effective_height
    if not math.isfinite(area) or area > max_pdf_page_area_points():
        raise PDFResourceLimitError(
            f"{label} area exceeds the safe processing limit."
        )
    render_pixels = None
    if render_scale is not None:
        try:
            scale = float(render_scale)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PDFResourceLimitError("PDF render scale is invalid.") from exc
        if not math.isfinite(scale) or scale <= 0:
            raise PDFResourceLimitError("PDF render scale is invalid.")
        render_width = math.ceil(effective_width * scale)
        render_height = math.ceil(effective_height * scale)
        render_pixels = render_width * render_height
        if render_pixels > max_pdf_render_pixels():
            raise PDFResourceLimitError(
                f"{label} would exceed the safe raster preview size."
            )
    return {
        "width_points": effective_width,
        "height_points": effective_height,
        "area_points": area,
        "render_pixels": render_pixels,
    }


def _normalized_mime(value):
    return str(value or "").split(";", 1)[0].strip().lower()


def _mime_warnings(extension, declared_mime_type):
    declared = _normalized_mime(declared_mime_type)
    expected = EXPECTED_MIME_TYPES.get(extension, set())
    if not declared or declared in GENERIC_MIME_TYPES or declared in expected:
        return []
    return [
        "The attachment's declared content type does not match its validated "
        f"{extension.lstrip('.').upper()} format. The validated bytes were used; "
        "review the source document before confirming."
    ]


def _local_name(tag):
    return str(tag or "").rsplit("}", 1)[-1]


def _truthy_ooxml(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _protection_enabled(element, *, workbook=False):
    attributes = {
        str(key or "").rsplit("}", 1)[-1].lower(): str(value or "").strip()
        for key, value in element.attrib.items()
    }
    credential_markers = {
        key: value
        for key, value in attributes.items()
        if any(marker in key for marker in ("password", "hashvalue", "saltvalue"))
    }
    if any(credential_markers.values()):
        return True
    enabled_keys = (
        {"lockstructure", "lockwindows", "lockrevision"}
        if workbook
        else {"sheet"}
    )
    return any(_truthy_ooxml(attributes.get(key)) for key in enabled_keys)


def _safe_archive_name(name):
    value = str(name or "")
    if not value or "\\" in value or "\x00" in value:
        return False
    # Some conforming ZIP writers include explicit directory entries. Validate
    # their path without treating the trailing slash as a mismatch.
    comparable = value[:-1] if value.endswith("/") else value
    if not comparable:
        return False
    path = PurePosixPath(comparable)
    return bool(
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.as_posix() == comparable
    )


def _bounded_column_span(element):
    try:
        start = max(1, int(element.attrib.get("min") or 1))
        end = max(start, int(element.attrib.get("max") or start))
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Excel workbook contains malformed worksheet column metadata."
        ) from exc
    return min(16_384, end - start + 1)


def _read_xml_member(archive, info, *, max_bytes=None):
    effective_limit = max_archive_member_bytes()
    if max_bytes is not None:
        effective_limit = min(effective_limit, max(1, int(max_bytes)))
    if info.file_size > effective_limit:
        raise ValidationError(
            "Excel workbook metadata exceeds the safe per-part expansion limit."
        )
    try:
        return SafeElementTree.fromstring(archive.read(info))
    except Exception as exc:
        raise ValidationError(
            "Excel workbook contains malformed or unsafe XML metadata."
        ) from exc


def _content_type_for_part(content_types_root, part_name):
    part_name = f"/{part_name.lstrip('/')}"
    for element in content_types_root:
        if (
            _local_name(element.tag) == "Override"
            and str(element.attrib.get("PartName") or "") == part_name
        ):
            return str(element.attrib.get("ContentType") or "").lower()
    return ""


def _vba_content_type_parts(content_types_root):
    """Return package parts explicitly declared as VBA projects.

    A filename containing ``vba`` is not sufficient evidence of executable
    macro content. OOXML's content-type declaration is authoritative even
    when a producer uses a non-standard part name.
    """

    parts = set()
    for element in content_types_root:
        if _local_name(element.tag) not in {"Default", "Override"}:
            continue
        content_type = str(element.attrib.get("ContentType") or "").strip().lower()
        if content_type not in OOXML_VBA_PROJECT_CONTENT_TYPES:
            continue
        if _local_name(element.tag) == "Override":
            part_name = str(element.attrib.get("PartName") or "").strip().lstrip("/")
            parts.add(part_name.lower() or "<undeclared-vba-project-part>")
        else:
            extension = str(element.attrib.get("Extension") or "").strip().lower()
            parts.add(f"*.{extension}" if extension else "<default-vba-project-part>")
    return parts


def _vba_relationship_targets(archive, infos):
    """Return targets of standard workbook VBA-project relationships."""

    targets = set()
    for info in infos:
        if info.is_dir() or info.filename.lower() != "xl/_rels/workbook.xml.rels":
            continue
        relationships_root = _read_xml_member(
            archive,
            info,
            max_bytes=MAX_DETAILED_XML_INSPECTION_BYTES,
        )
        for element in relationships_root.iter():
            if _local_name(element.tag) != "Relationship":
                continue
            relationship_type = str(element.attrib.get("Type") or "").strip().lower()
            if relationship_type not in OOXML_VBA_PROJECT_RELATIONSHIP_TYPES:
                continue
            target = str(element.attrib.get("Target") or "").strip()
            targets.add(target.lower() or "<missing-vba-project-target>")
    return targets


def _inspect_ooxml_archive(data, extension):
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValidationError(
            f"Invalid Excel file. The upload is not a complete {extension} workbook."
        ) from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > max_archive_entries():
            raise ValidationError(
                "Excel workbook contains too many archive parts to inspect safely."
            )

        names = []
        seen_file_names = set()
        total_uncompressed = 0
        suspicious_ratio_parts = 0
        for info in infos:
            name = str(info.filename or "")
            if not _safe_archive_name(name):
                raise ValidationError(
                    "Excel workbook contains an unsafe archive member name."
                )
            unix_mode = (int(info.external_attr or 0) >> 16) & 0xFFFF
            if unix_mode and stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                raise ValidationError(
                    "Excel workbook contains an unsupported symbolic-link archive member."
                )
            # ZIP producers may emit the same explicit directory record more
            # than once. Directories do not represent OOXML parts, so this is
            # harmless; duplicate file parts remain ambiguous and fail closed.
            if not info.is_dir():
                if name in seen_file_names:
                    raise ValidationError(
                        "Excel workbook contains ambiguous duplicate archive parts."
                    )
                seen_file_names.add(name)
            names.append(name)
            if info.flag_bits & 0x1:
                raise ValidationError(
                    "Password-encrypted Excel archive members are not supported."
                )
            if info.file_size > max_archive_member_bytes():
                raise ValidationError(
                    "Excel workbook contains an archive part that expands beyond the safe limit."
                )
            total_uncompressed += max(0, int(info.file_size or 0))
            if total_uncompressed > max_archive_uncompressed_bytes():
                raise ValidationError(
                    "Excel workbook expands beyond the safe uncompressed-size limit."
                )
            compressed = max(1, int(info.compress_size or 0))
            ratio = int(info.file_size or 0) / compressed
            if (
                int(info.file_size or 0) >= MIN_RATIO_WARNING_BYTES
                and ratio >= SUSPICIOUS_COMPRESSION_RATIO
            ):
                suspicious_ratio_parts += 1

        required_parts = {
            "[Content_Types].xml",
            "_rels/.rels",
            OOXML_MAIN_PARTS[extension],
        }
        if not required_parts.issubset(seen_file_names):
            raise ValidationError(
                f"Invalid Excel file. The upload is not a valid {extension} workbook package."
            )

        content_types_info = archive.getinfo("[Content_Types].xml")
        content_types_root = _read_xml_member(
            archive,
            content_types_info,
            max_bytes=MAX_DETAILED_XML_INSPECTION_BYTES,
        )
        main_type = _content_type_for_part(
            content_types_root,
            OOXML_MAIN_PARTS[extension],
        )
        if main_type not in OOXML_MAIN_CONTENT_TYPES[extension]:
            raise ValidationError(
                "Excel filename extension does not match the workbook package type."
            )

        lowered_names = [name.lower() for name in names]
        lowered_file_names = {
            info.filename.lower()
            for info in infos
            if not info.is_dir()
        }
        exact_vba_parts = {
            name for name in lowered_file_names if name == OOXML_VBA_PROJECT_PART
        }
        declared_vba_parts = _vba_content_type_parts(content_types_root)
        related_vba_targets = _vba_relationship_targets(archive, infos)
        has_vba_project = bool(
            exact_vba_parts or declared_vba_parts or related_vba_targets
        )
        ambiguous_vba_like_parts = sorted(
            name
            for name in lowered_file_names
            if "vba" in name and name not in exact_vba_parts and name not in declared_vba_parts
        )
        if extension == ".xlsx" and has_vba_project:
            raise ValidationError(
                "Macro-enabled workbook content is not accepted inside an .xlsx file."
            )

        embedded_parts = sum(
            name.startswith(("xl/embeddings/", "xl/activex/", "xl/ctrlprops/"))
            for name in lowered_names
        )
        external_link_parts = sum(
            name.startswith("xl/externallinks/")
            or name == "xl/connections.xml"
            for name in lowered_names
        )
        warnings = []
        if suspicious_ratio_parts:
            warnings.append(
                "Workbook contains highly compressed parts. It passed the hard "
                "expansion limits, but staff should review the source carefully."
            )
        if embedded_parts:
            warnings.append(
                "Workbook contains embedded or active objects. They are not used "
                "for row extraction; verify the visible request manually."
            )
        if ambiguous_vba_like_parts:
            warnings.append(
                "Workbook contains VBA-like filenames, but no standard VBA project "
                "part or declaration was found. They are not treated as executable "
                "macros; review the source document if the filenames are unexpected."
            )

        fidelity = {
            "inspection_level": "ooxml_binary_limited" if extension == ".xlsb" else "ooxml_xml",
            "visible_sheet_count": None,
            "hidden_sheet_count": 0,
            "hidden_sheet_names": [],
            "formula_cell_count": 0,
            "formula_without_cached_value_count": 0,
            "error_cell_count": 0,
            "merged_range_count": 0,
            "hidden_row_count": 0,
            "hidden_column_count": 0,
            "date_cell_count": 0,
            "protected_sheet_count": 0,
            "workbook_protected": False,
            "external_link_count": external_link_parts,
            "embedded_object_count": embedded_parts,
            "macro_part_count": 1 if has_vba_project else 0,
            "limited_worksheet_xml_count": 0,
        }

        if extension == ".xlsb":
            warnings.append(
                "Binary .xlsb workbook formulas, hidden rows/columns, and merged "
                "cells cannot be fully inspected. Review every extracted row."
            )
            if has_vba_project:
                warnings.append(
                    "Binary workbook contains macro-capable content. Application "
                    "row extraction does not execute it; review the source manually."
                )
        else:
            workbook_info = archive.getinfo("xl/workbook.xml")
            workbook_root = _read_xml_member(
                archive,
                workbook_info,
                max_bytes=MAX_DETAILED_XML_INSPECTION_BYTES,
            )
            visible_sheets = 0
            hidden_sheet_names = []
            for element in workbook_root.iter():
                local = _local_name(element.tag)
                if local == "sheet":
                    state = str(element.attrib.get("state") or "visible").lower()
                    if state == "visible":
                        visible_sheets += 1
                    else:
                        hidden_sheet_names.append(
                            str(element.attrib.get("name") or "")[:120]
                        )
                elif local == "workbookProtection" and _protection_enabled(
                    element,
                    workbook=True,
                ):
                    fidelity["workbook_protected"] = True
            fidelity["visible_sheet_count"] = visible_sheets
            fidelity["hidden_sheet_count"] = len(hidden_sheet_names)
            fidelity["hidden_sheet_names"] = hidden_sheet_names[:20]

            worksheet_infos = [
                info
                for info in infos
                if info.filename.startswith("xl/worksheets/")
                and info.filename.lower().endswith(".xml")
            ]
            for info in worksheet_infos:
                if info.file_size > MAX_DETAILED_XML_INSPECTION_BYTES:
                    fidelity["limited_worksheet_xml_count"] += 1
                    continue
                root = _read_xml_member(archive, info)
                for element in root.iter():
                    local = _local_name(element.tag)
                    if local == "row" and str(element.attrib.get("hidden") or "").lower() in {"1", "true"}:
                        fidelity["hidden_row_count"] += 1
                    elif local == "col" and str(element.attrib.get("hidden") or "").lower() in {"1", "true"}:
                        fidelity["hidden_column_count"] += _bounded_column_span(element)
                    elif local == "mergeCell":
                        fidelity["merged_range_count"] += 1
                    elif local == "sheetProtection" and _protection_enabled(element):
                        fidelity["protected_sheet_count"] += 1
                    elif local == "c":
                        children = {
                            _local_name(child.tag): child
                            for child in list(element)
                        }
                        if "f" in children:
                            fidelity["formula_cell_count"] += 1
                            cached = children.get("v")
                            if cached is None or cached.text in (None, ""):
                                fidelity["formula_without_cached_value_count"] += 1
                            formula_text = str(children["f"].text or "")
                            if "[" in formula_text and "]" in formula_text:
                                fidelity["external_link_count"] += 1
                        if str(element.attrib.get("t") or "") == "e":
                            fidelity["error_cell_count"] += 1
                        if str(element.attrib.get("t") or "") == "d":
                            fidelity["date_cell_count"] += 1

            if fidelity["formula_cell_count"]:
                warnings.append(
                    "Workbook contains formula cells. Formulas are not recalculated; "
                    "cached results may be stale, so verify quantities and prices."
                )
            if fidelity["formula_without_cached_value_count"]:
                warnings.append(
                    "Some formula cells have no cached result and may appear blank "
                    "during extraction. Complete the missing values manually."
                )
            if fidelity["error_cell_count"]:
                warnings.append(
                    "Workbook contains formula/error cells that may not produce usable values."
                )
            if fidelity["hidden_sheet_count"] or fidelity["hidden_row_count"] or fidelity["hidden_column_count"]:
                warnings.append(
                    "Workbook contains hidden sheets, rows, or columns. Hidden sheets "
                    "are not imported; hidden rows/columns may still affect extracted rows."
                )
            if fidelity["merged_range_count"]:
                warnings.append(
                    "Workbook contains merged cells. Verify that item names, quantities, "
                    "and units stayed aligned in the extracted rows."
                )
            if fidelity["workbook_protected"] or fidelity["protected_sheet_count"]:
                warnings.append(
                    "Workbook or worksheet protection is enabled. Protection is not an "
                    "accuracy guarantee; review the extracted values."
                )
            if fidelity["date_cell_count"]:
                warnings.append(
                    "Workbook contains explicit date cells. Date-formatted identifiers "
                    "may be normalized; verify affected item codes."
                )
            if fidelity["limited_worksheet_xml_count"]:
                warnings.append(
                    "One or more large worksheets passed the hard expansion limits but "
                    "could not receive detailed formula/hidden-cell inspection. Review "
                    "every extracted row."
                )

        if fidelity["external_link_count"]:
            warnings.append(
                "Workbook contains external links or connections. External data is not "
                "refreshed, so cached values may be stale."
            )

        return {
            "warnings": list(dict.fromkeys(warnings)),
            "safety": {
                "container": "ooxml_zip",
                "validated_format": extension.lstrip("."),
                "archive_entry_count": len(infos),
                "archive_uncompressed_bytes": total_uncompressed,
                "suspicious_compression_part_count": suspicious_ratio_parts,
                "hard_limits_applied": True,
            },
            "fidelity": fidelity,
        }


def inspect_spreadsheet_attachment(data, *, extension, declared_mime_type=""):
    extension = str(extension or "").lower()
    warnings = _mime_warnings(extension, declared_mime_type)
    if extension == ".xls":
        if not bytes(data).startswith(OLE_SIGNATURE):
            raise ValidationError(
                "Invalid Excel file. The upload is not a valid .xls workbook container."
            )
        warnings.append(
            "Legacy .xls formulas, hidden content, merged cells, external links, "
            "and macros cannot be fully inspected. Review every extracted row."
        )
        return {
            "warnings": list(dict.fromkeys(warnings)),
            "safety": {
                "container": "ole_compound_file",
                "validated_format": "xls",
                "archive_entry_count": None,
                "archive_uncompressed_bytes": None,
                "suspicious_compression_part_count": None,
                "hard_limits_applied": True,
            },
            "fidelity": {
                "inspection_level": "legacy_binary_limited",
                "visible_sheet_count": None,
                "hidden_sheet_count": None,
                "formula_cell_count": None,
                "merged_range_count": None,
                "external_link_count": None,
            },
        }
    if extension not in {".xlsx", ".xlsb"}:
        raise ValidationError("Unsupported spreadsheet attachment type.")
    report = _inspect_ooxml_archive(bytes(data), extension)
    report["warnings"] = list(
        dict.fromkeys([*warnings, *(report.get("warnings") or [])])
    )
    return report


_PDF_WHITESPACE = frozenset(b"\x00\x09\x0a\x0c\x0d\x20")
_PDF_DELIMITERS = frozenset(b"()<>[]{}/%")
_PDF_SECURITY_DICTIONARY_BYTES = 65_536
_PDF_XREF_CHAIN_LIMIT = 128
_PDF_XREF_AGGREGATE_ENTRY_MULTIPLIER = 8
_PDF_MISSING = object()


class _RawPDFTokens:
    """Small, bounded PDF lexer for security-critical pre-reader metadata.

    It understands comments, strings, delimiters, and ``#xx`` name escapes.
    It intentionally does not interpret arbitrary PDF objects.
    """

    def __init__(self, data, start=0, end=None, *, max_tokens=200_000):
        self.data = bytes(data)
        self.position = max(0, int(start))
        self.end = min(len(self.data), len(self.data) if end is None else int(end))
        self.max_tokens = max(1, int(max_tokens))
        self.tokens_read = 0
        self._lookahead = []

    def _skip_space_and_comments(self):
        while self.position < self.end:
            value = self.data[self.position]
            if value in _PDF_WHITESPACE:
                self.position += 1
                continue
            if value == 0x25:  # % comment through the line ending
                self.position += 1
                while self.position < self.end and self.data[self.position] not in (0x0A, 0x0D):
                    self.position += 1
                continue
            break

    def _read_token(self):
        self._skip_space_and_comments()
        if self.position >= self.end:
            return ("eof", None, self.position, self.position)
        self.tokens_read += 1
        if self.tokens_read > self.max_tokens:
            raise PDFResourceLimitError(
                "PDF cross-reference metadata contains too many tokens to inspect safely."
            )
        start = self.position
        current = self.data[self.position]
        pair = self.data[self.position : self.position + 2]
        if pair in {b"<<", b">>"}:
            self.position += 2
            return ("symbol", pair, start, self.position)
        if current in b"[]":
            self.position += 1
            return ("symbol", bytes([current]), start, self.position)
        if current == 0x2F:  # /
            self.position += 1
            decoded = bytearray()
            while self.position < self.end:
                value = self.data[self.position]
                if value in _PDF_WHITESPACE or value in _PDF_DELIMITERS:
                    break
                if value == 0x23:  # #xx escape in a name
                    if self.position + 2 >= self.end:
                        raise PDFResourceLimitError(
                            "PDF contains an invalid escaped name in security metadata."
                        )
                    escaped = self.data[self.position + 1 : self.position + 3]
                    if not re.fullmatch(rb"[0-9A-Fa-f]{2}", escaped):
                        raise PDFResourceLimitError(
                            "PDF contains an invalid escaped name in security metadata."
                        )
                    decoded.append(int(escaped, 16))
                    self.position += 3
                    continue
                decoded.append(value)
                self.position += 1
            return ("name", bytes(decoded), start, self.position)
        if current == 0x28:  # balanced literal string
            self.position += 1
            depth = 1
            escaped = False
            while self.position < self.end and depth:
                value = self.data[self.position]
                self.position += 1
                if escaped:
                    escaped = False
                elif value == 0x5C:
                    escaped = True
                elif value == 0x28:
                    depth += 1
                elif value == 0x29:
                    depth -= 1
            if depth:
                raise PDFResourceLimitError(
                    "PDF contains an unterminated string in security metadata."
                )
            return ("opaque", b"string", start, self.position)
        if current == 0x3C:  # hexadecimal string (dictionary handled above)
            self.position += 1
            while self.position < self.end and self.data[self.position] != 0x3E:
                self.position += 1
            if self.position >= self.end:
                raise PDFResourceLimitError(
                    "PDF contains an unterminated hexadecimal string in security metadata."
                )
            self.position += 1
            return ("opaque", b"hex", start, self.position)
        if current in b"{}":
            self.position += 1
            return ("symbol", bytes([current]), start, self.position)

        while self.position < self.end:
            value = self.data[self.position]
            if value in _PDF_WHITESPACE or value in _PDF_DELIMITERS:
                break
            self.position += 1
        raw = self.data[start:self.position]
        if not raw:
            raise PDFResourceLimitError(
                "PDF contains an unrecognized delimiter in security metadata."
            )
        if re.fullmatch(rb"[+-]?\d+", raw):
            return ("integer", int(raw), start, self.position)
        return ("keyword", raw, start, self.position)

    def peek(self, distance=0):
        while len(self._lookahead) <= distance:
            self._lookahead.append(self._read_token())
        return self._lookahead[distance]

    def pop(self):
        if self._lookahead:
            return self._lookahead.pop(0)
        return self._read_token()


def _parse_raw_pdf_value(tokens, *, depth=0):
    if depth > 32:
        raise PDFResourceLimitError(
            "PDF security metadata is nested too deeply to inspect safely."
        )
    token = tokens.pop()
    kind, value = token[:2]
    if kind == "integer":
        second = tokens.peek(0)
        third = tokens.peek(1)
        if second[0] == "integer" and third[:2] == ("keyword", b"R"):
            tokens.pop()
            tokens.pop()
            return ("ref", value, second[1])
        return value
    if kind == "name":
        return ("name", value)
    if kind == "symbol" and value == b"[":
        values = []
        while tokens.peek()[:2] != ("symbol", b"]"):
            if tokens.peek()[0] == "eof":
                raise PDFResourceLimitError(
                    "PDF contains an unterminated array in security metadata."
                )
            values.append(_parse_raw_pdf_value(tokens, depth=depth + 1))
            if len(values) > max_pdf_objects() * 4 + 32:
                raise PDFResourceLimitError(
                    "PDF security metadata array is too large to inspect safely."
                )
        tokens.pop()
        return values
    if kind == "symbol" and value == b"<<":
        result = {}
        while tokens.peek()[:2] != ("symbol", b">>"):
            key = tokens.pop()
            if key[0] != "name":
                raise PDFResourceLimitError(
                    "PDF dictionary contains an invalid key in security metadata."
                )
            if key[1] in result:
                raise PDFResourceLimitError(
                    "PDF dictionary contains duplicate security metadata entries."
                )
            result[key[1]] = _parse_raw_pdf_value(tokens, depth=depth + 1)
        closing = tokens.pop()
        return ("dictionary", result, closing[3])
    if kind == "keyword" and value == b"null":
        return None
    if kind == "keyword" and value in {b"true", b"false"}:
        return value == b"true"
    if kind in {"keyword", "opaque"}:
        return (kind, value)
    raise PDFResourceLimitError(
        "PDF contains an unsupported value in security metadata."
    )


def _raw_pdf_object_at(
    data,
    offset,
    *,
    expected_object_id=None,
    expected_generation=None,
):
    offset = int(offset)
    if offset < 0 or offset >= len(data):
        raise PDFResourceLimitError(
            "PDF cross-reference points outside the uploaded file."
        )
    tokens = _RawPDFTokens(
        data,
        offset,
        min(len(data), offset + _PDF_SECURITY_DICTIONARY_BYTES),
        max_tokens=20_000,
    )
    object_id = tokens.pop()
    generation = tokens.pop()
    marker = tokens.pop()
    if (
        object_id[0] != "integer"
        or generation[0] != "integer"
        or marker[:2] != ("keyword", b"obj")
    ):
        raise PDFResourceLimitError(
            "PDF cross-reference object header could not be inspected safely."
        )
    if expected_object_id is not None and object_id[1] != int(expected_object_id):
        raise PDFResourceLimitError(
            "PDF cross-reference points to a mismatched object header."
        )
    if expected_generation is not None and generation[1] != int(expected_generation):
        raise PDFResourceLimitError(
            "PDF cross-reference points to a mismatched object generation."
        )
    value = _parse_raw_pdf_value(tokens)
    return object_id[1], generation[1], value


def _raw_pdf_dictionary(value, *, label):
    if not (isinstance(value, tuple) and len(value) == 3 and value[0] == "dictionary"):
        raise PDFResourceLimitError(f"{label} does not contain a direct dictionary.")
    return value[1], value[2]


def _raw_pdf_name(value):
    if isinstance(value, tuple) and len(value) == 2 and value[0] == "name":
        return value[1]
    return None


def _required_direct_pdf_integer(dictionary, key, *, label, allow_zero=True):
    value = dictionary.get(key, _PDF_MISSING)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PDFResourceLimitError(
            f"{label} uses an indirect, missing, or invalid /{key.decode('ascii')} value."
        )
    if value < 0 or (not allow_zero and value == 0):
        raise PDFResourceLimitError(
            f"{label} declares an invalid /{key.decode('ascii')} value."
        )
    return value


def _optional_direct_pdf_integer(dictionary, key, *, label):
    if key not in dictionary:
        return None
    return _required_direct_pdf_integer(dictionary, key, label=label)


def _security_filter_chain(dictionary, *, label):
    value = dictionary.get(b"Filter", _PDF_MISSING)
    if value is _PDF_MISSING:
        filters = []
    elif _raw_pdf_name(value) is not None:
        try:
            filters = ["/" + _raw_pdf_name(value).decode("ascii", errors="strict")]
        except UnicodeDecodeError as exc:
            raise PDFResourceLimitError(
                f"{label} uses a filter name that cannot be inspected safely."
            ) from exc
    elif isinstance(value, list) and value and all(_raw_pdf_name(item) is not None for item in value):
        try:
            filters = [
                "/" + _raw_pdf_name(item).decode("ascii", errors="strict")
                for item in value
            ]
        except UnicodeDecodeError as exc:
            raise PDFResourceLimitError(
                f"{label} uses a filter name that cannot be inspected safely."
            ) from exc
    else:
        raise PDFResourceLimitError(
            f"{label} uses an indirect or invalid /Filter value."
        )
    aliases = {
        "/A85": "/ASCII85Decode",
        "/AHx": "/ASCIIHexDecode",
        "/Fl": "/FlateDecode",
    }
    canonical = [aliases.get(item, item) for item in filters]
    supported = {
        (),
        ("/ASCII85Decode",),
        ("/ASCIIHexDecode",),
        ("/FlateDecode",),
        ("/ASCII85Decode", "/FlateDecode"),
        ("/ASCIIHexDecode", "/FlateDecode"),
    }
    if tuple(canonical) not in supported:
        raise PDFResourceLimitError(
            f"{label} uses filters that cannot be decoded with a bounded preflight."
        )
    decode_parameters = dictionary.get(b"DecodeParms", _PDF_MISSING)
    if decode_parameters is not _PDF_MISSING and decode_parameters is not None:
        if not (
            isinstance(decode_parameters, list)
            and decode_parameters
            and all(item is None for item in decode_parameters)
        ):
            raise PDFResourceLimitError(
                f"{label} uses decode parameters that cannot be preflighted safely."
            )
    return filters


def _security_stream_bytes(data, dictionary, dictionary_end, *, label, limit):
    encoded_length = _required_direct_pdf_integer(
        dictionary,
        b"Length",
        label=label,
    )
    if encoded_length > int(limit):
        raise PDFResourceLimitError(
            f"{label} encoded length exceeds the safe preflight limit."
        )
    cursor = int(dictionary_end)
    while cursor < len(data):
        if data[cursor] in _PDF_WHITESPACE:
            cursor += 1
            continue
        if data[cursor] == 0x25:
            cursor += 1
            while cursor < len(data) and data[cursor] not in (0x0A, 0x0D):
                cursor += 1
            continue
        break
    if data[cursor : cursor + 6] != b"stream":
        raise PDFResourceLimitError(f"{label} data could not be located safely.")
    cursor += 6
    while cursor < len(data) and data[cursor] in b"\x00\x09\x0c\x20":
        cursor += 1
    if data[cursor : cursor + 2] == b"\r\n":
        cursor += 2
    elif data[cursor : cursor + 1] in {b"\r", b"\n"}:
        cursor += 1
    else:
        raise PDFResourceLimitError(
            f"{label} stream marker is not followed by a valid line ending."
        )
    stream_end = cursor + encoded_length
    if stream_end > len(data):
        raise PDFResourceLimitError(f"{label} length exceeds the uploaded file.")
    endstream_cursor = stream_end
    while (
        endstream_cursor < len(data)
        and data[endstream_cursor] in _PDF_WHITESPACE
    ):
        endstream_cursor += 1
    if data[endstream_cursor : endstream_cursor + 9] != b"endstream":
        raise PDFResourceLimitError(
            f"{label} declared length does not end at an exact endstream marker."
        )
    marker_end = endstream_cursor + 9
    if (
        marker_end < len(data)
        and data[marker_end] not in _PDF_WHITESPACE
        and data[marker_end] not in _PDF_DELIMITERS
    ):
        raise PDFResourceLimitError(
            f"{label} endstream marker is not properly delimited."
        )
    filters = _security_filter_chain(dictionary, label=label)
    try:
        decoded = _bounded_filter_chain_data(
            data[cursor:stream_end],
            filters,
            limit=int(limit),
        )
    except (ValueError, binascii.Error, zlib.error) as exc:
        raise PDFResourceLimitError(
            f"{label} could not be decoded safely during preflight."
        ) from exc
    if decoded is None:
        raise PDFResourceLimitError(
            f"{label} uses filters that cannot be decoded with a bounded preflight."
        )
    if len(decoded) > int(limit):
        raise PDFResourceLimitError(
            f"{label} expands beyond the safe preflight limit."
        )
    return decoded


def _raw_pdf_only_whitespace_and_comments(data):
    cursor = 0
    data = bytes(data)
    while cursor < len(data):
        if data[cursor] in _PDF_WHITESPACE:
            cursor += 1
            continue
        if data[cursor] == 0x25:
            cursor += 1
            while cursor < len(data) and data[cursor] not in (0x0A, 0x0D):
                cursor += 1
            continue
        return False
    return True


def _final_pdf_startxref(data):
    eof_candidates = list(
        re.finditer(
            rb"(?m)^[\x00\x09\x0c\x20]*%%EOF[\x00\x09\x0c\x20]*(?:\r\n|\r|\n|\Z)",
            data,
        )
    )
    final_eof = next(
        (
            candidate
            for candidate in reversed(eof_candidates)
            if _raw_pdf_only_whitespace_and_comments(data[candidate.end() :])
        ),
        None,
    )
    if final_eof is None:
        raise PDFResourceLimitError(
            "PDF final startxref marker could not be inspected safely."
        )
    prefix = data[: final_eof.start()]
    startxref_candidates = list(
        re.finditer(
            rb"(?m)^[\x00\x09\x0c\x20]*startxref[\x00\x09\x0c\x20]*(?:\r\n|\r|\n)"
            rb"[\x00\x09\x0c\x20]*(\d+)[\x00\x09\x0c\x20]*(?:\r\n|\r|\n|\Z)",
            prefix,
        )
    )
    final_startxref = next(
        (
            candidate
            for candidate in reversed(startxref_candidates)
            if _raw_pdf_only_whitespace_and_comments(prefix[candidate.end() :])
        ),
        None,
    )
    if final_startxref is None:
        raise PDFResourceLimitError(
            "PDF final startxref marker could not be inspected safely."
        )
    return int(final_startxref.group(1))


def _classic_xref_at(data, offset):
    max_objects = max_pdf_objects()
    tokens = _RawPDFTokens(
        data,
        offset,
        max_tokens=(max_objects * 4) + 4096,
    )
    if tokens.pop()[:2] != ("keyword", b"xref"):
        raise PDFResourceLimitError(
            "PDF classic cross-reference section could not be inspected safely."
        )
    entries = {}
    declared_entries = 0
    while tokens.peek()[:2] != ("keyword", b"trailer"):
        start = tokens.pop()
        count = tokens.pop()
        if start[0] != "integer" or count[0] != "integer" or start[1] < 0 or count[1] < 0:
            raise PDFResourceLimitError(
                "PDF classic cross-reference subsection is malformed."
            )
        if start[1] + count[1] > max_objects + 1:
            raise PDFResourceLimitError(
                "PDF xref structure declares too many objects to inspect safely."
            )
        declared_entries += count[1]
        if declared_entries > max_objects + 1:
            raise PDFResourceLimitError(
                "PDF xref structure declares too many objects to inspect safely."
            )
        for index in range(count[1]):
            object_offset = tokens.pop()
            generation = tokens.pop()
            state = tokens.pop()
            if (
                object_offset[0] != "integer"
                or generation[0] != "integer"
                or state[0] != "keyword"
                or state[1] not in {b"n", b"f"}
                or object_offset[1] < 0
                or generation[1] < 0
            ):
                raise PDFResourceLimitError(
                    "PDF classic cross-reference entry is malformed."
                )
            object_id = start[1] + index
            if object_id in entries:
                raise PDFResourceLimitError(
                    "PDF classic cross-reference contains duplicate object entries."
                )
            entries[object_id] = (
                1 if state[1] == b"n" else 0,
                object_offset[1],
                generation[1],
            )
    tokens.pop()
    trailer_value = _parse_raw_pdf_value(tokens)
    trailer, _ = _raw_pdf_dictionary(
        trailer_value,
        label="PDF classic cross-reference trailer",
    )
    declared_size = _required_direct_pdf_integer(
        trailer,
        b"Size",
        label="PDF classic cross-reference trailer",
        allow_zero=False,
    )
    if declared_size > max_objects + 1:
        raise PDFResourceLimitError(
            "PDF xref structure declares too many objects to inspect safely."
        )
    return entries, trailer


def _xref_stream_at(data, offset):
    object_id, generation, value = _raw_pdf_object_at(data, offset)
    dictionary, dictionary_end = _raw_pdf_dictionary(
        value,
        label="PDF xref stream",
    )
    if _raw_pdf_name(dictionary.get(b"Type")) != b"XRef":
        raise PDFResourceLimitError(
            "PDF startxref does not point to a recognizable xref structure."
        )
    max_objects = max_pdf_objects()
    declared_size = _required_direct_pdf_integer(
        dictionary,
        b"Size",
        label="PDF xref stream",
        allow_zero=False,
    )
    if declared_size > max_objects + 1:
        raise PDFResourceLimitError(
            "PDF xref structure declares too many objects to inspect safely."
        )
    widths = dictionary.get(b"W", _PDF_MISSING)
    if (
        not isinstance(widths, list)
        or len(widths) != 3
        or any(isinstance(value, bool) or not isinstance(value, int) for value in widths)
        or any(value < 0 or value > 8 for value in widths)
        or sum(widths) <= 0
    ):
        raise PDFResourceLimitError(
            "PDF xref stream does not declare a bounded direct /W array."
        )
    indexes = dictionary.get(b"Index", [0, declared_size])
    if (
        not isinstance(indexes, list)
        or not indexes
        or len(indexes) % 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in indexes)
        or any(value < 0 for value in indexes)
    ):
        raise PDFResourceLimitError(
            "PDF xref stream does not declare a bounded direct /Index array."
        )
    entry_count = 0
    for first, count in zip(indexes[::2], indexes[1::2]):
        if first + count > declared_size:
            raise PDFResourceLimitError(
                "PDF xref stream /Index range exceeds its declared /Size."
            )
        entry_count += count
        if entry_count > max_objects + 1:
            raise PDFResourceLimitError(
                "PDF xref structure declares too many objects to inspect safely."
            )
    xref_limit = min(
        max_pdf_decoded_stream_bytes(),
        (max_objects * 64) + (1024 * 1024),
    )
    decoded = _security_stream_bytes(
        data,
        dictionary,
        dictionary_end,
        label="PDF xref stream",
        limit=xref_limit,
    )
    row_width = sum(widths)
    expected_bytes = entry_count * row_width
    if len(decoded) != expected_bytes:
        raise PDFResourceLimitError(
            "PDF xref stream decoded length does not match its /Index and /W declarations."
        )
    entries = {}
    cursor = 0
    for first, count in zip(indexes[::2], indexes[1::2]):
        for index in range(count):
            fields = []
            for width in widths:
                if width == 0:
                    fields.append(0)
                    continue
                fields.append(int.from_bytes(decoded[cursor : cursor + width], "big"))
                cursor += width
            entry_type = fields[0] if widths[0] else 1
            object_number = first + index
            if object_number in entries:
                raise PDFResourceLimitError(
                    "PDF xref stream /Index ranges contain duplicate object entries."
                )
            if entry_type == 0:
                entries[object_number] = (0, fields[1], fields[2])
            elif entry_type == 1:
                if fields[1] >= len(data):
                    raise PDFResourceLimitError(
                        "PDF xref stream points outside the uploaded file."
                    )
                entries[object_number] = (1, fields[1], fields[2])
            elif entry_type == 2:
                if fields[1] <= 0:
                    raise PDFResourceLimitError(
                        "PDF xref stream contains an invalid object-stream reference."
                    )
                entries[object_number] = (2, fields[1], fields[2])
            else:
                raise PDFResourceLimitError(
                    "PDF xref stream contains an unsupported entry type."
                )
    return object_id, generation, entries, dictionary


def _inspect_raw_object_stream(
    data,
    offset,
    object_id,
    generation,
    *,
    required=False,
):
    _actual_id, _generation, value = _raw_pdf_object_at(
        data,
        offset,
        expected_object_id=object_id,
        expected_generation=generation,
    )
    if not (isinstance(value, tuple) and len(value) == 3 and value[0] == "dictionary"):
        if required:
            raise PDFResourceLimitError(
                "PDF compressed-object entry does not reference an object stream."
            )
        return 0, None
    dictionary, dictionary_end = value[1], value[2]
    is_object_stream = _raw_pdf_name(dictionary.get(b"Type")) == b"ObjStm"
    if not is_object_stream and not required:
        return 0, None
    if not is_object_stream:
        raise PDFResourceLimitError(
            "PDF compressed-object entry does not reference a direct /ObjStm stream."
        )
    object_count = _required_direct_pdf_integer(
        dictionary,
        b"N",
        label="PDF object stream",
    )
    first_offset = _required_direct_pdf_integer(
        dictionary,
        b"First",
        label="PDF object stream",
    )
    if object_count > max_pdf_objects():
        raise PDFResourceLimitError(
            "PDF object stream declares too many compressed objects to inspect safely."
        )
    decoded = _security_stream_bytes(
        data,
        dictionary,
        dictionary_end,
        label="PDF object stream",
        limit=max_pdf_decoded_stream_bytes(),
    )
    if first_offset > len(decoded):
        raise PDFResourceLimitError(
            "PDF object stream declares an invalid /First offset."
        )
    header_tokens = _RawPDFTokens(
        decoded,
        0,
        first_offset,
        max_tokens=(object_count * 2) + 8,
    )
    previous_relative_offset = -1
    header_entries = []
    header_object_ids = set()
    for _ in range(object_count):
        compressed_id = header_tokens.pop()
        relative_offset = header_tokens.pop()
        if (
            compressed_id[0] != "integer"
            or relative_offset[0] != "integer"
            or compressed_id[1] <= 0
            or relative_offset[1] < 0
            or relative_offset[1] < previous_relative_offset
            or first_offset + relative_offset[1] > len(decoded)
        ):
            raise PDFResourceLimitError(
                "PDF object stream header could not be inspected safely."
            )
        if compressed_id[1] in header_object_ids:
            raise PDFResourceLimitError(
                "PDF object stream header contains duplicate object identifiers."
            )
        header_object_ids.add(compressed_id[1])
        header_entries.append((compressed_id[1], relative_offset[1]))
        previous_relative_offset = relative_offset[1]
    if header_tokens.peek()[0] != "eof":
        raise PDFResourceLimitError(
            "PDF object stream /First value does not match its object header."
        )
    return len(decoded), header_entries


def _preflight_pdf_xref_structures(data):
    """Validate reachable xref/object streams before ``PdfReader`` can decode them."""

    data = bytes(data)
    queue = [(_final_pdf_startxref(data), frozenset())]
    visited_offsets = set()
    direct_object_entries = set()
    compressed_object_entries = set()
    checked_xref_streams = 0
    checked_classic_sections = 0
    aggregate_xref_entries = 0

    while queue:
        offset, ancestors = queue.pop(0)
        offset = int(offset)
        if offset in ancestors:
            raise PDFResourceLimitError(
                "PDF cross-reference chain contains a cycle."
            )
        if offset in visited_offsets:
            continue
        if len(visited_offsets) >= _PDF_XREF_CHAIN_LIMIT:
            raise PDFResourceLimitError(
                "PDF cross-reference revision chain is too long to inspect safely."
            )
        visited_offsets.add(offset)
        probe = _RawPDFTokens(data, offset, max_tokens=8)
        if probe.peek()[:2] == ("keyword", b"xref"):
            entries, trailer = _classic_xref_at(data, offset)
            checked_classic_sections += 1
        else:
            xref_object_id, xref_generation, entries, trailer = _xref_stream_at(
                data,
                offset,
            )
            direct_object_entries.add((xref_object_id, xref_generation, offset))
            checked_xref_streams += 1

        aggregate_xref_entries += len(entries)
        if (
            aggregate_xref_entries
            > max_pdf_objects() * _PDF_XREF_AGGREGATE_ENTRY_MULTIPLIER
        ):
            raise PDFResourceLimitError(
                "PDF cross-reference revisions contain too many aggregate entries to inspect safely."
            )

        for object_id, entry in entries.items():
            if object_id <= 0:
                continue
            if entry[0] == 1:
                direct_object_entries.add((object_id, entry[2], entry[1]))
            elif entry[0] == 2:
                compressed_object_entries.add((object_id, entry[1], entry[2]))

        xref_stream_offset = _optional_direct_pdf_integer(
            trailer,
            b"XRefStm",
            label="PDF cross-reference trailer",
        )
        previous_offset = _optional_direct_pdf_integer(
            trailer,
            b"Prev",
            label="PDF cross-reference trailer",
        )
        next_ancestors = ancestors | {offset}
        if xref_stream_offset is not None:
            queue.append((xref_stream_offset, next_ancestors))
        if previous_offset is not None:
            queue.append((previous_offset, next_ancestors))

    validated_direct_offsets = {}
    for object_id, generation, offset in direct_object_entries:
        validated_direct_offsets.setdefault((object_id, generation), set()).add(offset)
    validated_compressed_mappings = {}
    for object_id, object_stream_id, object_stream_index in compressed_object_entries:
        validated_compressed_mappings.setdefault(object_id, set()).add(
            (object_stream_id, object_stream_index)
        )
    compressed_object_stream_ids = {
        object_stream_id
        for _object_id, object_stream_id, _object_stream_index in compressed_object_entries
    }
    missing_object_streams = {
        object_stream_id
        for object_stream_id in compressed_object_stream_ids
        if (object_stream_id, 0) not in validated_direct_offsets
    }
    if missing_object_streams:
        raise PDFResourceLimitError(
            "PDF compressed-object entries reference an unavailable object stream."
        )

    checked_object_streams = 0
    decoded_object_stream_bytes = 0
    object_stream_headers = {}
    for object_id, generation, offset in sorted(direct_object_entries):
        decoded_bytes, header_entries = _inspect_raw_object_stream(
            data,
            offset,
            object_id,
            generation,
            required=(generation == 0 and object_id in compressed_object_stream_ids),
        )
        if decoded_bytes:
            object_stream_headers[(object_id, generation)] = header_entries
            checked_object_streams += 1
            decoded_object_stream_bytes += decoded_bytes
            if decoded_object_stream_bytes > max_pdf_total_decoded_stream_bytes():
                raise PDFResourceLimitError(
                    "PDF object streams exceed the safe aggregate decoded-size limit."
                )

    for object_id, object_stream_id, object_stream_index in compressed_object_entries:
        header_entries = object_stream_headers.get((object_stream_id, 0))
        if (
            header_entries is None
            or object_stream_index < 0
            or object_stream_index >= len(header_entries)
            or header_entries[object_stream_index][0] != object_id
        ):
            raise PDFResourceLimitError(
                "PDF compressed-object xref entry does not match its object-stream header."
            )

    return {
        "raw_xref_preflight_applied": True,
        "preflight_xref_stream_count": checked_xref_streams,
        "preflight_classic_xref_section_count": checked_classic_sections,
        "preflight_object_stream_count": checked_object_streams,
        "_validated_direct_offsets": {
            key: frozenset(offsets)
            for key, offsets in validated_direct_offsets.items()
        },
        "_validated_compressed_mappings": {
            object_id: frozenset(mappings)
            for object_id, mappings in validated_compressed_mappings.items()
        },
    }


def _pdf_object_inventory(reader):
    direct = []
    seen = set()
    for generation, offsets in (getattr(reader, "xref", None) or {}).items():
        for object_id in (offsets or {}):
            if int(object_id) <= 0:
                continue
            key = (int(object_id), int(generation))
            if key not in seen:
                seen.add(key)
                direct.append(key)
    compressed = {
        (int(object_id), 0)
        for object_id in (getattr(reader, "xref_objStm", None) or {})
        if int(object_id) > 0
    }
    object_count = len(seen | compressed)
    if object_count > max_pdf_objects():
        raise PDFResourceLimitError(
            "PDF contains too many objects to inspect safely."
        )
    return direct, object_count, len(compressed)


def _pdf_filter_names(stream):
    value = stream.get("/Filter")
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _decode_ascii85(data):
    cleaned = b"".join(bytes(data).split())
    if cleaned.startswith(b"<~"):
        return base64.a85decode(cleaned, adobe=True)
    if cleaned.endswith(b"~>"):
        cleaned = cleaned[:-2]
    return base64.a85decode(cleaned, adobe=False)


def _decode_asciihex(data):
    cleaned = b"".join(bytes(data).split()).rstrip(b">")
    if len(cleaned) % 2:
        cleaned += b"0"
    return binascii.unhexlify(cleaned)


def _bounded_flate_decode(data, limit):
    last_error = None
    for window_bits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            decoder = zlib.decompressobj(window_bits)
            output = decoder.decompress(bytes(data), int(limit) + 1)
            if len(output) > limit or decoder.unconsumed_tail:
                raise PDFResourceLimitError(
                    "PDF contains a compressed stream that expands beyond the safe limit."
                )
            output += decoder.flush(max(1, int(limit) + 1 - len(output)))
            if len(output) > limit:
                raise PDFResourceLimitError(
                    "PDF contains a compressed stream that expands beyond the safe limit."
                )
            if not decoder.eof:
                raise zlib.error("incomplete Flate stream")
            if decoder.unused_data:
                # A completed zlib member with trailing bytes is invalid even
                # if the same byte sequence could also be interpreted as one
                # raw-Deflate stream. Do not fall through to the alternate
                # window mode after a decoder has reached EOF.
                raise PDFResourceLimitError(
                    "PDF contains malformed or trailing Flate stream data."
                )
            return output
        except PDFResourceLimitError:
            raise
        except zlib.error as exc:
            last_error = exc
    raise last_error or zlib.error("could not decode Flate stream")


def _bounded_filter_chain_data(data, filters, *, limit):
    data = bytes(data or b"")
    aliases = {
        "/A85": "/ASCII85Decode",
        "/AHx": "/ASCIIHexDecode",
        "/Fl": "/FlateDecode",
    }
    filters = [aliases.get(value, value) for value in filters]
    if not filters:
        if len(data) > int(limit):
            raise PDFResourceLimitError(
                "PDF contains a stream that exceeds the safe decoded-size limit."
            )
        return data
    try:
        if filters and filters[0] == "/ASCII85Decode":
            data = _decode_ascii85(data)
            filters = filters[1:]
        elif filters and filters[0] == "/ASCIIHexDecode":
            data = _decode_asciihex(data)
            filters = filters[1:]
    except (ValueError, binascii.Error) as exc:
        raise PDFResourceLimitError(
            "PDF contains malformed ASCII-encoded stream data."
        ) from exc
    if len(data) > int(limit):
        raise PDFResourceLimitError(
            "PDF contains a stream that exceeds the safe decoded-size limit."
        )
    if not filters:
        return data
    if filters == ["/FlateDecode"]:
        try:
            return _bounded_flate_decode(data, limit)
        except zlib.error as exc:
            raise PDFResourceLimitError(
                "PDF contains malformed or trailing Flate stream data."
            ) from exc
    return None


def _bounded_pdf_stream_data(stream):
    """Return decoded bytes for filter chains that can be bounded locally.

    ``None`` means the filter chain is intentionally not decoded here. The
    caller records that limitation rather than claiming complete stream
    expansion coverage.
    """

    return _bounded_filter_chain_data(
        getattr(stream, "_data", b"") or b"",
        _pdf_filter_names(stream),
        limit=max_pdf_decoded_stream_bytes(),
    )


def _pdf_filter_decode_parameters(stream, filter_count):
    value = _resolve_pdf_value(stream.get("/DecodeParms"))
    if value is None or isinstance(value, NullObject):
        return [None] * filter_count
    if isinstance(value, (list, tuple)):
        if len(value) != filter_count:
            return None
        parameters = []
        for item in value:
            resolved = _resolve_pdf_value(item)
            parameters.append(
                None
                if resolved is None or isinstance(resolved, NullObject)
                else resolved
            )
        return parameters
    if filter_count == 1:
        return [_resolve_pdf_value(value)]
    return None


def _pdf_image_filter_chain_native_prefix_bytes(stream, *, decode_prefix=True):
    """Return bounded wrapper bytes for a geometry-bounded native image.

    The final image codec is bounded separately through the declared image
    geometry. Some ordinary JPEG-to-PDF converters first wrap the JPEG bytes
    in ASCII or Flate compression, so decode that prefix here with the same
    decoded-stream ceiling before allowing the native image decoder to run.

    ``None`` means the chain or its decode parameters are not in the narrow
    local allowlist. A non-negative integer is the wrapper output that must be
    included in the aggregate decoded-stream budget.
    """

    aliases = {
        "/A85": "/ASCII85Decode",
        "/AHx": "/ASCIIHexDecode",
        "/CCF": "/CCITTFaxDecode",
        "/DCT": "/DCTDecode",
        "/Fl": "/FlateDecode",
        "/JPX": "/JPXDecode",
    }
    canonical = [
        aliases.get(value, value) for value in _pdf_filter_names(stream)
    ]
    if not canonical or canonical[-1] not in {
        "/CCITTFaxDecode",
        "/DCTDecode",
        "/JBIG2Decode",
        "/JPXDecode",
    }:
        return None
    prefix = canonical[:-1]
    decode_parameters = _pdf_filter_decode_parameters(stream, len(canonical))
    if decode_parameters is None:
        return None
    for filter_name, parameters in zip(prefix, decode_parameters[:-1]):
        if filter_name in {"/ASCII85Decode", "/ASCIIHexDecode"}:
            if parameters is not None:
                return None
            continue
        if filter_name == "/FlateDecode":
            if parameters is None:
                continue
            if not isinstance(parameters, DictionaryObject):
                return None
            if any(str(key) != "/Predictor" for key in parameters):
                return None
            predictor = _resolve_pdf_value(parameters.get("/Predictor", 1))
            if (
                isinstance(predictor, bool)
                or not isinstance(predictor, int)
                or predictor != 1
            ):
                return None
            continue
        return None
    terminal_parameters = decode_parameters[-1]
    if terminal_parameters is not None and not isinstance(
        terminal_parameters, DictionaryObject
    ):
        return None
    if not decode_prefix:
        return 0
    bounded_prefix = _bounded_filter_chain_data(
        getattr(stream, "_data", b"") or b"",
        prefix,
        limit=max_pdf_decoded_stream_bytes(),
    )
    if bounded_prefix is None:
        return None
    return len(bounded_prefix) if prefix else 0


def _decoded_pdf_content_has_inline_image(data):
    tokens = _RawPDFTokens(data, max_tokens=1_000_000)
    while True:
        token = tokens.pop()
        if token[0] == "eof":
            return False
        if token[:2] == ("keyword", b"BI"):
            return True


def _pdf_object_identity(value):
    indirect_reference = getattr(value, "indirect_reference", None)
    if indirect_reference is not None:
        return (
            "indirect",
            int(indirect_reference.idnum),
            int(indirect_reference.generation),
        )
    return ("direct", id(value))


def _resolve_pdf_value(value):
    return value.get_object() if hasattr(value, "get_object") else value


def _page_has_inline_image_content(page, *, page_number):
    """Scan every renderable content stream reachable from one page.

    Besides the page's own ``/Contents``, native renderers can execute content
    in Form XObjects, tiling Patterns, Type3 glyph procedures, and soft-mask
    Forms. All are traversed with cycle guards and the same bounded decoder.
    """

    content_stack = []
    resource_stack = []
    appearance_stack = []
    seen_content = set()
    seen_resources = set()
    seen_appearances = set()

    def enqueue_content(value, label):
        if value is None:
            return
        values = list(value) if isinstance(value, (list, tuple)) else [value]
        for item in values:
            stream = _resolve_pdf_value(item)
            if not isinstance(stream, StreamObject):
                raise PDFResourceLimitError(
                    f"PDF page {page_number} {label} stream is malformed."
                )
            content_stack.append(stream)

    def enqueue_resources(value):
        if value is not None:
            resource_stack.append(value)

    try:
        enqueue_content(page.get("/Contents"), "content")
        enqueue_resources(page.get("/Resources"))
        annotations = _resolve_pdf_value(page.get("/Annots"))
        for annotation_value in list(annotations) if annotations else []:
            annotation = _resolve_pdf_value(annotation_value)
            if hasattr(annotation, "get") and annotation.get("/AP") is not None:
                appearance_stack.append(annotation.get("/AP"))

        while content_stack or resource_stack or appearance_stack:
            if content_stack:
                stream = content_stack.pop()
                stream_key = _pdf_object_identity(stream)
                if stream_key in seen_content:
                    continue
                seen_content.add(stream_key)
                decoded = _bounded_pdf_stream_data(stream)
                if decoded is not None and _decoded_pdf_content_has_inline_image(decoded):
                    return True
                enqueue_resources(stream.get("/Resources"))
                continue

            if appearance_stack:
                appearance = _resolve_pdf_value(appearance_stack.pop())
                if isinstance(appearance, StreamObject):
                    enqueue_content(appearance, "annotation appearance")
                    continue
                if not hasattr(appearance, "values"):
                    raise PDFResourceLimitError(
                        f"PDF page {page_number} annotation appearance is malformed."
                    )
                appearance_key = _pdf_object_identity(appearance)
                if appearance_key in seen_appearances:
                    continue
                seen_appearances.add(appearance_key)
                appearance_stack.extend(list(appearance.values()))
                continue

            resources = _resolve_pdf_value(resource_stack.pop())
            if resources is None:
                continue
            if not hasattr(resources, "get"):
                raise PDFResourceLimitError(
                    f"PDF page {page_number} resources are malformed."
                )
            resource_key = _pdf_object_identity(resources)
            if resource_key in seen_resources:
                continue
            seen_resources.add(resource_key)

            xobjects = _resolve_pdf_value(resources.get("/XObject"))
            for value in list(xobjects.values()) if xobjects else []:
                xobject = _resolve_pdf_value(value)
                if (
                    isinstance(xobject, StreamObject)
                    and str(xobject.get("/Subtype") or "") == "/Form"
                ):
                    enqueue_content(xobject, "Form XObject")

            patterns = _resolve_pdf_value(resources.get("/Pattern"))
            for value in list(patterns.values()) if patterns else []:
                pattern = _resolve_pdf_value(value)
                if isinstance(pattern, StreamObject):
                    enqueue_content(pattern, "Pattern")

            fonts = _resolve_pdf_value(resources.get("/Font"))
            for value in list(fonts.values()) if fonts else []:
                font = _resolve_pdf_value(value)
                if not hasattr(font, "get") or str(font.get("/Subtype") or "") != "/Type3":
                    continue
                char_procs = _resolve_pdf_value(font.get("/CharProcs"))
                for char_proc in list(char_procs.values()) if char_procs else []:
                    enqueue_content(char_proc, "Type3 character")
                enqueue_resources(font.get("/Resources"))

            graphics_states = _resolve_pdf_value(resources.get("/ExtGState"))
            for value in list(graphics_states.values()) if graphics_states else []:
                graphics_state = _resolve_pdf_value(value)
                if not hasattr(graphics_state, "get"):
                    continue
                soft_mask = _resolve_pdf_value(graphics_state.get("/SMask"))
                if not hasattr(soft_mask, "get"):
                    continue
                mask_form = soft_mask.get("/G")
                if mask_form is not None:
                    enqueue_content(mask_form, "soft-mask Form")
    except PDFResourceLimitError:
        raise
    except Exception as exc:
        raise PDFResourceLimitError(
            f"PDF page {page_number} content streams could not be inspected safely."
        ) from exc
    return False


def _pdf_one_bit_image_mask_uses_bounded_limits(stream, *, width, height, bits):
    """Recognize the narrow CCITT image-mask compatibility profile.

    Image masks receive a larger pixel budget only when their declared shape,
    row geometry, and native filter chain can all be bounded without decoding
    the terminal codec. Other images retain the generic image limits.
    """

    if not pdf_image_mask_limits_enabled() or bits != 1:
        return False
    image_mask_value = _resolve_pdf_value(stream.get("/ImageMask"))
    bits_value = _resolve_pdf_value(stream.get("/BitsPerComponent"))
    bits_declares_one = (
        bits_value is None
        or isinstance(bits_value, NullObject)
        or (
            not isinstance(bits_value, bool)
            and isinstance(bits_value, int)
            and int(bits_value) == 1
        )
    )
    if not (
        isinstance(image_mask_value, BooleanObject)
        and image_mask_value.value is True
        and bits_declares_one
    ):
        return False
    for key in (
        "/ColorSpace",
        "/Mask",
        "/SMask",
        "/Alternates",
        "/SMaskInData",
    ):
        value = _resolve_pdf_value(stream.get(key))
        if value is not None and not isinstance(value, NullObject):
            return False
    decode_value = _resolve_pdf_value(stream.get("/Decode"))
    if decode_value is not None and not isinstance(decode_value, NullObject):
        if not isinstance(decode_value, (list, tuple)) or len(decode_value) != 2:
            return False
        decoded_numbers = []
        for item in decode_value:
            item = _resolve_pdf_value(item)
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return False
            decoded_numbers.append(float(item))
        if decoded_numbers not in ([0.0, 1.0], [1.0, 0.0]):
            return False

    aliases = {
        "/A85": "/ASCII85Decode",
        "/AHx": "/ASCIIHexDecode",
        "/CCF": "/CCITTFaxDecode",
        "/Fl": "/FlateDecode",
    }
    filters = [
        aliases.get(value, value) for value in _pdf_filter_names(stream)
    ]
    if not filters or filters[-1] != "/CCITTFaxDecode":
        return False
    if (
        _pdf_image_filter_chain_native_prefix_bytes(
            stream,
            decode_prefix=False,
        )
        is None
    ):
        return False
    decode_parameters = _pdf_filter_decode_parameters(stream, len(filters))
    if decode_parameters is None:
        return False
    terminal_parameters = decode_parameters[-1]
    if terminal_parameters is None:
        terminal_parameters = DictionaryObject()
    if not isinstance(terminal_parameters, DictionaryObject):
        return False
    allowed_keys = {
        "/K",
        "/Columns",
        "/Rows",
        "/EndOfLine",
        "/EncodedByteAlign",
        "/EndOfBlock",
        "/BlackIs1",
        "/DamagedRowsBeforeError",
    }
    if any(str(key) not in allowed_keys for key in terminal_parameters):
        return False

    def integer_parameter(name, default, *, minimum=None):
        value = _resolve_pdf_value(terminal_parameters.get(name, default))
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        value = int(value)
        if minimum is not None and value < minimum:
            return None
        return value

    if "/Columns" not in terminal_parameters or "/Rows" not in terminal_parameters:
        return False
    columns = integer_parameter("/Columns", None, minimum=1)
    rows = integer_parameter("/Rows", None, minimum=1)
    k_value = integer_parameter("/K", 0)
    damaged_rows = integer_parameter("/DamagedRowsBeforeError", 0, minimum=0)
    if (
        columns != width
        or rows != height
        or k_value is None
        or not (k_value == -1 or 0 <= k_value <= height)
        or damaged_rows is None
        or damaged_rows > height
    ):
        return False
    for key in ("/EndOfLine", "/EncodedByteAlign", "/EndOfBlock", "/BlackIs1"):
        if key not in terminal_parameters:
            continue
        value = _resolve_pdf_value(terminal_parameters.get(key))
        if not isinstance(value, BooleanObject):
            return False
    return True


def _pdf_image_dimensions(stream):
    subtype_value = _resolve_pdf_value(stream.get("/Subtype"))
    if str(subtype_value or "") != "/Image":
        return None
    image_mask_value = _resolve_pdf_value(stream.get("/ImageMask"))
    bits_value = _resolve_pdf_value(stream.get("/BitsPerComponent"))
    default_bits = (
        1
        if (
            isinstance(image_mask_value, BooleanObject)
            and image_mask_value.value is True
        )
        else 8
    )
    width = _resolve_pdf_value(stream.get("/Width"))
    height = _resolve_pdf_value(stream.get("/Height"))
    bits = (
        default_bits
        if bits_value is None or isinstance(bits_value, NullObject)
        else bits_value
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in (width, height, bits)
    ):
        raise PDFResourceLimitError(
            "PDF contains invalid embedded-image dimensions."
        )
    width = int(width)
    height = int(height)
    bits = int(bits)
    if width <= 0 or height <= 0 or bits <= 0:
        raise PDFResourceLimitError(
            "PDF contains invalid embedded-image dimensions."
        )
    pixels = width * height
    explicit_one_bit_mask = (
        isinstance(image_mask_value, BooleanObject)
        and image_mask_value.value is True
        and bits == 1
    )
    mask_limits_applied = _pdf_one_bit_image_mask_uses_bounded_limits(
        stream,
        width=width,
        height=height,
        bits=bits,
    )
    image_pixel_limit = (
        max_pdf_image_mask_pixels()
        if mask_limits_applied
        else max_pdf_image_pixels()
    )
    if pixels > image_pixel_limit:
        if mask_limits_applied:
            raise PDFResourceLimitError(
                "PDF contains an embedded one-bit image mask that exceeds the safe pixel limit."
            )
        raise PDFResourceLimitError(
            "PDF contains an embedded image that exceeds the safe pixel limit."
        )
    color_space = str(stream.get("/ColorSpace") or "")
    if explicit_one_bit_mask or color_space == "/DeviceGray":
        components = 1
    elif color_space == "/DeviceRGB":
        components = 3
    else:
        # CMYK, ICC-based, indexed, or indirect color spaces use a conservative
        # upper estimate; this is only a memory-safety boundary.
        components = 4
    estimated_bytes = (
        math.ceil(width / 8) * height
        if explicit_one_bit_mask
        else math.ceil(pixels * components * bits / 8)
    )
    if estimated_bytes > max_pdf_decoded_stream_bytes():
        raise PDFResourceLimitError(
            "PDF contains an embedded image whose decoded size exceeds the safe limit."
        )
    return {
        "pixels": pixels,
        "estimated_bytes": estimated_bytes,
        "explicit_one_bit_mask": explicit_one_bit_mask,
        "mask_limits_applied": mask_limits_applied,
    }


def _inspect_pdf_objects(reader):
    direct_objects, object_count, compressed_object_count = _pdf_object_inventory(reader)
    stream_count = 0
    encoded_stream_bytes = 0
    decoded_stream_bytes_checked = 0
    bounded_decoded_stream_count = 0
    unbounded_decoded_stream_count = 0
    bounded_image_prefix_count = 0
    bounded_image_prefix_bytes = 0
    image_object_count = 0
    image_mask_object_count = 0
    bounded_image_mask_object_count = 0
    image_mask_pixels = 0
    bounded_image_mask_pixels = 0
    estimated_image_mask_bytes = 0
    unsafe_image_filter_count = 0
    estimated_image_bytes = 0
    max_image_pixels_seen = 0
    for object_id, generation in direct_objects:
        try:
            value = reader.get_object(IndirectObject(object_id, generation, reader))
        except Exception as exc:
            raise PDFResourceLimitError(
                "PDF contains an object that could not be inspected safely."
            ) from exc
        if not isinstance(value, StreamObject):
            continue
        stream_count += 1
        if stream_count > max_pdf_streams():
            raise PDFResourceLimitError(
                "PDF contains too many streams to inspect safely."
            )
        encoded = bytes(getattr(value, "_data", b"") or b"")
        encoded_stream_bytes += len(encoded)
        image_dimensions = _pdf_image_dimensions(value)
        if image_dimensions:
            image_object_count += 1
            pixels = image_dimensions["pixels"]
            image_bytes = image_dimensions["estimated_bytes"]
            max_image_pixels_seen = max(max_image_pixels_seen, pixels)
            estimated_image_bytes += image_bytes
            if image_dimensions["explicit_one_bit_mask"]:
                image_mask_object_count += 1
                image_mask_pixels += pixels
                estimated_image_mask_bytes += image_bytes
            if image_dimensions["mask_limits_applied"]:
                bounded_image_mask_object_count += 1
                bounded_image_mask_pixels += pixels
                if bounded_image_mask_pixels > max_pdf_total_image_mask_pixels():
                    raise PDFResourceLimitError(
                        "PDF one-bit image masks exceed the safe document pixel limit."
                    )

        decoded = _bounded_pdf_stream_data(value)
        if decoded is None:
            # Standard compressed image filters are bounded through declared
            # image geometry above. Other filter chains remain a disclosed
            # inspection limitation and are not decoded speculatively.
            if not image_dimensions:
                unbounded_decoded_stream_count += 1
            else:
                native_prefix_bytes = _pdf_image_filter_chain_native_prefix_bytes(
                    value
                )
                if native_prefix_bytes is not None:
                    if native_prefix_bytes:
                        bounded_image_prefix_count += 1
                        bounded_image_prefix_bytes += native_prefix_bytes
                    decoded_stream_bytes_checked += native_prefix_bytes
                    if (
                        decoded_stream_bytes_checked
                        > max_pdf_total_decoded_stream_bytes()
                    ):
                        raise PDFResourceLimitError(
                            "PDF decoded streams exceed the safe aggregate processing limit."
                        )
                    continue
                unsafe_image_filter_count += 1
                unbounded_decoded_stream_count += 1
            continue
        bounded_decoded_stream_count += 1
        if not image_dimensions:
            decoded_stream_bytes_checked += len(decoded)
            if decoded_stream_bytes_checked > max_pdf_total_decoded_stream_bytes():
                raise PDFResourceLimitError(
                    "PDF decoded streams exceed the safe aggregate processing limit."
                )
    warnings = []
    if unbounded_decoded_stream_count:
        warnings.append(
            "Some PDF stream filter chains could not receive a bounded decoded-size "
            "check. Page, object, encoded-file, image, and extracted-output limits "
            "still apply; review the source before confirming."
        )
    if estimated_image_bytes > max_pdf_total_decoded_stream_bytes():
        warnings.append(
            "PDF contains multiple high-resolution images. Each image passed the "
            "per-image limit, but staff should process or preview pages one at a time."
        )
    return {
        "warnings": warnings,
        "object_count": object_count,
        "compressed_object_count": compressed_object_count,
        "stream_count": stream_count,
        "encoded_stream_bytes": encoded_stream_bytes,
        "bounded_decoded_stream_count": bounded_decoded_stream_count,
        "unbounded_decoded_stream_count": unbounded_decoded_stream_count,
        "decoded_stream_bytes_checked": decoded_stream_bytes_checked,
        "decoded_stream_checks_complete": unbounded_decoded_stream_count == 0,
        "bounded_image_prefix_count": bounded_image_prefix_count,
        "bounded_image_prefix_bytes": bounded_image_prefix_bytes,
        "image_object_count": image_object_count,
        "image_mask_object_count": image_mask_object_count,
        "bounded_image_mask_object_count": bounded_image_mask_object_count,
        "image_mask_pixels": image_mask_pixels,
        "bounded_image_mask_pixels": bounded_image_mask_pixels,
        "estimated_image_mask_bytes": estimated_image_mask_bytes,
        "unsafe_image_filter_count": unsafe_image_filter_count,
        "estimated_image_bytes": estimated_image_bytes,
        "max_image_pixels": max_image_pixels_seen,
    }


def _page_referenced_image_usage(
    page,
    *,
    page_number,
    relaxed_image_mask_present=False,
):
    """Bound unique image XObjects reachable from one page's resources."""

    try:
        resources = _resolve_pdf_value(page.get("/Resources"))
    except Exception as exc:
        raise PDFResourceLimitError(
            f"PDF page {page_number} resources could not be inspected safely."
        ) from exc
    resource_stack = [resources] if resources else []
    seen_resources = set()
    seen_xobjects = set()
    total_pixels = 0
    generic_limit_pixels = 0
    image_mask_pixels = 0
    total_estimated_bytes = 0
    estimated_image_mask_bytes = 0
    image_count = 0
    image_mask_count = 0
    complex_render_path = False

    try:
        annotations = _resolve_pdf_value(page.get("/Annots"))
        for annotation_value in list(annotations) if annotations else []:
            annotation = _resolve_pdf_value(annotation_value)
            if hasattr(annotation, "get") and annotation.get("/AP") is not None:
                complex_render_path = True
                break
    except Exception as exc:
        raise PDFResourceLimitError(
            f"PDF page {page_number} annotation resources could not be inspected safely."
        ) from exc

    while resource_stack:
        current_resources = _resolve_pdf_value(resource_stack.pop())
        resource_key = id(current_resources)
        indirect_reference = getattr(current_resources, "indirect_reference", None)
        if indirect_reference is not None:
            resource_key = (
                int(indirect_reference.idnum),
                int(indirect_reference.generation),
            )
        if resource_key in seen_resources:
            continue
        seen_resources.add(resource_key)
        try:
            xobjects = current_resources.get("/XObject")
            values = list(xobjects.values()) if xobjects else []
            patterns = _resolve_pdf_value(current_resources.get("/Pattern"))
            if patterns and list(patterns.values()):
                complex_render_path = True
            fonts = _resolve_pdf_value(current_resources.get("/Font"))
            for font_value in list(fonts.values()) if fonts else []:
                font = _resolve_pdf_value(font_value)
                if hasattr(font, "get") and str(font.get("/Subtype") or "") == "/Type3":
                    complex_render_path = True
                    break
            graphics_states = _resolve_pdf_value(
                current_resources.get("/ExtGState")
            )
            for graphics_state_value in (
                list(graphics_states.values()) if graphics_states else []
            ):
                graphics_state = _resolve_pdf_value(graphics_state_value)
                if not hasattr(graphics_state, "get"):
                    continue
                soft_mask = _resolve_pdf_value(graphics_state.get("/SMask"))
                if soft_mask is not None and str(soft_mask) != "/None":
                    complex_render_path = True
                    break
        except Exception as exc:
            raise PDFResourceLimitError(
                f"PDF page {page_number} image resources could not be inspected safely."
            ) from exc
        for value in values:
            try:
                xobject = value.get_object() if hasattr(value, "get_object") else value
            except Exception as exc:
                raise PDFResourceLimitError(
                    f"PDF page {page_number} image resource could not be inspected safely."
                ) from exc
            xobject_key = id(xobject)
            indirect_reference = getattr(xobject, "indirect_reference", None)
            if indirect_reference is not None:
                xobject_key = (
                    int(indirect_reference.idnum),
                    int(indirect_reference.generation),
                )
            if xobject_key in seen_xobjects:
                continue
            seen_xobjects.add(xobject_key)
            if not isinstance(xobject, StreamObject):
                continue
            image_dimensions = _pdf_image_dimensions(xobject)
            if image_dimensions:
                for nested_mask_key in ("/Mask", "/SMask"):
                    nested_mask = _resolve_pdf_value(xobject.get(nested_mask_key))
                    if isinstance(nested_mask, StreamObject):
                        complex_render_path = True
                pixels = image_dimensions["pixels"]
                estimated_bytes = image_dimensions["estimated_bytes"]
                total_pixels += pixels
                total_estimated_bytes += estimated_bytes
                image_count += 1
                if image_dimensions["mask_limits_applied"]:
                    image_mask_pixels += pixels
                    if image_mask_pixels > max_pdf_page_image_mask_pixels():
                        raise PDFResourceLimitError(
                            f"PDF page {page_number} references one-bit image masks "
                            "whose aggregate pixel count exceeds the safe limit."
                        )
                else:
                    generic_limit_pixels += pixels
                if image_dimensions["explicit_one_bit_mask"]:
                    image_mask_count += 1
                    estimated_image_mask_bytes += estimated_bytes
                if generic_limit_pixels > max_pdf_image_pixels():
                    raise PDFResourceLimitError(
                        f"PDF page {page_number} references images whose aggregate "
                        "pixel count exceeds the safe limit."
                    )
                if total_estimated_bytes > max_pdf_total_decoded_stream_bytes():
                    raise PDFResourceLimitError(
                        f"PDF page {page_number} references images whose aggregate "
                        "decoded size exceeds the safe limit."
                    )
                continue
            if str(xobject.get("/Subtype") or "") == "/Form":
                nested_resources = xobject.get("/Resources")
                if nested_resources:
                    resource_stack.append(nested_resources)

    if relaxed_image_mask_present and complex_render_path:
        raise PDFResourceLimitError(
            f"PDF page {page_number} combines one-bit image masks with a complex "
            "render path that cannot be accounted safely."
        )

    return {
        "pixels": total_pixels,
        "estimated_bytes": total_estimated_bytes,
        "image_count": image_count,
        "image_mask_count": image_mask_count,
        "image_mask_pixels": image_mask_pixels,
        "estimated_image_mask_bytes": estimated_image_mask_bytes,
    }


class _PreflightBoundPdfReader(PdfReader):
    """PdfReader that cannot repair references outside the validated xref graph."""

    def __init__(
        self,
        stream,
        *,
        validated_direct_offsets,
        validated_compressed_mappings,
    ):
        self._validated_direct_offsets = dict(validated_direct_offsets)
        self._validated_compressed_mappings = dict(
            validated_compressed_mappings
        )
        super().__init__(stream)

    def get_object(self, indirect_reference):
        if isinstance(indirect_reference, int):
            object_id = int(indirect_reference)
            generation = 0
        else:
            object_id = int(indirect_reference.idnum)
            generation = int(indirect_reference.generation)
        key = (object_id, generation)
        direct_offsets = self._validated_direct_offsets.get(key)
        if direct_offsets is not None:
            cached = self.cache_get_indirect_object(generation, object_id)
            if cached is None:
                current_offset = (self.xref.get(generation) or {}).get(object_id)
                if current_offset not in direct_offsets:
                    raise PdfReadError(
                        "PDF reader selected an object offset outside the validated cross-reference graph."
                    )
            return super().get_object(indirect_reference)
        if generation == 0 and object_id in self._validated_compressed_mappings:
            current_mapping = (self.xref_objStm or {}).get(object_id)
            if current_mapping not in self._validated_compressed_mappings[object_id]:
                raise PdfReadError(
                    "PDF reader selected an object-stream mapping outside the "
                    "validated cross-reference graph."
                )
            return super().get_object(indirect_reference)
        else:
            raise PdfReadError(
                "PDF object reference is absent from the validated cross-reference graph."
            )


def inspect_pdf_attachment(data, *, declared_mime_type="", max_pages=None):
    data = bytes(data)
    if not data.startswith(b"%PDF-"):
        raise ValidationError("Invalid PDF file. The upload does not look like a PDF.")
    xref_preflight = _preflight_pdf_xref_structures(data)
    validated_direct_offsets = xref_preflight.pop(
        "_validated_direct_offsets"
    )
    validated_compressed_mappings = xref_preflight.pop(
        "_validated_compressed_mappings"
    )
    try:
        reader = _PreflightBoundPdfReader(
            BytesIO(data),
            validated_direct_offsets=validated_direct_offsets,
            validated_compressed_mappings=validated_compressed_mappings,
        )
    except (PdfReadError, OSError, ValueError, RecursionError, MemoryError) as exc:
        raise ValidationError("Could not read PDF: the document is incomplete or malformed.") from exc
    if reader.is_encrypted:
        raise ValidationError(
            "Encrypted PDF files are not supported. Please upload an unlocked PDF."
        )

    object_inspection = _inspect_pdf_objects(reader)
    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise PDFResourceLimitError(
            "PDF page structure could not be traversed safely."
        ) from exc
    effective_max_pages = (
        max_pdf_inspection_pages()
        if max_pages is None
        else min(max(1, int(max_pages)), 100)
    )
    if page_count < 1:
        raise ValidationError("Invalid PDF file. The document has no pages.")
    if page_count > effective_max_pages:
        raise PDFResourceLimitError(
            f"PDF has {page_count} pages. Maximum supported pages: {effective_max_pages}."
        )

    max_width = 0.0
    max_height = 0.0
    max_area = 0.0
    max_page_referenced_image_pixels = 0
    max_page_referenced_image_bytes = 0
    max_page_referenced_image_count = 0
    max_page_referenced_image_mask_count = 0
    max_page_referenced_image_mask_pixels = 0
    max_page_referenced_image_mask_bytes = 0
    total_referenced_image_mask_count = 0
    total_referenced_image_mask_pixels = 0
    total_referenced_image_mask_bytes = 0
    inline_image_page_count = 0
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            user_unit = page.get("/UserUnit", 1)
            boxes = [page.mediabox, page.cropbox]
            for box in boxes:
                geometry = validate_pdf_page_geometry(
                    box.width,
                    box.height,
                    page_number=page_number,
                    user_unit=user_unit,
                )
                max_width = max(max_width, geometry["width_points"])
                max_height = max(max_height, geometry["height_points"])
                max_area = max(max_area, geometry["area_points"])
            page_image_usage = _page_referenced_image_usage(
                page,
                page_number=page_number,
                relaxed_image_mask_present=(
                    object_inspection["bounded_image_mask_object_count"] > 0
                ),
            )
            max_page_referenced_image_pixels = max(
                max_page_referenced_image_pixels,
                page_image_usage["pixels"],
            )
            max_page_referenced_image_bytes = max(
                max_page_referenced_image_bytes,
                page_image_usage["estimated_bytes"],
            )
            max_page_referenced_image_count = max(
                max_page_referenced_image_count,
                page_image_usage["image_count"],
            )
            max_page_referenced_image_mask_count = max(
                max_page_referenced_image_mask_count,
                page_image_usage["image_mask_count"],
            )
            max_page_referenced_image_mask_pixels = max(
                max_page_referenced_image_mask_pixels,
                page_image_usage["image_mask_pixels"],
            )
            max_page_referenced_image_mask_bytes = max(
                max_page_referenced_image_mask_bytes,
                page_image_usage["estimated_image_mask_bytes"],
            )
            total_referenced_image_mask_count += page_image_usage[
                "image_mask_count"
            ]
            total_referenced_image_mask_pixels += page_image_usage[
                "image_mask_pixels"
            ]
            total_referenced_image_mask_bytes += page_image_usage[
                "estimated_image_mask_bytes"
            ]
            if (
                total_referenced_image_mask_pixels
                > max_pdf_total_image_mask_pixels()
            ):
                raise PDFResourceLimitError(
                    "PDF page references to one-bit image masks exceed the safe "
                    "document pixel limit."
                )
            if _page_has_inline_image_content(page, page_number=page_number):
                inline_image_page_count += 1
        except PDFResourceLimitError:
            raise
        except Exception as exc:
            raise PDFResourceLimitError(
                f"PDF page {page_number} geometry could not be inspected safely."
            ) from exc

    lower = data.lower()
    active_content = any(
        marker in lower
        for marker in (b"/javascript", b"/launch", b"/richmedia", b"/openaction")
    )
    embedded_files = b"/embeddedfile" in lower or b"/embeddedfiles" in lower
    form_fields = b"/acroform" in lower or b"/xfa" in lower
    warnings = [
        *_mime_warnings(".pdf", declared_mime_type),
        *(object_inspection.get("warnings") or []),
    ]
    if active_content:
        warnings.append(
            "PDF contains active-content markers. The application does not execute "
            "them; verify the visible document manually."
        )
    if embedded_files:
        warnings.append(
            "PDF contains embedded-file markers. Embedded files are not parsed; "
            "verify the visible request against the original attachment."
        )
    if form_fields:
        warnings.append(
            "PDF contains form or XFA fields. Form values may not appear in extracted "
            "text; verify the document manually."
        )
    if inline_image_page_count:
        warnings.append(
            "PDF content contains inline images that cannot receive bounded "
            "per-image geometry checks. Local extraction and rendering were skipped."
        )
    return reader, {
        "warnings": list(dict.fromkeys(warnings)),
        "safety": {
            "container": "pdf",
            "validated_format": "pdf",
            "encrypted": False,
            "hard_limits_applied": True,
            "object_count": object_inspection["object_count"],
            "stream_count": object_inspection["stream_count"],
            "encoded_stream_bytes": object_inspection["encoded_stream_bytes"],
            "bounded_decoded_stream_count": object_inspection[
                "bounded_decoded_stream_count"
            ],
            "unbounded_decoded_stream_count": object_inspection[
                "unbounded_decoded_stream_count"
            ],
            "decoded_stream_bytes_checked": object_inspection[
                "decoded_stream_bytes_checked"
            ],
            "decoded_stream_checks_complete": object_inspection[
                "decoded_stream_checks_complete"
            ],
            "bounded_image_prefix_count": object_inspection[
                "bounded_image_prefix_count"
            ],
            "bounded_image_prefix_bytes": object_inspection[
                "bounded_image_prefix_bytes"
            ],
            "local_traversal_safe": (
                object_inspection["decoded_stream_checks_complete"]
                and inline_image_page_count == 0
            ),
            **xref_preflight,
            "active_content_markers": active_content,
            "embedded_file_markers": embedded_files,
        },
        "fidelity": {
            "form_field_markers": form_fields,
            "page_count": page_count,
            "max_page_width_points": round(max_width, 3),
            "max_page_height_points": round(max_height, 3),
            "max_page_area_points": round(max_area, 3),
            "compressed_object_count": object_inspection[
                "compressed_object_count"
            ],
            "image_object_count": object_inspection["image_object_count"],
            "image_mask_object_count": object_inspection[
                "image_mask_object_count"
            ],
            "bounded_image_mask_object_count": object_inspection[
                "bounded_image_mask_object_count"
            ],
            "image_mask_pixels": object_inspection["image_mask_pixels"],
            "bounded_image_mask_pixels": object_inspection[
                "bounded_image_mask_pixels"
            ],
            "estimated_image_mask_bytes": object_inspection[
                "estimated_image_mask_bytes"
            ],
            "max_image_pixels": object_inspection["max_image_pixels"],
            "estimated_image_bytes": object_inspection[
                "estimated_image_bytes"
            ],
            "max_page_referenced_image_count": max_page_referenced_image_count,
            "max_page_referenced_image_mask_count": (
                max_page_referenced_image_mask_count
            ),
            "max_page_referenced_image_pixels": max_page_referenced_image_pixels,
            "max_page_referenced_image_bytes": max_page_referenced_image_bytes,
            "max_page_referenced_image_mask_pixels": (
                max_page_referenced_image_mask_pixels
            ),
            "max_page_referenced_image_mask_bytes": (
                max_page_referenced_image_mask_bytes
            ),
            "total_referenced_image_mask_count": (
                total_referenced_image_mask_count
            ),
            "total_referenced_image_mask_pixels": (
                total_referenced_image_mask_pixels
            ),
            "total_referenced_image_mask_bytes": (
                total_referenced_image_mask_bytes
            ),
            "inline_image_page_count": inline_image_page_count,
            "unsafe_image_filter_count": object_inspection[
                "unsafe_image_filter_count"
            ],
        },
    }
