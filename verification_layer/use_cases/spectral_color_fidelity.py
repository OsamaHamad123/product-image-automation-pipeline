# verification_layer/use_cases/spectral_color_fidelity.py
"""
Spectral Delta-E Color Fidelity Simulator & Brand Gatekeeper.
Implements CIEDE2000 (Delta E 2000) distance formula according to ISO/CIE 11664-6:2022.
Pure NumPy and Python math implementation.
"""

import numpy as np
from typing import Tuple, Dict, Any, List
from verification_layer.domain.nextgen_models import LabColor, ProductBrandSpecs, DeltaECompliance


def rgb_to_lab(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    """Converts sRGB (0-255) tuple to CIELAB (D65 illuminant)."""
    r, g, b = [x / 255.0 for x in rgb]

    # Gamma correction
    r = r / 12.92 if r <= 0.04045 else ((r + 0.055) / 1.055) ** 2.4
    g = g / 12.92 if g <= 0.04045 else ((g + 0.055) / 1.055) ** 2.4
    b = b / 12.92 if b <= 0.04045 else ((b + 0.055) / 1.055) ** 2.4

    # Observer = 2°, Illuminant = D65
    x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
    y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750) / 1.00000
    z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883

    def f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 0.008856 else (7.787 * t) + (16.0 / 116.0)

    fx, fy, fz = f(x), f(y), f(z)
    l_star = round(max(0.0, (116.0 * fy) - 16.0), 3)
    a_star = round(500.0 * (fx - fy), 3)
    b_star = round(200.0 * (fy - fz), 3)
    return (l_star, a_star, b_star)


def calculate_ciede2000(lab1: Tuple[float, float, float], lab2: Tuple[float, float, float]) -> float:
    """Calculates CIEDE2000 (Delta E 2000) distance between two CIELAB colors."""
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2

    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    mean_C = (C1 + C2) / 2.0

    G = 0.5 * (1.0 - np.sqrt((mean_C ** 7) / ((mean_C ** 7) + (25.0 ** 7))))

    a1_prime = (1.0 + G) * a1
    a2_prime = (1.0 + G) * a2

    C1_prime = np.hypot(a1_prime, b1)
    C2_prime = np.hypot(a2_prime, b2)

    h1_prime = np.degrees(np.arctan2(b1, a1_prime)) % 360.0
    h2_prime = np.degrees(np.arctan2(b2, a2_prime)) % 360.0

    delta_L_prime = L2 - L1
    delta_C_prime = C2_prime - C1_prime

    if C1_prime * C2_prime == 0.0:
        delta_h_prime = 0.0
    else:
        diff_h = h2_prime - h1_prime
        if abs(diff_h) <= 180.0:
            delta_h_prime = diff_h
        elif diff_h > 180.0:
            delta_h_prime = diff_h - 360.0
        else:
            delta_h_prime = diff_h + 360.0

    delta_H_prime = 2.0 * np.sqrt(C1_prime * C2_prime) * np.sin(np.radians(delta_h_prime / 2.0))

    mean_L_prime = (L1 + L2) / 2.0
    mean_C_prime = (C1_prime + C2_prime) / 2.0

    if C1_prime * C2_prime == 0.0:
        mean_h_prime = h1_prime + h2_prime
    else:
        sum_h = h1_prime + h2_prime
        if abs(h1_prime - h2_prime) <= 180.0:
            mean_h_prime = sum_h / 2.0
        else:
            if sum_h < 360.0:
                mean_h_prime = (sum_h + 360.0) / 2.0
            else:
                mean_h_prime = (sum_h - 360.0) / 2.0

    T = (
        1.0 - 0.17 * np.cos(np.radians(mean_h_prime - 30.0)) +
        0.24 * np.cos(np.radians(2.0 * mean_h_prime)) +
        0.32 * np.cos(np.radians(3.0 * mean_h_prime + 6.0)) -
        0.20 * np.cos(np.radians(4.0 * mean_h_prime - 63.0))
    )

    delta_theta = 30.0 * np.exp(-(((mean_h_prime - 275.0) / 25.0) ** 2))

    S_L = 1.0 + (0.015 * ((mean_L_prime - 50.0) ** 2)) / np.sqrt(20.0 + ((mean_L_prime - 50.0) ** 2))
    S_C = 1.0 + 0.045 * mean_C_prime
    S_H = 1.0 + 0.015 * mean_C_prime * T

    R_C = 2.0 * np.sqrt((mean_C_prime ** 7) / ((mean_C_prime ** 7) + (25.0 ** 7)))
    R_T = -np.sin(np.radians(2.0 * delta_theta)) * R_C

    de2000 = np.sqrt(
        ((delta_L_prime / S_L) ** 2) +
        ((delta_C_prime / S_C) ** 2) +
        ((delta_H_prime / S_H) ** 2) +
        R_T * (delta_C_prime / S_C) * (delta_H_prime / S_H)
    )
    return round(float(de2000), 4)


class SpectralColorFidelityUseCase:
    def verify_brand_color_compliance(
        self,
        extracted_lab: Tuple[float, float, float],
        specs: ProductBrandSpecs
    ) -> DeltaECompliance:
        if not specs.target_colors:
            # Fallback default standard target
            target = LabColor(l_star=50.0, a_star=20.0, b_star=-10.0, label="Standard Default")
            target_labs = [target]
        else:
            target_labs = specs.target_colors

        min_de = 999.0
        best_target_label = ""

        for target in target_labs:
            de = calculate_ciede2000((target.l_star, target.a_star, target.b_star), extracted_lab)
            if de < min_de:
                min_de = de
                best_target_label = target.label

        if min_de <= 1.0:
            perception = "IMPERCEPTIBLE_PERFECT_MATCH"
            decision = "IMMEDIATE_BRAND_APPROVAL"
            approved = True
        elif min_de <= 2.0:
            perception = "SLIGHT_VARIATION_EXPERT_ONLY"
            decision = "SAFE_BRAND_APPROVAL"
            approved = True
        elif min_de <= specs.allowed_tolerance:
            perception = "PERCEPTIBLE_DISTINCTION"
            decision = "CONDITIONAL_APPROVAL_WARNING"
            approved = True
        else:
            perception = "SEVERE_COLOR_DEVIATION"
            decision = "REJECTED_BRAND_COLOR_VIOLATION"
            approved = False

        return DeltaECompliance(
            gtin=specs.gtin,
            brand_name=specs.brand_name,
            delta_e2000=min_de,
            perception_level=perception,
            brand_decision=f"{decision} ({best_target_label})",
            approved=approved
        )
