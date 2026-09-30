"""Image sources behind the models.Provider interface (decision D7).

default_providers() builds the list from settings:
    serper      Serper.dev Google Images, when SERPER_API_KEY is set (primary, sanctioned)
    off         Open Food Facts GTIN lookup (always listed; it only calls out for a valid GTIN)
    cse_legacy  Google CSE, only with an existing key and cx, until CSE_SUNSET_DATE
    bing_html   Bing HTML scraper (unsanctioned):
                - the primary text source when no sanctioned search API is configured;
                - otherwise a per-query fallback, only when ENABLE_BING_HTML_FALLBACK is on,
                  used by retrieve() when every sanctioned search provider failed.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import List, Optional

from .. import settings
from .base import BaseProvider, ProviderBlocked, ProviderEmpty, ProviderHTTPError
from .bing_html import BingHtmlProvider
from .cse_legacy import CseLegacyProvider
from .open_food_facts import OffProvider
from .serper import SerperImagesProvider

logger = logging.getLogger(__name__)

__all__ = [
    "BaseProvider", "BingHtmlProvider", "CseLegacyProvider", "OffProvider", "ProviderBlocked",
    "ProviderEmpty", "ProviderHTTPError", "SerperImagesProvider", "default_providers",
]


def default_providers(today: Optional[_dt.date] = None) -> List[BaseProvider]:
    """The configured providers, in the order retrieve() should try them."""
    providers: List[BaseProvider] = []
    serper_key = settings.serper_api_key()
    if serper_key:
        providers.append(SerperImagesProvider(api_key=serper_key))
    providers.append(OffProvider())
    cse = CseLegacyProvider.create(today=today)
    if cse is not None:
        providers.append(cse)
    has_sanctioned_search = bool(serper_key) or cse is not None
    if not has_sanctioned_search:
        providers.append(BingHtmlProvider(fallback=False))
    elif settings.enable_bing_html_fallback():
        providers.append(BingHtmlProvider(fallback=True))
    logger.info(
        "catalog_match providers: %s",
        ", ".join(f"{p.name}{' (fallback)' if p.fallback else ''}" for p in providers),
    )
    return providers
