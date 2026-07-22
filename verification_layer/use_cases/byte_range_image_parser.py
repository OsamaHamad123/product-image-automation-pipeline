# verification_layer/use_cases/byte_range_image_parser.py
"""
Early Byte-Range Binary Image Parser & Aspect Ratio Filter.
Parses PNG, GIF, and JPEG dimensions (with EXIF 0x0112 orientation handling) directly from early byte chunks using struct.
"""

import struct
from typing import Tuple, Optional


class ByteRangeImageParser:
    """
    High-performance binary header parser extracting dimensions from partial byte streams.
    """
    @staticmethod
    def parse_png_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
        if len(data) >= 24 and data.startswith(b'\x89PNG\r\n\x1a\n'):
            if data[12:16] == b'IHDR':
                width, height = struct.unpack('>II', data[16:24])
                return width, height
        return None

    @staticmethod
    def parse_gif_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
        if len(data) >= 10 and (data.startswith(b'GIF89a') or data.startswith(b'GIF87a')):
            width, height = struct.unpack('<HH', data[6:10])
            return width, height
        return None

    @staticmethod
    def parse_jpeg_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
        if len(data) < 2 or not data.startswith(b'\xff\xd8'):
            return None

        offset = 2
        data_len = len(data)

        while offset < data_len:
            if data[offset] != 0xff:
                next_ff = data.find(b'\xff', offset)
                if next_ff == -1:
                    break
                offset = next_ff

            while offset < data_len and data[offset] == 0xff:
                offset += 1

            if offset >= data_len:
                break

            marker = data[offset]
            offset += 1

            if marker == 0xda:
                break

            if marker in (0xd8, 0xd9) or (0xd0 <= marker <= 0xd7):
                continue

            if offset + 2 > data_len:
                break

            segment_length = struct.unpack('>H', data[offset:offset + 2])[0]

            if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
                if offset + 2 + 5 <= data_len:
                    height, width = struct.unpack('>HH', data[offset + 3:offset + 7])
                    orientation = ByteRangeImageParser._extract_exif_orientation(data)
                    if orientation in (5, 6, 7, 8):
                        return height, width
                    return width, height
                break

            offset += segment_length
        return None

    @staticmethod
    def _extract_exif_orientation(data: bytes) -> int:
        try:
            exif_offset = data.find(b'Exif\x00\x00')
            if exif_offset != -1:
                tiff_start = exif_offset + 6
                byte_order = data[tiff_start:tiff_start + 2]
                fmt_char = '<' if byte_order == b'II' else '>'
                magic_tiff = struct.unpack(f"{fmt_char}H", data[tiff_start + 2:tiff_start + 4])[0]
                if magic_tiff == 42:
                    ifd_offset = struct.unpack(f"{fmt_char}I", data[tiff_start + 4:tiff_start + 8])[0]
                    dir_start = tiff_start + ifd_offset
                    num_entries = struct.unpack(f"{fmt_char}H", data[dir_start:dir_start + 2])[0]
                    for i in range(num_entries):
                        entry_pos = dir_start + 2 + (i * 12)
                        tag = struct.unpack(f"{fmt_char}H", data[entry_pos:entry_pos + 2])[0]
                        if tag == 0x0112:
                            val = struct.unpack(f"{fmt_char}H", data[entry_pos + 8:entry_pos + 10])[0]
                            return val
        except Exception:
            pass
        return 1

    @classmethod
    def get_dimensions(cls, binary_chunk: bytes) -> Optional[Tuple[int, int]]:
        if binary_chunk.startswith(b'\x89PNG\r\n\x1a\n'):
            return cls.parse_png_dimensions(binary_chunk)
        elif binary_chunk.startswith(b'\xff\xd8'):
            return cls.parse_jpeg_dimensions(binary_chunk)
        elif binary_chunk.startswith(b'GIF89a') or binary_chunk.startswith(b'GIF87a'):
            return cls.parse_gif_dimensions(binary_chunk)
        return None


class AspectRatioFilterUseCase:
    """
    Filters small icons and inappropriate aspect ratios (banners/panoramas).
    """
    def __init__(
        self,
        min_width: int = 300,
        min_height: int = 300,
        min_aspect_ratio: float = 0.5,
        max_aspect_ratio: float = 2.0
    ):
        self.min_width = min_width
        self.min_height = min_height
        self.min_aspect = min_aspect_ratio
        self.max_aspect = max_aspect_ratio

    def is_valid_commercial_image(self, width: int, height: int) -> Tuple[bool, str]:
        if width < self.min_width or height < self.min_height:
            return False, f"Image dimensions too small ({width}x{height} pixels)"

        aspect_ratio = width / float(height) if height > 0 else 0.0
        if aspect_ratio < self.min_aspect or aspect_ratio > self.max_aspect:
            return False, f"Inappropriate aspect ratio ({aspect_ratio:.2f})"

        return True, "Valid"
