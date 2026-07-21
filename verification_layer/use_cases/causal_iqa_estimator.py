# verification_layer/use_cases/causal_iqa_estimator.py
import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple


@dataclass
class IQAEvaluationResult:
    brisque_score: float  # Lower is better (0 to 100)
    nima_aesthetic_score: float  # 1.0 to 10.0 (Higher is better)
    image_quality_grade: str


@dataclass
class DoubleMLCausalEstimateResult:
    average_treatment_effect_ate: float  # theta
    statistically_significant: bool
    confidence_interval_95: Tuple[float, float]
    sample_size_n: int


class CausalIQAEstimator:
    """
    إطار الاستدلال السببي بالتعلم الآلي المزدوج ومقاييس BRISQUE/NIMA الجمالية (Double ML Causal Estimator & IQA Engine)
    - NIMA Score = sum_{i=1}^{10} i * P(s_i)
    - Double ML Partial Linear Model: Y = theta * T + g(X) + eps, T = h(X) + eta
    - Neyman Orthogonality: Y_tilde = Y - Y_hat(X), T_tilde = T - T_hat(X), Y_tilde = theta * T_tilde + u
    """

    @classmethod
    def calculate_nima_score(cls, probabilities_10_bins: List[float]) -> float:
        """
        NIMA Score = sum_{i=1}^{10} i * P(s_i)
        """
        if len(probabilities_10_bins) != 10:
            # Fallback uniform distribution
            probabilities_10_bins = [0.10] * 10

        probs = np.array(probabilities_10_bins) / np.sum(probabilities_10_bins)
        scores = np.arange(1, 11)
        nima_val = float(np.sum(scores * probs))
        return float(round(nima_val, 2))

    @classmethod
    def calculate_brisque_score(cls, image_np: np.ndarray) -> float:
        """
        حساب مقياس التقييم الإدراكي للجودة في النطاق المكاني (BRISQUE)
        انخفاض القيمة يشير لجودة ناصعة وخلو الصورة من الضوضاء.
        """
        if image_np.ndim == 3:
            gray = np.mean(image_np, axis=2)
        else:
            gray = image_np.astype(float)

        # Standard deviation of spatial coefficients as lightweight proxy for BRISQUE
        std_val = float(np.std(gray))
        brisque_proxy = max(0.0, min(100.0, 100.0 - std_val))
        return float(round(brisque_proxy, 2))

    @classmethod
    def estimate_double_ml_causal_effect(
        cls,
        converters_y: List[int],         # Binary conversion outcome Y (1 or 0)
        catalog_quality_t: List[float], # Treatment quality score T
        confounders_x: List[List[float]] # High-dimensional confounders X (Prices, Ad Spend, User Ratings)
    ) -> DoubleMLCausalEstimateResult:
        """
        Double Machine Learning (DML) Partial Linear Model for Causal Impact Analysis:
        Y_tilde = Y - Y_hat(X)
        T_tilde = T - T_hat(X)
        Y_tilde = theta * T_tilde + u
        """
        Y = np.array(converters_y, dtype=float)
        T = np.array(catalog_quality_t, dtype=float)
        X = np.array(confounders_x, dtype=float)

        N = len(Y)
        if N < 10:
            return DoubleMLCausalEstimateResult(
                average_treatment_effect_ate=0.0,
                statistically_significant=False,
                confidence_interval_95=(0.0, 0.0),
                sample_size_n=N,
            )

        # 1. Estimate Y_hat(X) using linear regression over confounders X
        beta_y = np.linalg.pinv(X).dot(Y) if X.ndim > 1 else np.zeros(X.shape[1])
        Y_hat = X.dot(beta_y) if X.ndim > 1 else np.mean(Y)
        Y_tilde = Y - Y_hat

        # 2. Estimate T_hat(X) using linear regression over confounders X
        beta_t = np.linalg.pinv(X).dot(T) if X.ndim > 1 else np.zeros(X.shape[1])
        T_hat = X.dot(beta_t) if X.ndim > 1 else np.mean(T)
        T_tilde = T - T_hat

        # 3. Orthogonal Regression: theta = (T_tilde^T * T_tilde)^(-1) * (T_tilde^T * Y_tilde)
        denom = np.sum(T_tilde**2)
        theta = (np.sum(T_tilde * Y_tilde) / denom) if denom > 0 else 0.0

        # Calculate 95% confidence interval
        residuals = Y_tilde - (theta * T_tilde)
        std_err = np.sqrt(np.sum(residuals**2) / max(1, N - 2)) / np.sqrt(max(1e-5, denom))
        ci_lower = theta - (1.96 * std_err)
        ci_upper = theta + (1.96 * std_err)

        is_sig = bool(ci_lower > 0 or ci_upper < 0)

        return DoubleMLCausalEstimateResult(
            average_treatment_effect_ate=float(round(theta, 4)),
            statistically_significant=is_sig,
            confidence_interval_95=(float(round(ci_lower, 4)), float(round(ci_upper, 4))),
            sample_size_n=N,
        )
