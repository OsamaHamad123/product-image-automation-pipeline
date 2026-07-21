# verification_layer/domain/bilingual_schema.py
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class ServingSize:
    value: float
    unit: str = "g"  # g, ml, oz, piece


@dataclass
class NutrientValue:
    value: float
    unit: str = "g"


@dataclass
class CarbohydrateFacts:
    total: float
    sugar: float = 0.0
    fiber: float = 0.0


@dataclass
class FatFacts:
    total: float
    saturated: float = 0.0


@dataclass
class Macronutrients:
    proteins: float
    carbohydrates: CarbohydrateFacts
    fats: FatFacts
    alcohol: float = 0.0


@dataclass
class NutritionFacts:
    serving_size: ServingSize
    calories: float  # in kcal
    macronutrients: Macronutrients


@dataclass
class BrandEntity:
    extracted_name: str
    canonical_name: str
    synonyms: List[str] = field(default_factory=list)


@dataclass
class BilingualText:
    ar: str
    en: str


@dataclass
class BilingualIngredients:
    ar: List[str] = field(default_factory=list)
    en: List[str] = field(default_factory=list)


@dataclass
class BilingualProductCatalogModel:
    gtin: str  # 14-digit GTIN
    brand: BrandEntity
    names: BilingualText
    ingredients: BilingualIngredients
    nutrition_facts: Optional[NutritionFacts] = None
    warnings: Optional[BilingualIngredients] = None
    gs1_digital_link: Optional[str] = None
