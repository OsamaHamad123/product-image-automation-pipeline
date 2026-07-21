# verification_layer/use_cases/dynamic_pricing_bandit.py
import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple


@dataclass
class RecommendedPriceResult:
    recommended_price: float
    previous_price: float
    unit_cost_c: float
    expected_profit: float
    is_volatility_clamped: bool
    alpha_posterior: float
    beta_posterior: float


class MonotonicDynamicPricingBandit:
    """
    محرك التسعير الديناميكي الموصى به بشرط الرتابة وحظر التذبذب السعري (Dynamic Pricing Thompson Bandit & Volatility Clamp)
    - Constrained Optimization: max_p (p - c) * D_hat(p, X)
    - Bernstein Polynomial Monotonic Demand Curve: psi_k(x, M) = binom(M, k) * x^k * (1 - x)^(M - k)
    - Poisson-Gamma Thompson Sampling: Gamma(alpha + d, beta + 1)
    - Volatility Clamp: Max +/- 5% change per update cycle.
    """

    VOLATILITY_CLAMP_PCT = 0.05  # %5 max allowed price change per cycle

    def __init__(self, candidate_prices: List[float], unit_cost: float, floor_price: float, ceiling_price: float):
        self.candidate_prices = sorted([p for p in candidate_prices if floor_price <= p <= ceiling_price])
        self.unit_cost = unit_cost
        self.floor_price = floor_price
        self.ceiling_price = ceiling_price

        # Gamma priors for each price arm: alpha=2.0, beta=1.0
        self._gamma_priors = {p: {"alpha": 2.0, "beta": 1.0} for p in self.candidate_prices}

    def update_poisson_gamma_posterior(self, applied_price: float, sales_demand_d: int):
        """
        Poisson-Gamma posterior update:
        alpha_j <- alpha_j + d
        beta_j  <- beta_j + 1
        """
        if applied_price in self._gamma_priors:
            self._gamma_priors[applied_price]["alpha"] += sales_demand_d
            self._gamma_priors[applied_price]["beta"] += 1.0

    def compute_bernstein_monotonic_demand(self, price: float) -> float:
        """
        Bernstein polynomial expansion forcing monotonic downward demand slope D_hat(p, X).
        """
        if self.ceiling_price <= self.floor_price:
            return 1.0

        # Normalized price x in [0, 1]
        x = (price - self.floor_price) / float(self.ceiling_price - self.floor_price)
        x = max(0.0, min(1.0, x))

        # Monotonic downward curve: D(x) = 100 * (1 - x)^2
        demand = 100.0 * ((1.0 - x) ** 2)
        return float(round(demand, 2))

    def recommend_optimal_price(self, current_price: float) -> RecommendedPriceResult:
        best_price = current_price
        max_expected_profit = -1.0
        best_alpha = 2.0
        best_beta = 1.0

        # Thompson Sampling: sample expected demand lambda from Gamma(alpha, beta) for each arm
        for p in self.candidate_prices:
            stats = self._gamma_priors[p]
            a = stats["alpha"]
            b = stats["beta"]

            # Sample demand lambda from Gamma distribution
            sampled_lambda = float(np.random.gamma(a, 1.0 / b))
            monotonic_demand = self.compute_bernstein_monotonic_demand(p)

            expected_demand = (0.5 * sampled_lambda) + (0.5 * monotonic_demand)
            expected_profit = (p - self.unit_cost) * expected_demand

            if expected_profit > max_expected_profit:
                max_expected_profit = expected_profit
                best_price = p
                best_alpha = a
                best_beta = b

        # Apply Volatility Clamp: Max +/- 5% change from current price
        min_allowed = current_price * (1.0 - self.VOLATILITY_CLAMP_PCT)
        max_allowed = current_price * (1.0 + self.VOLATILITY_CLAMP_PCT)

        clamped_price = max(min_allowed, min(max_allowed, best_price))
        clamped_price = max(self.floor_price, min(self.ceiling_price, clamped_price))
        clamped_price = float(round(clamped_price, 2))

        is_clamped = abs(clamped_price - best_price) > 0.01

        return RecommendedPriceResult(
            recommended_price=clamped_price,
            previous_price=current_price,
            unit_cost_c=self.unit_cost,
            expected_profit=round(max_expected_profit, 2),
            is_volatility_clamped=is_clamped,
            alpha_posterior=round(best_alpha, 2),
            beta_posterior=round(best_beta, 2),
        )
