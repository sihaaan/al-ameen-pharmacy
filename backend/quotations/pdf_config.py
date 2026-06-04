from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


@dataclass(frozen=True)
class QuotationPDFConfig:
    company_name: str
    company_name_ar: str
    address: str
    phone: str
    email: str
    trn: str
    logo_path: str
    signature_image_path: str
    stamp_image_path: str
    logo_layout: str
    footer_note: str
    default_terms: str
    validity_days: int
    payment_terms: str
    license_number: str
    prepared_by_default: str
    signature_label: str
    stamp_label: str
    pdf_template_style: str
    primary_color: str
    accent_color: str
    show_arabic_name: bool
    show_trn: bool
    show_license_number: bool
    show_signature_area: bool
    show_stamp_area: bool


def _default_logo_path():
    candidate = Path(settings.BASE_DIR).parent / "frontend" / "public" / "brand" / "al-ameen-pharmacy-logo-dark.png"
    return str(candidate) if candidate.exists() else ""


def _settings_image_source(image_field):
    if not image_field:
        return ""
    try:
        if hasattr(image_field, "path") and Path(image_field.path).exists():
            return image_field.path
    except (NotImplementedError, ValueError):
        pass
    try:
        return image_field.url
    except ValueError:
        return ""


def _user_signature_image_source(user):
    if not user or not getattr(user, "is_authenticated", False):
        return ""
    try:
        profile = getattr(user, "quotation_profile", None)
    except Exception:
        return ""
    if not profile:
        return ""
    return _settings_image_source(profile.signature_image)


def _get_saved_settings():
    try:
        from .models import QuotationSettings

        return QuotationSettings.objects.filter(pk=1).first()
    except Exception:
        return None


def get_quotation_pdf_config(quotation=None):
    settings_obj = _get_saved_settings()
    if settings_obj:
        user_signature_path = _user_signature_image_source(getattr(quotation, "created_by", None))
        return QuotationPDFConfig(
            company_name=settings_obj.company_name,
            company_name_ar=settings_obj.company_name_ar if settings_obj.show_arabic_name else "",
            address=settings_obj.address,
            phone=settings_obj.phone,
            email=settings_obj.email,
            trn=settings_obj.trn if settings_obj.show_trn else "",
            logo_path=_settings_image_source(settings_obj.logo) or _default_logo_path(),
            signature_image_path=user_signature_path or _settings_image_source(settings_obj.signature_image),
            stamp_image_path=_settings_image_source(settings_obj.stamp_image),
            logo_layout=settings_obj.logo_layout,
            footer_note=settings_obj.footer_note,
            default_terms=settings_obj.default_terms,
            validity_days=settings_obj.validity_days or 30,
            payment_terms=settings_obj.payment_terms,
            license_number=settings_obj.license_number if settings_obj.show_license_number else "",
            prepared_by_default=settings_obj.prepared_by_default,
            signature_label=settings_obj.signature_label,
            stamp_label=settings_obj.stamp_label,
            pdf_template_style=settings_obj.pdf_template_style,
            primary_color=settings_obj.primary_color,
            accent_color=settings_obj.accent_color,
            show_arabic_name=settings_obj.show_arabic_name,
            show_trn=settings_obj.show_trn,
            show_license_number=settings_obj.show_license_number,
            show_signature_area=settings_obj.show_signature_area,
            show_stamp_area=settings_obj.show_stamp_area,
        )

    user_signature_path = _user_signature_image_source(getattr(quotation, "created_by", None))
    return QuotationPDFConfig(
        company_name=getattr(settings, "QUOTATION_COMPANY_NAME", "Al Ameen Pharmacy"),
        company_name_ar=getattr(settings, "QUOTATION_COMPANY_NAME_AR", ""),
        address=getattr(settings, "QUOTATION_COMPANY_ADDRESS", "Dubai, United Arab Emirates"),
        phone=getattr(settings, "QUOTATION_COMPANY_PHONE", "+971 50 545 6388"),
        email=getattr(settings, "QUOTATION_COMPANY_EMAIL", "alameenpharmacyllc@gmail.com"),
        trn=getattr(settings, "QUOTATION_COMPANY_TRN", ""),
        logo_path=getattr(settings, "QUOTATION_LOGO_PATH", _default_logo_path()),
        signature_image_path=user_signature_path or getattr(settings, "QUOTATION_SIGNATURE_IMAGE_PATH", ""),
        stamp_image_path=getattr(settings, "QUOTATION_STAMP_IMAGE_PATH", ""),
        logo_layout=getattr(settings, "QUOTATION_LOGO_LAYOUT", "full_logo_only"),
        footer_note="",
        default_terms=getattr(
            settings,
            "QUOTATION_DEFAULT_TERMS",
            "Prices are subject to stock availability and final confirmation. This quotation is confidential and intended for the named customer only.",
        ),
        validity_days=int(getattr(settings, "QUOTATION_VALIDITY_DAYS", 30) or 30),
        payment_terms=getattr(settings, "QUOTATION_PAYMENT_TERMS", "Credit 30 days"),
        license_number="",
        prepared_by_default="",
        signature_label="Signature",
        stamp_label="Stamp",
        pdf_template_style="classic",
        primary_color="#0F766E",
        accent_color="#ECFDF5",
        show_arabic_name=True,
        show_trn=True,
        show_license_number=True,
        show_signature_area=True,
        show_stamp_area=True,
    )
