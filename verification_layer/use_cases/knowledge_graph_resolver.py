# verification_layer/use_cases/knowledge_graph_resolver.py
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from verification_layer.use_cases.string_distance_calculator import StringDistanceCalculator


@dataclass
class ResolvedEntity:
    original_text: str
    canonical_name: str
    synonyms: List[str]
    confidence: float
    is_merged: bool


@dataclass
class CatalogKnowledgeGraphNode:
    gtin: str
    brand: ResolvedEntity
    product_name_ar: str
    product_name_en: str
    ingredients: List[ResolvedEntity] = field(default_factory=list)
    gs1_link: Optional[str] = None


class KnowledgeGraphEntityResolver:
    """
    حل الهويات الكيانية للرسم البياني للمعرفة (Entity Resolution Engine)
    - حل تعارضات ومترادفات الموردين ثنائية اللغة.
    - المطابقة الحتمية (GTIN) والمطابقة الاحتمالية الدلالية (Jaro-Winkler & Levenshtein >= 0.85).
    - توليد تمثيلات RDF/Turtle للأنطولوجيا الدلالية.
    """

    MATCH_THRESHOLD = 0.85

    def __init__(self):
        # Local in-memory entity lookup store
        self._brand_db: Dict[str, Dict[str, Any]] = {
            "nido": {"canonical": "Nido", "synonyms": ["نييدو", "نيدو", "nido", "nestle nido"]},
            "nestle": {"canonical": "Nestle", "synonyms": ["نستله", "نستلة", "nestle"]},
            "pepsi": {"canonical": "Pepsi", "synonyms": ["ببسي", "بيبتسي", "بيبسي", "pepsi"]},
        }

        self._ingredient_db: Dict[str, Dict[str, Any]] = {
            "aspartame": {"canonical_en": "aspartame", "canonical_ar": "أسبارتام", "synonyms": ["الأسبرتام", "أسبارتام", "aspartame", "e951"]},
            "sugar": {"canonical_en": "sugar", "canonical_ar": "سكر", "synonyms": ["السكر", "سكر", "sugar", "sucrose"]},
            "milk": {"canonical_en": "milk powder", "canonical_ar": "حليب مجفف", "synonyms": ["حليب", "لبن", "milk", "whole milk"]},
        }

    def resolve_brand(self, raw_brand_text: str) -> ResolvedEntity:
        if not raw_brand_text:
            return ResolvedEntity(original_text="", canonical_name="GENERIC", synonyms=[], confidence=0.0, is_merged=False)

        clean_text = raw_brand_text.strip().lower()

        # Step 1: Direct match against synonyms
        for key, info in self._brand_db.items():
            if clean_text == info["canonical"].lower() or clean_text in [s.lower() for s in info["synonyms"]]:
                return ResolvedEntity(
                    original_text=raw_brand_text,
                    canonical_name=info["canonical"],
                    synonyms=info["synonyms"],
                    confidence=1.0,
                    is_merged=True,
                )

        # Step 2: Probabilistic similarity match via Jaro-Winkler
        best_match_name = raw_brand_text
        best_score = 0.0
        best_synonyms = [raw_brand_text]

        for key, info in self._brand_db.items():
            score = StringDistanceCalculator.jaro_winkler_similarity(clean_text, info["canonical"].lower())
            for syn in info["synonyms"]:
                syn_score = StringDistanceCalculator.jaro_winkler_similarity(clean_text, syn.lower())
                if syn_score > score:
                    score = syn_score

            if score > best_score:
                best_score = score
                best_match_name = info["canonical"]
                best_synonyms = info["synonyms"]

        if best_score >= self.MATCH_THRESHOLD:
            return ResolvedEntity(
                original_text=raw_brand_text,
                canonical_name=best_match_name,
                synonyms=best_synonyms,
                confidence=round(best_score, 4),
                is_merged=True,
            )

        # Register new brand if no match found
        new_canonical = raw_brand_text.title()
        self._brand_db[clean_text] = {"canonical": new_canonical, "synonyms": [raw_brand_text]}
        return ResolvedEntity(
            original_text=raw_brand_text,
            canonical_name=new_canonical,
            synonyms=[raw_brand_text],
            confidence=0.5,
            is_merged=False,
        )

    def resolve_ingredient(self, raw_ing_text: str) -> ResolvedEntity:
        if not raw_ing_text:
            return ResolvedEntity(original_text="", canonical_name="", synonyms=[], confidence=0.0, is_merged=False)

        clean_text = raw_ing_text.strip().lower()

        for key, info in self._ingredient_db.items():
            if clean_text == info["canonical_en"] or clean_text == info["canonical_ar"] or clean_text in [s.lower() for s in info["synonyms"]]:
                return ResolvedEntity(
                    original_text=raw_ing_text,
                    canonical_name=info["canonical_en"],
                    synonyms=info["synonyms"],
                    confidence=1.0,
                    is_merged=True,
                )

        best_canonical = raw_ing_text
        best_score = 0.0
        best_synonyms = [raw_ing_text]

        for key, info in self._ingredient_db.items():
            s1 = StringDistanceCalculator.jaro_winkler_similarity(clean_text, info["canonical_en"])
            s2 = StringDistanceCalculator.jaro_winkler_similarity(clean_text, info["canonical_ar"])
            score = max(s1, s2)
            if score > best_score:
                best_score = score
                best_canonical = info["canonical_en"]
                best_synonyms = info["synonyms"]

        if best_score >= self.MATCH_THRESHOLD:
            return ResolvedEntity(
                original_text=raw_ing_text,
                canonical_name=best_canonical,
                synonyms=best_synonyms,
                confidence=round(best_score, 4),
                is_merged=True,
            )

        return ResolvedEntity(
            original_text=raw_ing_text,
            canonical_name=raw_ing_text.lower(),
            synonyms=[raw_ing_text],
            confidence=0.5,
            is_merged=False,
        )

    def generate_rdf_turtle(self, node: CatalogKnowledgeGraphNode) -> str:
        """توليد أنطولوجيا المعرفة الدلالية بتنسيق (RDF/Turtle)."""
        gtin = node.gtin or "00000000000000"
        brand_clean = node.brand.canonical_name.replace(" ", "_")
        gs1_link = node.gs1_link or f"https://id.gs1.org/01/{gtin}"

        turtle_str = f"""@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix schema: <https://schema.org/> .
@prefix ex: <http://example.org/catalog/> .

ex:product_{gtin} a schema:Product ;
    ex:hasGtin "{gtin}" ;
    schema:name "{node.product_name_en}"@en, "{node.product_name_ar}"@ar ;
    ex:manufacturedBy ex:brand_{brand_clean} ;
    ex:hasGs1Link <{gs1_link}> .

ex:brand_{brand_clean} a schema:Brand ;
    rdfs:label "{node.brand.canonical_name}" .
"""
        return turtle_str
