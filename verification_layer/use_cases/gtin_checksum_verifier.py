# verification_layer/use_cases/gtin_checksum_verifier.py
"""
Hybrid GTIN-13 / EAN-13 Checksum Verification Engine.
Verifies check digit formula: Check Digit = (10 - (SUM(d_i * w_i) mod 10)) mod 10.
"""

class HybridBarcodeEngine:
    @staticmethod
    def is_valid_gtin13(barcode: str) -> bool:
        """
        Validates 13-digit GTIN / EAN-13 barcode using modulo-10 weighted sum.
        """
        if not barcode or len(barcode) != 13 or not barcode.isdigit():
            return False
        
        digits = [int(char) for char in barcode]
        # Position 1 to 12 (0-indexed: index 0,2,4... weight 1; index 1,3,5... weight 3)
        total = sum(digits[i] * (3 if i % 2 == 1 else 1) for i in range(12))
        check_digit = (10 - (total % 10)) % 10
        return check_digit == digits[12]
