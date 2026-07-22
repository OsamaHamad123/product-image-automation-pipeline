# verification_layer/infrastructure/adaptive_proxy_pool.py
"""
Adaptive Residential Proxy Pool with Per-Node Circuit Breaker.
Manages proxy health states (CLOSED, OPEN, HALF_OPEN), latency sampling, and failure recovery.
"""

import time
import urllib.request
import urllib.error
from typing import List, Optional
from verification_layer.domain.nextgen_models import ProxyNode, ProxyState


class AdaptiveProxyPool:
    """
    Manages and rotates residential proxies with an integrated per-proxy circuit breaker.
    """
    def __init__(
        self,
        proxy_urls: List[str],
        failure_threshold: int = 3,
        cooldown_period: float = 15.0,
        latency_sample_size: int = 5
    ):
        self.nodes: List[ProxyNode] = [ProxyNode(url=url) for url in proxy_urls]
        self.failure_threshold = failure_threshold
        self.cooldown_period = cooldown_period
        self.latency_sample_size = latency_sample_size

    def get_next_proxy(self) -> Optional[ProxyNode]:
        now = time.time()
        healthy_candidates: List[ProxyNode] = []

        for node in self.nodes:
            if node.state == ProxyState.CLOSED:
                healthy_candidates.append(node)
            elif node.state == ProxyState.OPEN:
                if now - node.last_failure_time >= self.cooldown_period:
                    node.state = ProxyState.HALF_OPEN
                    healthy_candidates.append(node)
            elif node.state == ProxyState.HALF_OPEN:
                healthy_candidates.append(node)

        if not healthy_candidates:
            return None

        # Sort by average latency (lowest latency first)
        healthy_candidates.sort(key=lambda x: x.average_latency)
        return healthy_candidates[0]

    def record_success(self, node: ProxyNode, latency: float):
        node.failure_count = 0
        node.success_count += 1
        node.latency_history.append(latency)
        if len(node.latency_history) > self.latency_sample_size:
            node.latency_history.pop(0)

        if node.state == ProxyState.HALF_OPEN:
            node.state = ProxyState.CLOSED
            node.success_count = 0

    def record_failure(self, node: ProxyNode, error_code: int):
        node.failure_count += 1
        node.last_failure_time = time.time()
        if node.state == ProxyState.HALF_OPEN or node.failure_count >= self.failure_threshold:
            node.state = ProxyState.OPEN
            node.latency_history.clear()

    def execute_http_request(self, target_url: str, timeout: float = 3.0) -> Optional[bytes]:
        for _ in range(len(self.nodes)):
            node = self.get_next_proxy()
            if not node:
                break

            start_time = time.time()
            try:
                proxy_handler = urllib.request.ProxyHandler({'http': node.url, 'https': node.url})
                opener = urllib.request.build_opener(proxy_handler)

                req = urllib.request.Request(
                    target_url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                )

                with opener.open(req, timeout=timeout) as response:
                    data = response.read()
                    latency = time.time() - start_time
                    self.record_success(node, latency)
                    return data

            except urllib.error.HTTPError as http_err:
                if http_err.code in (403, 429):
                    self.record_failure(node, http_err.code)
                else:
                    self.record_failure(node, 500)
            except Exception:
                self.record_failure(node, 500)

        return None
