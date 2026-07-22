# verification_layer/use_cases/search_intent_resolver.py
"""
Bundle & Multi-Pack Search Intent Resolver Use Case.
Resolves query text intent (single vs multi-pack/bundle) and scores visual packaging aspect match.
"""

import re
from verification_layer.domain.nextgen_models import IntentType, SearchQuery, ProductVisualProfile


class SearchIntentResolverUseCase:
    """
    UseCase analyzing textual query intent and matching it visually/geometrically with product profiles.
    """
    def __init__(self):
        self.bundle_keywords = re.compile(
            r'\b(كرتونة|شدة|صندوق|مجموعة|باكيت|كرتون|علبة مجمعة|حزمة|pack|bundle|promo|multipack|box|tray|case)\b',
            re.IGNORECASE
        )
        self.numeric_pack_pattern = re.compile(
            r'(\d+)\s*(?:x|×|\*)\s*(\d+)?|(\d+)\s*(?:حبة|قطعة|وحدة|pcs|units|pack)',
            re.IGNORECASE
        )

    def resolve_query_intent(self, query_text: str) -> SearchQuery:
        cleaned = query_text.lower().strip()
        intent = IntentType.SINGLE_UNIT
        parsed_units = 1

        if self.bundle_keywords.search(cleaned):
            intent = IntentType.MULTI_PACK

        match = self.numeric_pack_pattern.search(cleaned)
        if match:
            intent = IntentType.MULTI_PACK
            groups = match.groups()
            if groups[0]:
                parsed_units = int(groups[0])
            elif groups[2]:
                parsed_units = int(groups[2])

        return SearchQuery(
            raw_text=query_text,
            cleaned_text=cleaned,
            parsed_units=parsed_units,
            intent=intent
        )

    def score_visual_match(self, query: SearchQuery, product: ProductVisualProfile) -> float:
        """
        Visual match scoring formula preventing single items from displaying for bundle searches and vice versa.
        """
        # Mismatch penalties
        if query.intent == IntentType.MULTI_PACK and not product.is_bundle_packaging:
            return 0.02

        if query.intent == IntentType.SINGLE_UNIT and product.is_bundle_packaging:
            return 0.05

        base_score = 1.0
        if query.intent == IntentType.MULTI_PACK:
            unit_difference = abs(query.parsed_units - product.unit_count)
            if unit_difference == 0:
                base_score += 0.5  # Full unit count match bonus
            else:
                base_score += max(0.0, 0.3 - (unit_difference * 0.05))

        if product.is_bundle_packaging and (0.8 <= product.aspect_ratio <= 1.3):
            base_score += 0.2

        return round(min(2.0, base_score), 2)
