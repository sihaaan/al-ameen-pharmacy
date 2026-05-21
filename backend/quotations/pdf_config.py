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
    default_terms: str
    validity_days: int
    payment_terms: str


def _default_logo_path():
    candidate = Path(settings.BASE_DIR).parent / "frontend" / "public" / "brand" / "al-ameen-pharmacy-logo-dark.png"
    return str(candidate) if candidate.exists() else ""


def get_quotation_pdf_config():
    return QuotationPDFConfig(
        company_name=getattr(settings, "QUOTATION_COMPANY_NAME", "Al Ameen Pharmacy"),
        company_name_ar=getattr(settings, "QUOTATION_COMPANY_NAME_AR", ""),
        address=getattr(settings, "QUOTATION_COMPANY_ADDRESS", "Dubai, United Arab Emirates"),
        phone=getattr(settings, "QUOTATION_COMPANY_PHONE", "+971 50 545 6388"),
        email=getattr(settings, "QUOTATION_COMPANY_EMAIL", "alameenpharmacyllc@gmail.com"),
        trn=getattr(settings, "QUOTATION_COMPANY_TRN", ""),
        logo_path=getattr(settings, "QUOTATION_LOGO_PATH", _default_logo_path()),
        default_terms=getattr(
            settings,
            "QUOTATION_DEFAULT_TERMS",
            "Prices are subject to stock availability and final confirmation. This quotation is confidential and intended for the named customer only.",
        ),
        validity_days=int(getattr(settings, "QUOTATION_VALIDITY_DAYS", 14) or 14),
        payment_terms=getattr(settings, "QUOTATION_PAYMENT_TERMS", "Payment terms to be confirmed with the customer."),
    )
