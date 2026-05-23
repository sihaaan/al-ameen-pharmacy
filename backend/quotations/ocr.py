class OCRProviderUnavailable(Exception):
    pass


class BaseOCRProvider:
    name = "base"

    def extract_pdf(self, *, data, filename=""):
        raise OCRProviderUnavailable("OCR provider is not configured.")


class LocalTesseractOCRProvider(BaseOCRProvider):
    name = "local_tesseract"

    def extract_pdf(self, *, data, filename=""):
        raise OCRProviderUnavailable(
            "Local Tesseract OCR is not enabled in this environment. "
            "Install and configure Tesseract separately before using this provider."
        )


class GoogleDocumentAIOCRProvider(BaseOCRProvider):
    name = "google_document_ai"

    def extract_pdf(self, *, data, filename=""):
        raise OCRProviderUnavailable(
            "Google Document AI OCR is not configured. Configure a managed OCR provider before enabling OCR imports."
        )


OCR_PROVIDERS = {
    LocalTesseractOCRProvider.name: LocalTesseractOCRProvider,
    GoogleDocumentAIOCRProvider.name: GoogleDocumentAIOCRProvider,
}


def get_ocr_provider(provider_name):
    provider_class = OCR_PROVIDERS.get(provider_name or "")
    if not provider_class:
        raise OCRProviderUnavailable("OCR is not enabled in this environment.")
    return provider_class()
