# verification_layer/use_cases/image_validation_service.py
"""
Image Validation Service for Pre-Cache Verification.
Performs HEAD probe checks & 2048-byte Magic Bytes Binary Signature Verification.
"""

from typing import Tuple, Optional
from curl_cffi.requests import AsyncSession


class ImageValidationService:
    MAGIC_SIGNATURES = {
        b"\x89PNG\r\n\x1a\n": "image/png",
        b"\xff\xd8\xff": "image/jpeg",
        b"GIF8": "image/gif",
        b"RIFF": "image/webp"
    }

    @classmethod
    async def verify_integrity(cls, url: str, timeout: float = 4.0) -> Tuple[bool, Optional[str]]:
        headers = {
            "User-Agent": "E-Commerce-Catalog-Engine/2.0",
            "Range": "bytes=0-2047",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
        }
        try:
            async with AsyncSession() as session:
                # 1. Probe lightweight HEAD request
                try:
                    probe = await session.head(url, headers=headers, impersonate="chrome120", timeout=timeout)
                    if probe.status_code >= 400:
                        return False, f"HTTP Error {probe.status_code}"
                    
                    content_len = probe.headers.get("Content-Length")
                    if content_len and int(content_len) == 0:
                        return False, "Zero byte payload detected"
                except Exception:
                    # Proceed to GET Range probe if HEAD is restricted
                    pass

                # 2. Streaming Range GET probe for Magic Bytes (first 2048 bytes)
                response = await session.get(url, headers=headers, impersonate="chrome120", timeout=timeout)
                if response.status_code >= 400:
                    return False, f"Stream error status {response.status_code}"

                binary_chunk = response.content[:2048]
                if not binary_chunk:
                    return False, "No streamable data received"

                detected_mime = None
                for signature, mime in cls.MAGIC_SIGNATURES.items():
                    if binary_chunk.startswith(signature):
                        detected_mime = mime
                        break

                if not detected_mime and binary_chunk.startswith(b"RIFF"):
                    if len(binary_chunk) >= 12 and binary_chunk[8:12] == b"WEBP":
                        detected_mime = "image/webp"

                if not detected_mime and len(binary_chunk) >= 12 and binary_chunk[4:12] in (b"ftypavif", b"ftypavis"):
                    detected_mime = "image/avif"

                if not detected_mime:
                    return False, "Failed magic bytes validation"

                return True, detected_mime

        except Exception as err:
            return False, f"Network communication error: {str(err)}"
