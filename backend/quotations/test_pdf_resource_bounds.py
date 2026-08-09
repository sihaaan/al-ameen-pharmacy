import base64
import hashlib
import zlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfWriter
from PIL import Image
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    EncodedStreamObject,
    NameObject,
    NullObject,
    NumberObject,
)
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from rest_framework import status
from rest_framework.test import APIClient

from .ai_parsing import (
    AIParseError,
    _render_pdf_bytes_images,
    clean_historical_import_with_ai,
    clean_preview_with_ai,
)
from .attachment_inspection import (
    PDFResourceLimitError,
    _bounded_flate_decode,
    inspect_pdf_attachment,
)
from .gmail_inquiry_import import _build_source_analysis
from .historical_import_parsers import _extract_pdf_text, parse_historical_pdf_upload
from .import_parsers import parse_pdf_preview
from .models import (
    GmailInquiryImport,
    GmailOAuthConnection,
    HistoricalPriceImport,
    QuotationSettings,
)


PDF_MIME = "application/pdf"


def flate_zlib_raw_polyglot(*, trailing=b"evil"):
    payload = bytearray(b"A" * 0xFEFE)
    final_data_length = (len(payload) - 260) + 4 + len(trailing)
    payload[255:260] = (
        b"\x01"
        + final_data_length.to_bytes(2, "little")
        + (0xFFFF - final_data_length).to_bytes(2, "little")
    )
    zlib_member = (
        b"\x78\x01\x01\xfe\xfe\x01\x01"
        + bytes(payload)
        + zlib.adler32(payload).to_bytes(4, "big")
    )
    return zlib_member + trailing


def blank_pdf(*, width=595, height=842):
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=height)
    writer.write(output)
    return output.getvalue()


def text_pdf(text="Sterile Gauze 5 PCS"):
    output = BytesIO()
    document = canvas.Canvas(output)
    document.drawString(72, 740, text)
    document.save()
    return output.getvalue()


def run_length_content_pdf(*, repeat_blocks=32):
    output = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    # 129 means repeat the next byte 128 times; 128 terminates the stream.
    stream = EncodedStreamObject()
    stream._data = (bytes([129, 32]) * repeat_blocks) + bytes([128])
    stream[NameObject("/Filter")] = NameObject("/RunLengthDecode")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(output)
    return output.getvalue()


def inline_image_content_pdf(*, width=1_000_000, height=1_000_000):
    output = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    stream = DecodedStreamObject()
    stream.set_data(
        b"q\nBI /W "
        + str(width).encode("ascii")
        + b" /H "
        + str(height).encode("ascii")
        + b" /CS /RGB /BPC 8 ID x EI\nQ"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(output)
    return output.getvalue()


def form_inline_image_content_pdf(*, width=1_000_000, height=1_000_000):
    output = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    form = DecodedStreamObject()
    form.set_data(
        b"q\nBI /W "
        + str(width).encode("ascii")
        + b" /H "
        + str(height).encode("ascii")
        + b" /CS /RGB /BPC 8 ID x EI\nQ"
    )
    form[NameObject("/Type")] = NameObject("/XObject")
    form[NameObject("/Subtype")] = NameObject("/Form")
    form[NameObject("/BBox")] = ArrayObject(
        [NumberObject(0), NumberObject(0), NumberObject(100), NumberObject(100)]
    )
    form[NameObject("/Resources")] = DictionaryObject()
    form_ref = writer._add_object(form)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/Fm0"): form_ref}
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(b"q /Fm0 Do Q")
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(output)
    return output.getvalue()


def annotation_inline_image_content_pdf(*, width=1_000_000, height=1_000_000):
    output = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    appearance = DecodedStreamObject()
    appearance.set_data(
        b"q\nBI /W "
        + str(width).encode("ascii")
        + b" /H "
        + str(height).encode("ascii")
        + b" /CS /RGB /BPC 8 ID x EI\nQ"
    )
    appearance[NameObject("/Type")] = NameObject("/XObject")
    appearance[NameObject("/Subtype")] = NameObject("/Form")
    appearance[NameObject("/BBox")] = ArrayObject(
        [NumberObject(0), NumberObject(0), NumberObject(100), NumberObject(100)]
    )
    appearance[NameObject("/Resources")] = DictionaryObject()
    appearance_ref = writer._add_object(appearance)
    annotation = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Stamp"),
            NameObject("/Rect"): ArrayObject(
                [
                    NumberObject(0),
                    NumberObject(0),
                    NumberObject(100),
                    NumberObject(100),
                ]
            ),
            NameObject("/AP"): DictionaryObject(
                {NameObject("/N"): appearance_ref}
            ),
        }
    )
    page[NameObject("/Annots")] = ArrayObject([writer._add_object(annotation)])
    writer.write(output)
    return output.getvalue()


def unsupported_filter_image_pdf():
    output = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    image = EncodedStreamObject()
    image._data = (bytes([129, 0]) * 100) + bytes([128])
    image[NameObject("/Type")] = NameObject("/XObject")
    image[NameObject("/Subtype")] = NameObject("/Image")
    image[NameObject("/Width")] = NumberObject(10)
    image[NameObject("/Height")] = NumberObject(10)
    image[NameObject("/BitsPerComponent")] = NumberObject(8)
    image[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
    image[NameObject("/Filter")] = NameObject("/RunLengthDecode")
    image_ref = writer._add_object(image)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/Im0"): image_ref}
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(b"q 10 0 0 10 0 0 cm /Im0 Do Q")
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(output)
    return output.getvalue()


def flate_wrapped_jpeg_image_pdf(
    *,
    intermediate_bytes=None,
    image_count=1,
    ascii_filter="",
    flate_predictor=None,
    trailing_bytes=b"",
    truncate_flate=False,
    indirect_decode_parameters=False,
):
    output = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    if intermediate_bytes is None:
        image_output = BytesIO()
        image_source = Image.new("RGB", (32, 48), color=(220, 40, 40))
        image_source.save(image_output, format="JPEG", quality=60)
        image_source.close()
        intermediate_bytes = image_output.getvalue()
    encoded = zlib.compress(intermediate_bytes)
    if truncate_flate:
        encoded = encoded[:-2]
    encoded += trailing_bytes
    filters = [NameObject("/FlateDecode"), NameObject("/DCTDecode")]
    flate_parameters = (
        NullObject()
        if flate_predictor is None
        else DictionaryObject(
            {NameObject("/Predictor"): NumberObject(flate_predictor)}
        )
    )
    decode_parameters = [
        flate_parameters,
        DictionaryObject({NameObject("/Quality"): NumberObject(60)}),
    ]
    if ascii_filter == "ascii85":
        encoded = base64.a85encode(encoded, adobe=False) + b"~>"
        filters.insert(0, NameObject("/ASCII85Decode"))
        decode_parameters.insert(0, NullObject())
    elif ascii_filter == "asciihex":
        encoded = encoded.hex().encode("ascii") + b">"
        filters.insert(0, NameObject("/ASCIIHexDecode"))
        decode_parameters.insert(0, NullObject())
    decode_parameters_value = ArrayObject(decode_parameters)
    if indirect_decode_parameters:
        decode_parameters_value = writer._add_object(decode_parameters_value)
    image_references = {}
    for index in range(image_count):
        image = EncodedStreamObject()
        image._data = encoded
        image[NameObject("/Type")] = NameObject("/XObject")
        image[NameObject("/Subtype")] = NameObject("/Image")
        image[NameObject("/Width")] = NumberObject(32)
        image[NameObject("/Height")] = NumberObject(48)
        image[NameObject("/BitsPerComponent")] = NumberObject(8)
        image[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
        image[NameObject("/Filter")] = ArrayObject(filters)
        image[NameObject("/DecodeParms")] = decode_parameters_value
        image_references[NameObject(f"/Im{index}")] = writer._add_object(image)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(image_references)
        }
    )
    content = DecodedStreamObject()
    content.set_data(
        b"\n".join(
            f"q 32 0 0 48 0 0 cm /Im{index} Do Q".encode("ascii")
            for index in range(image_count)
        )
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(output)
    return output.getvalue()


def multipage_scan_pdf(page_count=3):
    output = BytesIO()
    document = canvas.Canvas(output, pagesize=(595, 842))
    colors = [(220, 40, 40), (40, 220, 40), (40, 40, 220)]
    for index in range(page_count):
        image_output = BytesIO()
        image = Image.new(
            "RGB",
            (2480, 3508),
            color=colors[index % len(colors)],
        )
        image.save(image_output, format="JPEG", quality=40)
        image.close()
        document.drawImage(
            ImageReader(BytesIO(image_output.getvalue())),
            0,
            0,
            width=595,
            height=842,
        )
        document.showPage()
    document.save()
    return output.getvalue()


def single_page_multi_image_pdf(image_count=3):
    output = BytesIO()
    document = canvas.Canvas(output, pagesize=(595, 842))
    colors = [(220, 40, 40), (40, 220, 40), (40, 40, 220)]
    for index in range(image_count):
        image_output = BytesIO()
        image = Image.new(
            "RGB",
            (2480, 3508),
            color=colors[index % len(colors)],
        )
        image.save(image_output, format="JPEG", quality=40)
        image.close()
        document.drawImage(
            ImageReader(BytesIO(image_output.getvalue())),
            index * 10,
            index * 10,
            width=595,
            height=842,
        )
    document.save()
    return output.getvalue()


def xref_stream_bomb_pdf(
    *,
    decoded_bytes=4096,
    type_entry=b"/Type /XRef",
):
    encoded = zlib.compress(b"X" * decoded_bytes)
    prefix = b"%PDF-1.7\n1 0 obj\n"
    dictionary = (
        b"<< "
        + type_entry
        + b" /Size 2 /W [1 2 1] /Length "
        + str(len(encoded)).encode("ascii")
        + b" /Filter /FlateDecode >>\nstream\n"
    )
    return (
        prefix
        + dictionary
        + encoded
        + b"\nendstream\nendobj\nstartxref\n9\n%%EOF\n"
    )


def oversized_classic_xref_pdf(*, declared_entries=100):
    return (
        b"%PDF-1.4\n"
        + b"xref\n0 "
        + str(declared_entries).encode("ascii")
        + b"\ntrailer\n<< /Size 1 >>\nstartxref\n9\n%%EOF\n"
    )


def raw_xref_stream_pdf(
    *,
    dictionary_entries,
    payload=b"",
    object_id=1,
    generation=0,
    before=b"",
    include_length=True,
):
    prefix = b"%PDF-1.7\n" + bytes(before)
    xref_offset = len(prefix)
    length_entry = (
        b" /Length " + str(len(payload)).encode("ascii")
        if include_length
        else b""
    )
    body = (
        str(object_id).encode("ascii")
        + b" "
        + str(generation).encode("ascii")
        + b" obj\n<< "
        + bytes(dictionary_entries)
        + length_entry
        + b" >>\nstream\n"
        + bytes(payload)
        + b"\nendstream\nendobj\n"
    )
    return (
        prefix
        + body
        + b"startxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )


def classic_object_pdf(*, objects, listed_keys, trailer_entries=b""):
    data = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for object_id, generation, body in objects:
        offsets[(object_id, generation)] = len(data)
        data.extend(
            str(object_id).encode("ascii")
            + b" "
            + str(generation).encode("ascii")
            + b" obj\n"
            + bytes(body)
            + b"\nendobj\n"
        )
    xref_offset = len(data)
    data.extend(b"xref\n0 1\n0000000000 65535 f \n")
    for object_id, generation in listed_keys:
        data.extend(
            str(object_id).encode("ascii")
            + b" 1\n"
            + f"{offsets[(object_id, generation)]:010d} {generation:05d} n \n".encode(
                "ascii"
            )
        )
    declared_size = max([0, *(object_id for object_id, _generation, _body in objects)]) + 1
    data.extend(
        b"trailer\n<< /Size "
        + str(declared_size).encode("ascii")
        + b" "
        + bytes(trailer_entries)
        + b" >>\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(data)


class PDFResourceInspectionTests(SimpleTestCase):
    def test_normal_pdf_passes_bounded_inspection(self):
        reader, report = inspect_pdf_attachment(text_pdf())

        self.assertEqual(len(reader.pages), 1)
        self.assertEqual(report["safety"]["object_count"], 7)
        self.assertEqual(report["fidelity"]["page_count"], 1)
        self.assertTrue(report["safety"]["decoded_stream_checks_complete"])
        self.assertLess(report["fidelity"]["max_page_area_points"], 1_000_000)

    def test_tiny_pdf_with_huge_media_box_is_rejected(self):
        data = blank_pdf(width=1_000_000, height=1_000_000)

        self.assertLess(len(data), 1024)
        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "dimensions exceed the safe processing limit",
        ):
            inspect_pdf_attachment(data)

    def test_page_user_unit_cannot_bypass_geometry_limit(self):
        output = BytesIO()
        writer = PdfWriter()
        page = writer.add_blank_page(width=100, height=100)
        page[NameObject("/UserUnit")] = NumberObject(1000)
        writer.write(output)

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "dimensions exceed the safe processing limit",
        ):
            inspect_pdf_attachment(output.getvalue())

    @override_settings(QUOTATION_IMPORT_MAX_PDF_OBJECTS=5)
    def test_object_limit_is_checked_before_page_extraction(self):
        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        for index in range(5):
            writer._add_object(DictionaryObject({}))
        writer.write(output)

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "too many objects",
        ):
            inspect_pdf_attachment(output.getvalue())

    @override_settings(
        QUOTATION_IMPORT_MAX_PDF_DECODED_STREAM_BYTES=1024,
        QUOTATION_IMPORT_MAX_PDF_TOTAL_DECODED_STREAM_BYTES=8192,
    )
    def test_flate_stream_expansion_is_bounded(self):
        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        stream = DecodedStreamObject()
        stream.set_data(b"A" * 4096)
        writer._add_object(stream.flate_encode())
        writer.write(output)

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "compressed stream that expands beyond the safe limit",
        ):
            inspect_pdf_attachment(output.getvalue())

    def test_completed_zlib_member_with_raw_deflate_polyglot_tail_is_rejected(self):
        data = flate_zlib_raw_polyglot()
        zlib_decoder = zlib.decompressobj(zlib.MAX_WBITS)
        zlib_decoder.decompress(data)
        raw_decoder = zlib.decompressobj(-zlib.MAX_WBITS)
        raw_decoder.decompress(data)
        self.assertTrue(zlib_decoder.eof)
        self.assertEqual(zlib_decoder.unused_data, b"evil")
        self.assertTrue(raw_decoder.eof)
        self.assertEqual(raw_decoder.unused_data, b"")

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "malformed or trailing Flate stream data",
        ):
            _bounded_flate_decode(data, 100_000)

    @override_settings(
        QUOTATION_IMPORT_MAX_PDF_OBJECTS=10,
        QUOTATION_IMPORT_MAX_PDF_DECODED_STREAM_BYTES=1024,
    )
    @patch("quotations.attachment_inspection.PdfReader")
    def test_xref_stream_expansion_is_bounded_before_pdf_reader(self, pdf_reader):
        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "compressed stream that expands beyond the safe limit",
        ):
            inspect_pdf_attachment(xref_stream_bomb_pdf())

        pdf_reader.assert_not_called()

    @override_settings(QUOTATION_IMPORT_MAX_PDF_OBJECTS=10)
    @patch("quotations.attachment_inspection.PdfReader")
    def test_classic_xref_declared_count_is_bounded_before_pdf_reader(
        self,
        pdf_reader,
    ):
        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "xref structure declares too many objects",
        ):
            inspect_pdf_attachment(oversized_classic_xref_pdf())

        pdf_reader.assert_not_called()

    @override_settings(
        QUOTATION_IMPORT_MAX_PDF_OBJECTS=10,
        QUOTATION_IMPORT_MAX_PDF_DECODED_STREAM_BYTES=1024,
    )
    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_xref_type_lexing_handles_comments_and_name_escapes_before_reader(
        self,
        pdf_reader,
    ):
        for type_entry in (b"/Type% split\n/XRef", b"/Type /X#52ef"):
            with self.subTest(type_entry=type_entry):
                with self.assertRaisesMessage(
                    PDFResourceLimitError,
                    "compressed stream that expands beyond the safe limit",
                ):
                    inspect_pdf_attachment(
                        xref_stream_bomb_pdf(type_entry=type_entry)
                    )

        pdf_reader.assert_not_called()

    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_indirect_xref_filter_is_not_treated_as_absent(self, pdf_reader):
        data = raw_xref_stream_pdf(
            dictionary_entries=(
                b"/Type /XRef /Size 1 /Index [0 1] /W [1 1 1] "
                b"/Filter 9 0 R"
            ),
            payload=b"\x00\x00\x00",
        )

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "indirect or invalid /Filter",
        ):
            inspect_pdf_attachment(data)

        pdf_reader.assert_not_called()

    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_xref_stream_length_mismatch_cannot_trigger_reader_recovery(
        self,
        pdf_reader,
    ):
        for label, declared_length in (("under", 1), ("over", 5)):
            with self.subTest(label=label):
                data = raw_xref_stream_pdf(
                    dictionary_entries=(
                        b"/Type /XRef /Size 1 /Index [0 1] /W [1 1 1] "
                        b"/Length "
                        + str(declared_length).encode("ascii")
                    ),
                    payload=b"\x01\x01\x01",
                    include_length=False,
                )
                with self.assertRaisesMessage(
                    PDFResourceLimitError,
                    "does not end at an exact endstream marker",
                ):
                    inspect_pdf_attachment(data)

        pdf_reader.assert_not_called()

    @override_settings(QUOTATION_IMPORT_MAX_PDF_OBJECTS=10)
    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_xref_size_index_and_width_bounds_run_before_reader(
        self,
        pdf_reader,
    ):
        cases = {
            "size": raw_xref_stream_pdf(
                dictionary_entries=b"/Type /XRef /Size 12 /W [1 1 1]",
            ),
            "index": raw_xref_stream_pdf(
                dictionary_entries=(
                    b"/Type /XRef /Size 2 /Index [0 3] /W [1 1 1]"
                ),
            ),
            "width": raw_xref_stream_pdf(
                dictionary_entries=b"/Type /XRef /Size 1 /W [1 9 1]",
            ),
        }
        for label, data in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(PDFResourceLimitError):
                    inspect_pdf_attachment(data)

        pdf_reader.assert_not_called()

    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_duplicate_xref_index_ranges_are_rejected_before_reader(
        self,
        pdf_reader,
    ):
        data = raw_xref_stream_pdf(
            dictionary_entries=(
                b"/Type /XRef /Size 2 /Index [1 1 1 1] /W [1 1 1]"
            ),
            payload=b"\x00\x00\x00\x01\x01\x00",
        )

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "duplicate object entries",
        ):
            inspect_pdf_attachment(data)

        pdf_reader.assert_not_called()

    @override_settings(QUOTATION_IMPORT_MAX_PDF_OBJECTS=10)
    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_aggregate_incremental_xref_entries_are_bounded(self, pdf_reader):
        data = bytearray(b"%PDF-1.4\n")
        previous_offset = None
        latest_offset = None
        free_entries = b"0000000000 65535 f \n" * 10
        for _revision in range(9):
            latest_offset = len(data)
            data.extend(b"xref\n0 10\n" + free_entries)
            data.extend(b"trailer\n<< /Size 10")
            if previous_offset is not None:
                data.extend(
                    b" /Prev " + str(previous_offset).encode("ascii")
                )
            data.extend(b" >>\n")
            previous_offset = latest_offset
        data.extend(
            b"startxref\n"
            + str(latest_offset).encode("ascii")
            + b"\n%%EOF\n"
        )

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "too many aggregate entries",
        ):
            inspect_pdf_attachment(bytes(data))

        pdf_reader.assert_not_called()

    @override_settings(QUOTATION_IMPORT_MAX_PDF_OBJECTS=10)
    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_leading_whitespace_classic_xref_is_still_bounded(self, pdf_reader):
        data = oversized_classic_xref_pdf().replace(
            b"%PDF-1.4\nxref",
            b"%PDF-1.4\n \t\r\nxref",
            1,
        ).replace(b"startxref\n9", b"startxref\n9", 1)

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "xref structure declares too many objects",
        ):
            inspect_pdf_attachment(data)

        pdf_reader.assert_not_called()

    @override_settings(QUOTATION_IMPORT_MAX_PDF_OBJECTS=10)
    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_prev_chain_is_followed_before_reader(self, pdf_reader):
        prefix = b"%PDF-1.4\n"
        previous_offset = len(prefix)
        previous = b" \txref\n0 100\n"
        latest_offset = previous_offset + len(previous)
        latest = (
            b"xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 1 /Prev "
            + str(previous_offset).encode("ascii")
            + b" >>\n"
        )
        data = (
            prefix
            + previous
            + latest
            + b"startxref\n"
            + str(latest_offset).encode("ascii")
            + b"\n%%EOF\n"
        )

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "xref structure declares too many objects",
        ):
            inspect_pdf_attachment(data)

        pdf_reader.assert_not_called()

    @override_settings(QUOTATION_IMPORT_MAX_PDF_OBJECTS=10)
    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_hybrid_xrefstm_chain_is_followed_before_reader(self, pdf_reader):
        prefix = b"%PDF-1.7\n"
        stream_offset = len(prefix)
        stream = (
            b"1 0 obj\n<< /Type /XRef /Size 100 /W [1 1 1] /Length 0 >>\n"
            b"stream\n\nendstream\nendobj\n"
        )
        classic_offset = stream_offset + len(stream)
        classic = (
            b"xref\n0 1\n0000000000 65535 f \ntrailer\n"
            b"<< /Size 2 /XRefStm "
            + str(stream_offset).encode("ascii")
            + b" >>\n"
        )
        data = (
            prefix
            + stream
            + classic
            + b"startxref\n"
            + str(classic_offset).encode("ascii")
            + b"\n%%EOF\n"
        )

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "xref structure declares too many objects",
        ):
            inspect_pdf_attachment(data)

        pdf_reader.assert_not_called()

    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_unsupported_object_stream_filter_is_rejected_before_reader(
        self,
        pdf_reader,
    ):
        data = classic_object_pdf(
            objects=[
                (
                    1,
                    0,
                    b"<< /Type /ObjStm /N 1 /First 4 /Length 8 "
                    b"/Filter /RunLengthDecode >>\nstream\n5 0 null\nendstream",
                )
            ],
            listed_keys=[(1, 0)],
        )

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "filters that cannot be decoded with a bounded preflight",
        ):
            inspect_pdf_attachment(data)

        pdf_reader.assert_not_called()

    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_compressed_entry_requires_exact_generation_zero_object_stream(
        self,
        pdf_reader,
    ):
        prefix = bytearray(b"%PDF-1.7\n")
        prefix.extend(
            b"2 0 obj\n<< /Type /ObjStm /N 1 /First 4 /Length 2 "
            b"/Filter /RunLengthDecode >>\nstream\nxx\nendstream\nendobj\n"
        )
        generation_one_offset = len(prefix)
        prefix.extend(
            b"2 1 obj\n<< /Type /ObjStm /N 1 /First 4 /Length 8 >>\n"
            b"stream\n5 0 null\nendstream\nendobj\n"
        )
        payload = (
            b"\x02\x00\x00\x00\x02\x00\x00"
            + b"\x01"
            + generation_one_offset.to_bytes(4, "big")
            + b"\x00\x01"
        )
        data = raw_xref_stream_pdf(
            dictionary_entries=(
                b"/Type /XRef /Size 4 /Index [1 2] /W [1 4 2]"
            ),
            payload=payload,
            object_id=3,
            before=bytes(prefix[len(b"%PDF-1.7\n") :]),
        )

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "unavailable object stream",
        ):
            inspect_pdf_attachment(data)

        pdf_reader.assert_not_called()

    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_compressed_xref_index_must_match_object_stream_header(
        self,
        pdf_reader,
    ):
        prefix = bytearray(b"%PDF-1.7\n")
        object_stream_offset = len(prefix)
        prefix.extend(
            b"2 0 obj\n<< /Type /ObjStm /N 1 /First 4 /Length 8 >>\n"
            b"stream\n5 0 null\nendstream\nendobj\n"
        )
        payload = (
            b"\x02\x00\x00\x00\x02\x00\x00"
            + b"\x01"
            + object_stream_offset.to_bytes(4, "big")
            + b"\x00\x00"
        )
        data = raw_xref_stream_pdf(
            dictionary_entries=(
                b"/Type /XRef /Size 4 /Index [1 2] /W [1 4 2]"
            ),
            payload=payload,
            object_id=3,
            before=bytes(prefix[len(b"%PDF-1.7\n") :]),
        )

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "does not match its object-stream header",
        ):
            inspect_pdf_attachment(data)

        pdf_reader.assert_not_called()

    @patch("quotations.attachment_inspection._PreflightBoundPdfReader")
    def test_prev_generation_conflict_cannot_hide_older_object_stream(
        self,
        pdf_reader,
    ):
        data = bytearray(b"%PDF-1.7\n")
        object_stream_offset = len(data)
        data.extend(
            b"2 0 obj\n<< /Type /ObjStm /N 1 /First 4 /Length 2 "
            b"/Filter /RunLengthDecode >>\nstream\nxx\nendstream\nendobj\n"
        )
        latest_object_offset = len(data)
        data.extend(b"1 1 obj\n<< /Producer (new revision) >>\nendobj\n")
        previous_xref_offset = len(data)
        previous_payload = (
            b"\x02\x00\x00\x00\x02\x00\x00"
            + b"\x01"
            + object_stream_offset.to_bytes(4, "big")
            + b"\x00\x00"
        )
        data.extend(
            b"3 0 obj\n<< /Type /XRef /Size 4 /Index [1 2] /W [1 4 2] "
            b"/Length "
            + str(len(previous_payload)).encode("ascii")
            + b" >>\nstream\n"
            + previous_payload
            + b"\nendstream\nendobj\n"
        )
        latest_xref_offset = len(data)
        data.extend(
            b"xref\n0 1\n0000000000 65535 f \n1 1\n"
            + f"{latest_object_offset:010d} 00001 n \n".encode("ascii")
            + b"trailer\n<< /Size 4 /Prev "
            + str(previous_xref_offset).encode("ascii")
            + b" >>\nstartxref\n"
            + str(latest_xref_offset).encode("ascii")
            + b"\n%%EOF\n"
        )

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "filters that cannot be decoded with a bounded preflight",
        ):
            inspect_pdf_attachment(bytes(data))

        pdf_reader.assert_not_called()

    def test_unlisted_referenced_object_cannot_use_pdf_reader_repair(self):
        data = classic_object_pdf(
            objects=[
                (1, 0, b"<< /Type /Catalog /Pages 2 0 R >>"),
                (2, 0, b"<< /Type /Pages /Count 0 /Kids [] >>"),
            ],
            listed_keys=[(1, 0)],
            trailer_entries=b"/Root 1 0 R",
        )

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "page structure could not be traversed safely",
        ):
            inspect_pdf_attachment(data)

    def test_unsupported_content_filter_skips_local_parsers(self):
        data = run_length_content_pdf(repeat_blocks=4000)
        with patch(
            "quotations.import_parsers._extract_pymupdf_text"
        ) as extract_text, patch(
            "quotations.import_parsers._parse_pdfplumber_tables"
        ) as parse_tables:
            preview = parse_pdf_preview(
                data,
                "run-length.pdf",
                PDF_MIME,
                hashlib.sha256(data).hexdigest(),
            )

        self.assertEqual(
            preview["parse_method"],
            "bounded_pdf_local_extraction_skipped_v1",
        )
        self.assertEqual(preview["lines"], [])
        self.assertFalse(
            preview["meta"]["attachment_safety"]["local_traversal_safe"]
        )
        self.assertTrue(
            any("Local PDF extraction was skipped" in value for value in preview["warnings"])
        )
        extract_text.assert_not_called()
        parse_tables.assert_not_called()

    @patch("quotations.ai_parsing.fitz.open")
    def test_in_memory_ai_render_rechecks_unsupported_filter_before_fitz(
        self,
        fitz_open,
    ):
        with self.assertRaisesMessage(
            AIParseError,
            "streams cannot be decoded with a bounded in-process preflight",
        ):
            _render_pdf_bytes_images(run_length_content_pdf(repeat_blocks=4000))

        fitz_open.assert_not_called()

    @patch("quotations.ai_parsing.fitz.open")
    def test_inline_image_content_conservatively_blocks_local_render(
        self,
        fitz_open,
    ):
        data = inline_image_content_pdf()

        _, report = inspect_pdf_attachment(data)

        self.assertFalse(report["safety"]["local_traversal_safe"])
        self.assertEqual(report["fidelity"]["inline_image_page_count"], 1)
        with self.assertRaisesMessage(
            AIParseError,
            "streams cannot be decoded with a bounded in-process preflight",
        ):
            _render_pdf_bytes_images(data)
        fitz_open.assert_not_called()

    @patch("quotations.ai_parsing.fitz.open")
    def test_form_inline_image_content_blocks_local_render(self, fitz_open):
        data = form_inline_image_content_pdf()

        _, report = inspect_pdf_attachment(data)

        self.assertFalse(report["safety"]["local_traversal_safe"])
        self.assertEqual(report["fidelity"]["inline_image_page_count"], 1)
        with self.assertRaisesMessage(
            AIParseError,
            "streams cannot be decoded with a bounded in-process preflight",
        ):
            _render_pdf_bytes_images(data)
        fitz_open.assert_not_called()

    @patch("quotations.ai_parsing.fitz.open")
    def test_annotation_appearance_inline_image_blocks_local_render(self, fitz_open):
        data = annotation_inline_image_content_pdf()

        _, report = inspect_pdf_attachment(data)

        self.assertFalse(report["safety"]["local_traversal_safe"])
        self.assertEqual(report["fidelity"]["inline_image_page_count"], 1)
        with self.assertRaisesMessage(
            AIParseError,
            "streams cannot be decoded with a bounded in-process preflight",
        ):
            _render_pdf_bytes_images(data)
        fitz_open.assert_not_called()

    def test_flate_wrapped_jpeg_is_bounded_and_available_to_ai_render(self):
        data = flate_wrapped_jpeg_image_pdf()

        _, report = inspect_pdf_attachment(data)

        self.assertTrue(report["safety"]["local_traversal_safe"])
        self.assertTrue(report["safety"]["decoded_stream_checks_complete"])
        self.assertEqual(report["safety"]["unbounded_decoded_stream_count"], 0)
        self.assertEqual(report["safety"]["bounded_image_prefix_count"], 1)
        self.assertGreater(report["safety"]["bounded_image_prefix_bytes"], 0)
        self.assertEqual(report["fidelity"]["unsafe_image_filter_count"], 0)
        rendered, rendered_pages = _render_pdf_bytes_images(data)
        self.assertEqual(rendered_pages, 1)
        self.assertEqual(len(rendered), 1)
        self.assertTrue(rendered[0].startswith("data:image/png;base64,"))

    def test_indirect_aligned_decode_parameters_remain_supported(self):
        _, report = inspect_pdf_attachment(
            flate_wrapped_jpeg_image_pdf(indirect_decode_parameters=True)
        )

        self.assertTrue(report["safety"]["local_traversal_safe"])
        self.assertEqual(report["safety"]["bounded_image_prefix_count"], 1)
        self.assertEqual(report["fidelity"]["unsafe_image_filter_count"], 0)

    def test_ascii_wrapped_flate_jpeg_chains_are_bounded(self):
        for ascii_filter in ("ascii85", "asciihex"):
            with self.subTest(ascii_filter=ascii_filter):
                _, report = inspect_pdf_attachment(
                    flate_wrapped_jpeg_image_pdf(ascii_filter=ascii_filter)
                )

                self.assertTrue(report["safety"]["local_traversal_safe"])
                self.assertEqual(report["safety"]["bounded_image_prefix_count"], 1)
                self.assertEqual(report["fidelity"]["unsafe_image_filter_count"], 0)

    @override_settings(
        QUOTATION_IMPORT_MAX_PDF_DECODED_STREAM_BYTES=8192,
        QUOTATION_IMPORT_MAX_PDF_TOTAL_DECODED_STREAM_BYTES=32768,
    )
    def test_flate_wrapped_jpeg_prefix_still_honors_per_stream_limit(self):
        data = flate_wrapped_jpeg_image_pdf(intermediate_bytes=b"x" * 16384)

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "compressed stream that expands beyond the safe limit",
        ):
            inspect_pdf_attachment(data)

    @override_settings(
        QUOTATION_IMPORT_MAX_PDF_DECODED_STREAM_BYTES=8192,
        QUOTATION_IMPORT_MAX_PDF_TOTAL_DECODED_STREAM_BYTES=1000,
    )
    def test_flate_wrapped_jpeg_prefixes_share_aggregate_limit(self):
        data = flate_wrapped_jpeg_image_pdf(image_count=2)

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "decoded streams exceed the safe aggregate processing limit",
        ):
            inspect_pdf_attachment(data)

    def test_malformed_or_trailing_flate_wrappers_remain_blocked(self):
        for options in (
            {"truncate_flate": True},
            {"trailing_bytes": b"unexpected"},
        ):
            with self.subTest(options=options):
                with self.assertRaisesMessage(
                    PDFResourceLimitError,
                    "malformed or trailing Flate stream data",
                ):
                    inspect_pdf_attachment(flate_wrapped_jpeg_image_pdf(**options))

    def test_flate_wrapper_predictors_are_not_treated_as_safe(self):
        _, report = inspect_pdf_attachment(
            flate_wrapped_jpeg_image_pdf(flate_predictor=12)
        )

        self.assertFalse(report["safety"]["local_traversal_safe"])
        self.assertEqual(report["safety"]["unbounded_decoded_stream_count"], 1)
        self.assertEqual(report["fidelity"]["unsafe_image_filter_count"], 1)

    @patch("quotations.ai_parsing.fitz.open")
    def test_unsupported_image_filter_blocks_local_render(self, fitz_open):
        data = unsupported_filter_image_pdf()

        _, report = inspect_pdf_attachment(data)

        self.assertFalse(report["safety"]["local_traversal_safe"])
        self.assertEqual(report["fidelity"]["unsafe_image_filter_count"], 1)
        with self.assertRaisesMessage(
            AIParseError,
            "streams cannot be decoded with a bounded in-process preflight",
        ):
            _render_pdf_bytes_images(data)
        fitz_open.assert_not_called()

    @patch(
        "quotations.historical_import_parsers.store_import_source",
        return_value="historical_sources/run-length.pdf",
    )
    @patch("quotations.historical_import_parsers.pdfplumber.open")
    @patch("quotations.historical_import_parsers._extract_pdf_text")
    def test_unsupported_filter_skips_historical_local_parsers(
        self,
        extract_text,
        pdfplumber_open,
        _store,
    ):
        data = run_length_content_pdf(repeat_blocks=4000)
        preview = parse_historical_pdf_upload(
            SimpleUploadedFile(
                "historical-run-length.pdf",
                data,
                content_type=PDF_MIME,
            )
        )

        self.assertEqual(
            preview["parse_method"],
            "bounded_pdf_local_extraction_skipped_v1",
        )
        self.assertEqual(preview["lines"], [])
        extract_text.assert_not_called()
        pdfplumber_open.assert_not_called()

    def test_normal_multipage_scan_is_not_rejected_by_document_image_sum(self):
        data = multipage_scan_pdf()

        reader, report = inspect_pdf_attachment(data)

        self.assertEqual(len(reader.pages), 3)
        self.assertEqual(report["fidelity"]["image_object_count"], 3)
        self.assertGreater(
            report["fidelity"]["estimated_image_bytes"],
            64 * 1024 * 1024,
        )
        self.assertTrue(
            any("multiple high-resolution images" in value for value in report["warnings"])
        )

    def test_single_page_image_aggregate_is_bounded_before_render(self):
        data = single_page_multi_image_pdf()

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "aggregate pixel count exceeds the safe limit",
        ):
            inspect_pdf_attachment(data)

    @override_settings(
        QUOTATION_IMPORT_MAX_PDF_TEXT_CHARS_PER_PAGE=50,
        QUOTATION_IMPORT_MAX_PDF_TOTAL_TEXT_CHARS=1000,
    )
    @patch("quotations.import_parsers._parse_pdfplumber_tables")
    def test_manual_pdf_text_limit_stops_before_table_parser(self, parse_tables):
        data = text_pdf("A" * 200)

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "too much extracted text",
        ):
            parse_pdf_preview(
                data,
                "large-text.pdf",
                PDF_MIME,
                hashlib.sha256(data).hexdigest(),
            )

        parse_tables.assert_not_called()

    @override_settings(
        QUOTATION_IMPORT_MAX_PDF_TEXT_CHARS_PER_PAGE=50,
        QUOTATION_IMPORT_MAX_PDF_TOTAL_TEXT_CHARS=1000,
    )
    def test_historical_pdf_text_limit_is_hard_failure(self):
        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "too much extracted text",
        ):
            _extract_pdf_text(text_pdf("B" * 200))

    @patch(
        "quotations.import_parsers._extract_pymupdf_text",
        return_value=(["Sterile Gauze 5 PCS"], ["Sterile Gauze | 5 | PCS"], []),
    )
    @patch(
        "quotations.import_parsers._parse_pdfplumber_tables",
        side_effect=PDFResourceLimitError("PDF table extraction exceeds the safe row limit."),
    )
    def test_table_resource_failure_is_not_downgraded_to_fallback_warning(
        self,
        _parse_tables,
        _extract_text,
    ):
        data = text_pdf()

        with self.assertRaisesMessage(
            PDFResourceLimitError,
            "table extraction exceeds the safe row limit",
        ):
            parse_pdf_preview(
                data,
                "table-limit.pdf",
                PDF_MIME,
                hashlib.sha256(data).hexdigest(),
            )

    @patch(
        "quotations.import_parsers._extract_pymupdf_text",
        side_effect=RuntimeError("native parser failure"),
    )
    def test_native_pdf_parser_failure_becomes_validation_error(self, _extract_text):
        data = text_pdf()

        with self.assertRaisesMessage(
            ValidationError,
            "Could not safely extract text from this PDF",
        ):
            parse_pdf_preview(
                data,
                "malformed.pdf",
                PDF_MIME,
                hashlib.sha256(data).hexdigest(),
            )

    def test_historical_page_text_failure_becomes_validation_error(self):
        page = MagicMock()
        page.extract_text.side_effect = RuntimeError("parser failure")
        reader = SimpleNamespace(pages=[page], is_encrypted=False)

        with self.assertRaisesMessage(
            ValidationError,
            "Could not safely extract text from historical PDF page 1",
        ):
            _extract_pdf_text(b"%PDF-1.4", reader=reader)


class GmailPDFResourceInspectionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="pdf-gmail-reviewer",
            is_staff=True,
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="quotes@example.com",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        quotation_settings = QuotationSettings.get_solo()
        quotation_settings.ai_pdf_vision_enabled = True
        quotation_settings.save(
            update_fields=["ai_pdf_vision_enabled", "updated_at"]
        )
        self.gmail_import = GmailInquiryImport.objects.create(
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_thread_id="pdf-resource-thread",
            anchor_message_id="pdf-resource-message",
            selected_message_ids=["pdf-resource-message"],
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
            status=GmailInquiryImport.STATUS_CLAIMED,
            claimed_by=self.staff,
            claimed_at=timezone.now(),
        )

    @override_settings(QUOTATION_MAILBOX_AI_VISION_ENABLED=True)
    @patch("quotations.gmail_inquiry_import._run_native_thread_analysis")
    def test_huge_page_blocks_gmail_provider_call(self, run_native_analysis):
        data = blank_pdf(width=1_000_000, height=1_000_000)
        inline_data = base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
        attachment = {
            "filename": "huge-page.pdf",
            "mime_type": PDF_MIME,
            "size": len(data),
            "attachment_id": "huge-page",
            "part_id": "1",
        }
        message = {
            "gmail_message_id": "pdf-resource-message",
            "gmail_thread_id": "pdf-resource-thread",
            "label_ids": ["INBOX"],
            "subject": "Request for quotation",
            "sender": "Buyer <buyer@example.com>",
            "recipients": self.connection.email,
            "cc": "",
            "reply_to": "",
            "sent_at": timezone.now(),
            "snippet": "Please quote the attachment.",
            "newest_body_text": "Please quote the attachment.",
            "newest_body_html": "",
            "attachment_manifest": [attachment],
            "_attachment_refs": [{**attachment, "_inline_data": inline_data}],
        }

        result = _build_source_analysis(
            [message],
            self.connection,
            self.gmail_import,
            self.staff,
            timeline_messages=[message],
        )

        run_native_analysis.assert_not_called()
        self.assertEqual(result["attachment_manifest"][0]["parse_status"], "failed")
        self.assertEqual(result["preview"]["lines"], [])
        self.assertFalse(result["preview"]["meta"]["ai_used"])


class HistoricalPDFRasterBoundsTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="pdf-raster-reviewer",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)
        self.historical_import = HistoricalPriceImport.objects.create(
            source_filename="huge-page.pdf",
            source_sha256="a" * 64,
            source_file_ref="historical_sources/huge-page.pdf",
            created_by=self.staff,
        )

    def test_manual_stored_ai_path_blocks_unsafe_pdf_before_fitz(self):
        data = run_length_content_pdf(repeat_blocks=4000)
        preview = {
            "source_type": "pdf",
            "source_filename": "unsafe.pdf",
            "source_mime_type": PDF_MIME,
            "source_file_ref": "imports/unsafe.pdf",
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "meta": {"page_count": 1},
            "lines": [],
        }
        with patch(
            "quotations.ai_parsing._assert_ai_allowed"
        ), patch(
            "quotations.ai_parsing._select_mode",
            return_value="vision",
        ), patch(
            "quotations.ai_parsing.read_private_ref",
            return_value=data,
        ), patch(
            "quotations.ai_parsing.fitz.open"
        ) as fitz_open:
            with self.assertRaisesMessage(
                AIParseError,
                "streams cannot be decoded with a bounded in-process preflight",
            ):
                clean_preview_with_ai(
                    preview,
                    actor=self.staff,
                    requested_mode="vision",
                )

        fitz_open.assert_not_called()

    def test_historical_ai_path_blocks_unsafe_pdf_before_fitz(self):
        data = run_length_content_pdf(repeat_blocks=4000)
        with patch(
            "quotations.ai_parsing._assert_ai_allowed"
        ), patch(
            "quotations.ai_parsing._select_mode",
            return_value="vision",
        ), patch(
            "quotations.ai_parsing.read_private_ref",
            return_value=data,
        ), patch(
            "quotations.ai_parsing.fitz.open"
        ) as fitz_open:
            with self.assertRaisesMessage(
                AIParseError,
                "streams cannot be decoded with a bounded in-process preflight",
            ):
                clean_historical_import_with_ai(
                    self.historical_import,
                    actor=self.staff,
                    requested_mode="vision",
                )

        fitz_open.assert_not_called()

    @patch("quotations.views.read_private_ref")
    @patch("quotations.views.fitz.open")
    def test_preview_rejects_geometry_before_pixmap_render(self, fitz_open, read):
        read.return_value = blank_pdf(width=1_000_000, height=1_000_000)

        response = self.client.get(
            reverse(
                "quotation-historical-import-preview-page",
                args=[self.historical_import.id],
            )
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("dimensions exceed", response.data["detail"])
        fitz_open.assert_not_called()

    @override_settings(
        QUOTATION_IMPORT_MAX_PDF_PAGE_AREA_POINTS=5_000_000,
        QUOTATION_IMPORT_MAX_PDF_RENDER_PIXELS=1_000_000,
    )
    @patch("quotations.views.read_private_ref")
    @patch("quotations.views.fitz.open")
    def test_preview_checks_render_pixels_before_get_pixmap(self, fitz_open, read):
        read.return_value = blank_pdf(width=2000, height=2000)
        page = MagicMock()
        page.rect = SimpleNamespace(width=2000, height=2000)
        document = MagicMock()
        document.__enter__.return_value = document
        document.__exit__.return_value = False
        document.__len__.return_value = 1
        document.__getitem__.return_value = page
        fitz_open.return_value = document

        response = self.client.get(
            reverse(
                "quotation-historical-import-preview-page",
                args=[self.historical_import.id],
            )
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("safe raster preview size", response.data["detail"])
        page.get_pixmap.assert_not_called()
