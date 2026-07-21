# verification_layer/use_cases/atwater_nutrition_verifier.py
from dataclasses import dataclass
from typing import Dict, Any, Tuple
from verification_layer.domain.bilingual_schema import NutritionFacts, Macronutrients


@dataclass
class AtwaterVerificationResult:
    is_valid: bool
    extracted_calories: float
    estimated_calories: float
    deviation_percentage: float
    mass_balance_passed: bool
    nutri_score_rating: str  # 'A', 'B', 'C', 'D', 'E'
    reasons: list


class AtwaterNutritionVerifier:
    """
    طبقة التدقيق الرياضي الحتمي للبيانات الغذائية عبر صيغة أتووتر المعدلة (Atwater Energy Factor System)
    E_est = (4 * P) + (4 * (C - DF)) + (9 * F) + (2 * DF) + (7 * A)
    حيث P البروتين، C الكربوهيدرات، F الدهون، DF الألياف، A الكحول.
    """

    MAX_ALLOWED_DEVIATION = 5.0  # %5 أقصى انحراف مسموح به بين السعرات المصرح بها والمحسوبة

    @classmethod
    def calculate_atwater_energy(cls, macros: Macronutrients) -> float:
        p = max(0.0, macros.proteins)
        c = max(0.0, macros.carbohydrates.total)
        df = max(0.0, macros.carbohydrates.fiber)
        f = max(0.0, macros.fats.total)
        a = max(0.0, macros.alcohol)

        # Available carbohydrates = (Total Carbohydrates - Dietary Fiber)
        avail_c = max(0.0, c - df)

        e_est = (4.0 * p) + (4.0 * avail_c) + (9.0 * f) + (2.0 * df) + (7.0 * a)
        return float(round(e_est, 2))

    @classmethod
    def verify_nutrition_facts(cls, nutrition: NutritionFacts) -> AtwaterVerificationResult:
        reasons = []
        is_valid = True

        extracted_cal = nutrition.calories
        macros = nutrition.macronutrients
        est_cal = cls.calculate_atwater_energy(macros)

        # 1. Atwater Energy Deviation Test
        if extracted_cal > 0:
            dev_pct = (abs(extracted_cal - est_cal) / float(extracted_cal)) * 100.0
        else:
            dev_pct = 0.0 if est_cal == 0.0 else 100.0

        if dev_pct > cls.MAX_ALLOWED_DEVIATION:
            is_valid = False
            reasons.append(
                f"Atwater energy deviation ({dev_pct:.2f}%) exceeds maximum threshold (5.0%). "
                f"Extracted: {extracted_cal} kcal vs Estimated: {est_cal} kcal."
            )

        # 2. Proximate Mass Balance Audit
        p = macros.proteins
        c = macros.carbohydrates.total
        f = macros.fats.total
        a = macros.alcohol
        total_macro_mass = p + c + f + a

        serving_g = nutrition.serving_size.value
        # If unit is grams, verify total macro mass does not exceed serving size by more than 2%
        mass_balance_passed = True
        if nutrition.serving_size.unit.lower() == "g" and serving_g > 0:
            if total_macro_mass > (serving_g * 1.02):
                mass_balance_passed = False
                is_valid = False
                reasons.append(
                    f"Mass balance audit failed: sum of macronutrients ({total_macro_mass:.1f}g) "
                    f"exceeds stated serving size ({serving_g:.1f}g)."
                )

        # 3. Compute Nutri-Score Grade
        nutri_score = cls.compute_nutri_score(extracted_cal, macros.carbohydrates.sugar, macros.fats.saturated, p, macros.carbohydrates.fiber)

        return AtwaterVerificationResult(
            is_valid=is_valid,
            extracted_calories=extracted_cal,
            estimated_calories=est_cal,
            deviation_percentage=round(dev_pct, 2),
            mass_balance_passed=mass_balance_passed,
            nutri_score_rating=nutri_score,
            reasons=reasons,
        )

    @staticmethod
    def compute_nutri_score(calories: float, sugar: float, saturated_fat: float, protein: float, fiber: float) -> str:
        """حساب نقاط الجودة الغذائية Nutri-Score (A, B, C, D, E)."""
        negative_points = 0
        if calories > 335: negative_points += 4
        elif calories > 167: negative_points += 2

        if sugar > 13.5: negative_points += 4
        elif sugar > 4.5: negative_points += 2

        if saturated_fat > 4.0: negative_points += 4
        elif saturated_fat > 1.0: negative_points += 2

        positive_points = 0
        if protein > 4.8: positive_points += 3
        elif protein > 1.6: positive_points += 1

        if fiber > 3.5: positive_points += 3
        elif fiber > 0.9: positive_points += 1

        final_score = negative_points - positive_points

        if final_score <= -1: return "A"
        elif final_score <= 2: return "B"
        elif final_score <= 10: return "C"
        elif final_score <= 18: return "D"
        else: return "E"
