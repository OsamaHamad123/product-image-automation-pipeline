# verification_layer/infrastructure/resilient_image_fetcher.py
"""
Resilient Image Ingestion Cascade with 12-byte Magic Bytes Decoding & WAF Evasion.
Fallback: Direct Host -> Google WebCache (http://webcache.googleusercontent.com/search?q=cache:URL).
"""

from curl_cffi.requests import AsyncSession


class ResilientImageFetcher:
    def __init__(self, timeout_sec: int = 5):
        self.timeout = timeout_sec
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

    def decode_magic_bytes(self, buffer: bytes) -> str:
        """
        Extracts MIME type from the first 12 magic bytes of the image payload.
        """
        if len(buffer) < 4:
            raise ValueError("Incomplete binary content")

        if buffer.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if buffer.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if buffer.startswith(b"RIFF") and len(buffer) >= 12 and buffer[8:12] == b"WEBP":
            return "image/webp"
        if len(buffer) >= 12 and buffer[4:12] in (b"ftypavif", b"ftypavis"):
            return "image/avif"
        return "application/octet-stream"

    async def fetch_image(self, url: str) -> bytes:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            "Referer": "https://www.google.com/",
            "Connection": "keep-alive"
        }

        # Attempt 1: Direct Host Fetch with Chrome 120 TLS Impersonation
        try:
            async with AsyncSession() as session:
                response = await session.get(
                    url,
                    headers=headers,
                    impersonate="chrome120",
                    timeout=self.timeout
                )
                if response.status_code == 200:
                    data = response.content
                    if self.decode_magic_bytes(data) != "application/octet-stream":
                        return data
        except Exception:
            pass

        # Attempt 2: Fallback Cascade to Google WebCache Proxy
        cached_url = f"http://webcache.googleusercontent.com/search?q=cache:{url}"
        try:
            async with AsyncSession() as session:
                response = await session.get(
                    cached_url,
                    headers={"User-Agent": self.user_agent},
                    impersonate="chrome120",
                    timeout=self.timeout
                )
                if response.status_code == 200:
                    data = response.content
                    if self.decode_magic_bytes(data) != "application/octet-stream":
                        return data
        except Exception as e:
            raise RuntimeError(f"All retrieval attempts failed in cascade: {str(e)}")

        raise ValueError("Content retrieved is corrupted or format is unknown")
