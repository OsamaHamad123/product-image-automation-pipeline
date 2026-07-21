# verification_layer/use_cases/proxy_trust_scoring.py
import random
import math
from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class ProxyTrustScore:
    proxy_url: str
    trust_score: float  # 0.0 (untrusted/quarantined) to 1.0 (highly trusted)
    success_count: int
    soft_ban_count: int
    is_quarantined: bool


class BayesianProxyTrustScorer:
    """
    محرك تقييم وحساب الثقة البايزية للبروكسيات والمهل الزمنية العشوائية (Bayesian Proxy Scorer & Pacing Engine)
    - T_delay = mu + sigma * randn()
    - رصد الحظر الناعم وعزل البروكسيات المتراجعة تلقائياً.
    """

    DEFAULT_MU = 1.5      # Mean delay in seconds
    DEFAULT_SIGMA = 0.5   # Standard deviation

    def __init__(self, mu: float = DEFAULT_MU, sigma: float = DEFAULT_SIGMA):
        self.mu = mu
        self.sigma = sigma
        self._proxy_stats: Dict[str, Dict[str, int]] = {}

    def calculate_gaussian_delay(self) -> float:
        """
        حساب المهلة الزمنية الفاصلة والمحتسبة ديناميكياً لكسر الوتيرة الميكانيكية للزواحف:
        T_delay = mu + sigma * randn()
        """
        val = random.gauss(self.mu, self.sigma)
        return max(0.2, round(val, 3))  # Clamped to at least 200ms

    def record_response(self, proxy_url: str, is_success: bool, is_soft_ban: bool = False):
        if proxy_url not in self._proxy_stats:
            self._proxy_stats[proxy_url] = {"success": 0, "fail": 0, "soft_ban": 0}

        stats = self._proxy_stats[proxy_url]
        if is_success and not is_soft_ban:
            stats["success"] += 1
        elif is_soft_ban:
            stats["soft_ban"] += 1
            stats["fail"] += 1
        else:
            stats["fail"] += 1

    def compute_bayesian_trust_score(self, proxy_url: str) -> ProxyTrustScore:
        stats = self._proxy_stats.get(proxy_url, {"success": 1, "fail": 0, "soft_ban": 0})
        s = stats["success"]
        f = stats["fail"]
        sb = stats["soft_ban"]

        # Bayesian Beta Prior: alpha=2, beta=1
        alpha_prior = 2.0
        beta_prior = 1.0

        bayes_score = (s + alpha_prior) / float(s + f + alpha_prior + beta_prior)

        # Apply penalty for soft bans
        soft_ban_penalty = 0.25 * sb
        final_score = max(0.0, min(1.0, bayes_score - soft_ban_penalty))

        is_quarantined = final_score < 0.35 or sb >= 3

        return ProxyTrustScore(
            proxy_url=proxy_url,
            trust_score=round(final_score, 4),
            success_count=s,
            soft_ban_count=sb,
            is_quarantined=is_quarantined,
        )
