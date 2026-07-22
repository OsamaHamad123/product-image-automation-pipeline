# verification_layer/use_cases/image_proxy_service.py
"""
SSRF Protection Validator & Secure Image Proxy Service.
Validates target host IP against forbidden private/loopback/cloud networks,
pins connections to resolved IPs to prevent DNS Rebinding,
and inspects streaming Magic Bytes for binary image verification.
"""

import socket
import ipaddress
import urllib.parse
import base64
from typing import Dict, Any, Tuple, Optional


# =====================================================================
# 1. SSRF Protection & Network Isolation
# =====================================================================

BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # Link-Local & AWS IMDSv1/v2
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT & Alibaba Cloud
    ipaddress.ip_network("192.0.0.0/24"),     # Oracle Cloud IMDS
    ipaddress.ip_network("fc00::/7"),         # IPv6 Private
    ipaddress.ip_network("::1/128")           # IPv6 Loopback
]


class SSRFProtectionValidator:
    """Performs strict DNS resolution and SSRF blocklist checking."""

    @staticmethod
    def resolve_and_validate_host(hostname: str) -> str:
        try:
            # Resolve all IPv4 and IPv6 addresses associated with the hostname
            ip_addresses = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
            if not ip_addresses:
                raise ValueError("Unable to resolve target host address.")

            for family, _, _, _, sockaddr in ip_addresses:
                resolved_ip = sockaddr[0]
                ip_obj = ipaddress.ip_address(resolved_ip)

                for blocked_net in BLOCKED_NETWORKS:
                    if ip_obj in blocked_net:
                        raise ValueError(f"SSRF Prevention: Target resolves to forbidden address: {resolved_ip}")

            # Return pinned IP for connection pinning
            return ip_addresses[0][4][0]
        except socket.gaierror:
            raise ValueError(f"Host resolution failed for: {hostname}")


# =====================================================================
# 2. Magic Bytes Binary Signature Inspection
# =====================================================================

MAGIC_BYTES_SIGNATURES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif"
}


def detect_magic_bytes_mime(header_bytes: bytes) -> Optional[str]:
    """Detect genuine image MIME type from binary magic bytes signature."""
    for sig, mime in MAGIC_BYTES_SIGNATURES.items():
        if header_bytes.startswith(sig):
            return mime
    if header_bytes.startswith(b"RIFF") and b"WEBP" in header_bytes[8:16]:
        return "image/webp"
    return None


# =====================================================================
# 3. Image Proxy Service
# =====================================================================

class ImageProxyService:
    """Service handling pre-flight checks, SSRF validation, and Base64 formatting."""

    def __init__(self, validator=None):
        self.validator = validator or SSRFProtectionValidator()

    def prepare_proxy_request(self, target_url: str) -> Tuple[str, Dict[str, str]]:
        parsed = urllib.parse.urlparse(target_url)
        if parsed.scheme not in ["http", "https"]:
            raise ValueError("Only HTTP and HTTPS protocols are supported.")

        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Invalid target URL hostname.")

        pinned_ip = self.validator.resolve_and_validate_host(hostname)
        port = parsed.port if parsed.port else (443 if parsed.scheme == "https" else 80)
        
        secure_url = f"{parsed.scheme}://{pinned_ip}:{port}{parsed.path}"
        if parsed.query:
            secure_url += f"?{parsed.query}"

        headers = {
            "Host": hostname,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        return secure_url, headers

    def format_base64_data_uri(self, mime_type: str, binary_data: bytes) -> str:
        encoded = base64.b64encode(binary_data).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"
