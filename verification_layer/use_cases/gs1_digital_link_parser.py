# verification_layer/use_cases/gs1_digital_link_parser.py
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class GS1DigitalLinkData:
    is_valid: bool
    raw_uri: str
    gtin_14: Optional[str] = None
    batch_lot: Optional[str] = None
    serial_number: Optional[str] = None
    gln: Optional[str] = None
    canonical_url: Optional[str] = None


class GS1DigitalLinkParser:
    """
    محلل معيار الرابط الرقمي لـ GS1 (GS1 Digital Link URI Parser)
    يفك رموز معرفات التطبيق (Application Identifiers):
    - AI 01: رقم بند التجارة العالمي (GTIN-14)
    - AI 10: رقم دفعة الإنتاج (Batch/Lot Number)
    - AI 21: الرقم التسلسلي الفردي للسلعة (Serial Number)
    - AI 414/417: رقم الموقع العالمي (GLN)
    """

    # Regex patterns for GS1 Digital Link URI format
    GTIN_PATTERN = re.compile(r"/01/(\d{14})")
    BATCH_PATTERN = re.compile(r"/10/([a-zA-Z0-9_-]+)")
    SERIAL_PATTERN = re.compile(r"/21/([a-zA-Z0-9_-]+)")
    GLN_PATTERN = re.compile(r"/(?:414|417)/(\d{13})")

    @classmethod
    def parse_uri(cls, uri_str: str) -> GS1DigitalLinkData:
        if not uri_str:
            return GS1DigitalLinkData(is_valid=False, raw_uri="")

        clean_uri = uri_str.strip()
        gtin_match = cls.GTIN_PATTERN.search(clean_uri)

        if not gtin_match:
            return GS1DigitalLinkData(is_valid=False, raw_uri=clean_uri)

        gtin = gtin_match.group(1)
        batch = cls.BATCH_PATTERN.search(clean_uri)
        serial = cls.SERIAL_PATTERN.search(clean_uri)
        gln = cls.GLN_PATTERN.search(clean_uri)

        canonical_url = f"https://id.gs1.org/01/{gtin}"

        return GS1DigitalLinkData(
            is_valid=True,
            raw_uri=clean_uri,
            gtin_14=gtin,
            batch_lot=batch.group(1) if batch else None,
            serial_number=serial.group(1) if serial else None,
            gln=gln.group(1) if gln else None,
            canonical_url=canonical_url,
        )

    @classmethod
    def build_gs1_uri(cls, domain: str, gtin_14: str, batch: Optional[str] = None, serial: Optional[str] = None) -> str:
        clean_gtin = re.sub(r"\D", "", gtin_14).zfill(14)
        base_domain = domain.rstrip("/")
        uri = f"{base_domain}/01/{clean_gtin}"
        if batch:
            uri += f"/10/{batch}"
        if serial:
            uri += f"/21/{serial}"
        return uri
