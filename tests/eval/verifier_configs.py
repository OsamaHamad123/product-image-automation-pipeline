"""Label-reader configurations for the eval: replayed from recorded answers, or live behind an explicit flag.

A Config is what the settings choose (catalog_match.verifiers.cascade.default_verifier): the PRIMARY reader of every
batch, the STRONG reader of the second looks (or none), VERIFIER_STRONG_MAX_CALLS and VERIFIER_REJUDGE_MAX_CALLS.

    configs()                 'current' (the settings as they are), 'strong_primary' (the strong model reads every
                              batch, no second look), 'strong_max2' (the current setup with VERIFIER_STRONG_MAX_CALLS=2)
    replay_verifier(cfg, ...) a real CascadeVerifier whose readers answer from recorded readings (RecordedReader): the
                              cascade's own rules pick the second looks and re-judges, merge them and price them
    live_verifier(cfg)        the real readers (paid calls; a MemorySpendStore, so the owner's month spend is untouched)
    estimate_usd(cfg, n)      an upper bound of what a live run of n products costs (pricing.estimate_call_usd)

Recorded readings: a cassette's "verdicts" are the readings of its "model" (the model that recorded them; the
current primary when the cassette names none) and "readings_by_model" {model id: {sku: {candidate: reading}}} holds
other models' readings of the same images. A reader asked about an image its model never read answers UNKNOWN for
it and counts it in RecordedReader.not_recorded: such a config is only as complete as its recorded answers, and the
reports say how complete that is.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Mapping, Optional

import runners

CONFIG_NAMES = ("current", "strong_primary", "strong_max2")
LIVE_BUDGET_USD = 1000.0       # a confirmed live run is not held back by the owner's month budget


@dataclasses.dataclass(frozen=True)
class Config:
    name: str
    primary: str                       # 'gemini:<model>' | 'claude:<model>'
    strong: Optional[str]              # None: no second look
    strong_max_calls: int
    rejudge_max_calls: int

    def describe(self) -> str:
        second = (f"strong {self.strong}, {self.strong_max_calls} second look(s), {self.rejudge_max_calls} re-judge(s)"
                  if self.strong and self.strong_max_calls > 0 else "no second look")
        return f"primary {self.primary}; {second}"


def current_config() -> Config:
    from catalog_match import settings
    from catalog_match.verifiers import cascade

    strong = cascade.strong_ref()
    return Config("current", cascade.primary_ref().id, strong.id if strong is not None else None,
                  settings.verifier_strong_max_calls(), settings.verifier_rejudge_max_calls())


def configs(names=CONFIG_NAMES) -> List[Config]:
    """The A/B configurations, built from the current settings."""
    cur = current_config()
    strong = cur.strong or "gemini:gemini-3.5-flash"
    out = {
        "current": cur,
        "strong_primary": Config("strong_primary", strong, None, 0, 0),
        "strong_max2": dataclasses.replace(cur, name="strong_max2", strong=strong, strong_max_calls=2),
    }
    unknown = [n for n in names if n not in out]
    if unknown:
        raise ValueError(f"unknown verifier config(s) {unknown}; known: {list(out)}")
    return [out[n] for n in names]


def recorded_model(cassette: Mapping[str, Any]) -> str:
    """The model whose readings a cassette's 'verdicts' are (the current primary when it names none)."""
    model = str(cassette.get("model") or "").strip().lower()
    return model if ":" in model else current_config().primary


def readings_for(cassette: Mapping[str, Any], model: str) -> Dict[str, Dict[str, Any]]:
    """{sku: {candidate: reading}} of one model in a cassette."""
    if model == recorded_model(cassette):
        return dict(cassette.get("verdicts") or {})
    return dict((cassette.get("readings_by_model") or {}).get(model) or {})


class RecordedReader:
    """A reader (catalog_match.models.Verifier) answering from one model's recorded readings, through the same
    code decision (verify.make_verdict) a live answer gets. Each call carries the usage entry a live call of that
    model would bill for every image it was asked about (tokens estimated from the image count, recorded or not),
    so the cascade prices it like a live one. live: a real reader of the same model that reads the images with no
    recorded answer (one call per batch, merged back in place, billed with its own usage)."""

    def __init__(self, model_id: str, readings: Mapping[str, Any], sku: Mapping[str, Any], scenario: str = "normal",
                 long_side: Optional[int] = None, live: Any = None):
        from catalog_match.verifiers import pricing
        from catalog_match.verifiers.registry import parse_model_id

        self.ref = parse_model_id(model_id)
        if self.ref is None:
            raise ValueError(f"not a model id: {model_id!r}")
        self.provider, self.model = self.ref.provider, self.ref.model
        self.sku, self.scenario = sku, scenario
        self.readings = dict(readings.get(sku["id"]) or {})
        self.index = runners.UrlIndex(sku)
        self.long_side = long_side or pricing.PRIMARY_LONG_SIDE
        self.live = live                   # a real reader of the same model for the images never recorded (--live)
        self.calls = 0
        self.images = 0
        self.not_recorded = 0
        self.live_calls = 0
        self.live_images = 0

    def verify(self, spec: Any, images: List[Any]) -> Any:
        from catalog_match.models import VerificationResult, VlmImageVerdict
        from catalog_match.verifiers import pricing
        from catalog_match.verify import make_verdict

        self.calls += 1
        self.images += len(images)
        if self.scenario == "gemini_down":
            return VerificationResult(status="unknown", calls=1, error="gemini_down", usage=[],
                                      verdicts=[VlmImageVerdict(index=i) for i in range(len(images))])
        verdicts: Dict[int, Any] = {}
        missing: List[int] = []
        for i, fetched in enumerate(images):
            cid = self.index.cid(getattr(getattr(fetched, "candidate", None), "image_url", None))
            entry = self.readings.get(cid) if cid else None
            if not entry:
                missing.append(i)
            elif not any(k in entry for k in runners.CassetteVerifier.FIELDS) and entry.get("recorded_decision"):
                verdicts[i] = VlmImageVerdict(index=i, decision=str(entry["recorded_decision"]))
            else:
                verdicts[i] = make_verdict(spec, i, entry)
        usage: List[Dict[str, Any]] = []
        # what a live call of this model would bill for the images it is asked about (an unrecorded one too: the
        # config's cost must not look lower because its answers were not recorded); a live call bills its own
        priced = len(images) - (len(missing) if self.live is not None else 0)
        if priced:
            tokens_in, tokens_out = pricing.estimate_tokens(self.provider, priced, "", self.long_side, self.model)
            usage.append({"provider": self.provider, "model": self.model, "images": priced,
                          "input_tokens": tokens_in, "output_tokens": tokens_out, "estimated": True})
        if missing and self.live is not None:
            # the images nobody recorded an answer for: one real call of the same model, merged back in place
            res = self.live.verify(spec, [images[i] for i in missing])
            self.live_calls += 1
            self.live_images += len(missing)
            usage.extend(dict(u, live=True) for u in (getattr(res, "usage", None) or []) if isinstance(u, dict))
            got = {v.index: v for v in (res.verdicts or [])} if res.status == "ok" else {}
            for j, i in enumerate(missing):
                v = got.get(j)
                verdicts[i] = dataclasses.replace(v, index=i) if v is not None else VlmImageVerdict(index=i)
            missing = []
        self.not_recorded += len(missing)
        for i in missing:
            verdicts[i] = VlmImageVerdict(index=i, decision="UNKNOWN")
        return VerificationResult(status="ok", verdicts=[verdicts[i] for i in range(len(images))], calls=1,
                                  usage=usage)


class ReplayCascade:
    """A CascadeVerifier over RecordedReaders, with the counters the reports need."""

    def __init__(self, config: Config, cassette: Mapping[str, Any], sku: Mapping[str, Any], scenario: str = "normal",
                 live: bool = False):
        from catalog_match.verifiers import cascade as cascade_mod, pricing
        from catalog_match.verifiers.cascade import CascadeVerifier
        from catalog_match.verifiers.registry import parse_model_id
        from catalog_match.verifiers.spend import MemorySpendStore

        self.config = config
        p_live = cascade_mod.build_reader(parse_model_id(config.primary), "primary") if live else None
        self.primary = RecordedReader(config.primary, readings_for(cassette, config.primary), sku, scenario,
                                      live=p_live)
        has_strong = bool(config.strong) and config.strong_max_calls > 0
        s_live = cascade_mod.build_reader(parse_model_id(config.strong), "strong") if live and has_strong else None
        self.strong = (RecordedReader(config.strong, readings_for(cassette, config.strong), sku, scenario,
                                      long_side=pricing.STRONG_LONG_SIDE, live=s_live)
                       if has_strong else None)
        self.cascade = CascadeVerifier(self.primary, self.strong, primary_ref=parse_model_id(config.primary),
                                       strong_ref=parse_model_id(config.strong) if config.strong else None,
                                       max_strong_calls=config.strong_max_calls, budget_usd=LIVE_BUDGET_USD,
                                       spend_store=MemorySpendStore(), prices=dict(pricing.DEFAULT_PRICES),
                                       max_rejudges=config.rejudge_max_calls)
        self.calls = 0
        self.max_images = 0

    def verify(self, spec: Any, images: List[Any]) -> Any:
        self.calls += 1
        self.max_images = max(self.max_images, len(images))
        return self.cascade.verify(spec, images)

    @property
    def stats(self) -> Dict[str, int]:
        readers = [r for r in (self.primary, self.strong) if r is not None]
        return {"primary_calls": self.primary.calls, "strong_calls": self.strong.calls if self.strong else 0,
                "not_recorded": sum(r.not_recorded for r in readers),
                "images_asked": sum(r.images for r in readers),
                "live_calls": sum(r.live_calls for r in readers), "live_images": sum(r.live_images for r in readers)}


def replay_verifier(config: Config, cassette: Mapping[str, Any], sku: Mapping[str, Any],
                    scenario: str = "normal", live: bool = False) -> ReplayCascade:
    """The config over recorded answers; live=True reads the images with no recorded answer with the real model."""
    return ReplayCascade(config, cassette, sku, scenario, live=live)


def missing_answers(config: Config, cassette: Mapping[str, Any], golden: Mapping[str, Any]) -> Dict[str, int]:
    """Per role, how many fetchable candidates have no recorded answer of that role's model (what --live may read)."""
    out = {"primary": 0, "strong": 0}
    roles = [("primary", config.primary)] + ([("strong", config.strong)]
                                              if config.strong and config.strong_max_calls > 0 else [])
    for role, model in roles:
        readings = readings_for(cassette, model)
        for sku in golden.get("skus", []):
            have = readings.get(sku["id"]) or {}
            out[role] += sum(1 for c in sku.get("candidates", []) if c.get("download", "ok") == "ok"
                             and not c.get("source_only") and c["id"] not in have)
    return out


def estimate_gap_usd(config: Config, cassette: Mapping[str, Any], golden: Mapping[str, Any],
                     prices: Optional[Mapping[str, Any]] = None) -> float:
    """Upper bound of what --live costs for one config: every image without a recorded answer read once, in calls
    of 4 for the primary and of 1 for the strong model (at most its looks per product)."""
    import math

    from catalog_match.verifiers import pricing
    from catalog_match.verifiers.registry import parse_model_id

    prices = prices if prices is not None else pricing.DEFAULT_PRICES
    gaps = missing_answers(config, cassette, golden)
    usd = math.ceil(gaps["primary"] / pricing.PRIMARY_IMAGES) * pricing.estimate_call_usd(
        parse_model_id(config.primary), prices, pricing.PRIMARY_IMAGES, pricing.PRIMARY_LONG_SIDE)
    if gaps["strong"]:
        looks = min(gaps["strong"], len(golden.get("skus", [])) * (config.strong_max_calls + config.rejudge_max_calls))
        usd += looks * pricing.estimate_call_usd(parse_model_id(config.strong), prices, 1, pricing.STRONG_LONG_SIDE)
    return round(usd, 4)


def live_verifier(config: Config) -> Any:
    """The real readers of a config (paid calls). Only after the caller printed estimate_usd and got a yes."""
    from catalog_match.verifiers import cascade, pricing
    from catalog_match.verifiers.registry import parse_model_id
    from catalog_match.verifiers.spend import MemorySpendStore

    p_ref = parse_model_id(config.primary)
    s_ref = parse_model_id(config.strong) if config.strong else None
    return cascade.CascadeVerifier(
        cascade.build_reader(p_ref, "primary"),
        cascade.build_reader(s_ref, "strong") if s_ref is not None and config.strong_max_calls > 0 else None,
        primary_ref=p_ref, strong_ref=s_ref, max_strong_calls=config.strong_max_calls, budget_usd=LIVE_BUDGET_USD,
        spend_store=MemorySpendStore(), prices=pricing.load_prices(), max_rejudges=config.rejudge_max_calls)


def estimate_usd(config: Config, n_products: int, prices: Optional[Mapping[str, Any]] = None) -> float:
    """Upper bound of a live run: per product two primary calls of 4 images (pipeline.MAX_VERIFY_CALLS) plus every
    allowed strong look and re-judge of one image."""
    from catalog_match import pipeline
    from catalog_match.verifiers import pricing
    from catalog_match.verifiers.registry import parse_model_id

    prices = prices if prices is not None else pricing.DEFAULT_PRICES
    per = pipeline.MAX_VERIFY_CALLS * pricing.estimate_call_usd(parse_model_id(config.primary), prices,
                                                                pricing.PRIMARY_IMAGES, pricing.PRIMARY_LONG_SIDE)
    if config.strong and config.strong_max_calls > 0:
        looks = config.strong_max_calls + config.rejudge_max_calls
        per += looks * pricing.estimate_call_usd(parse_model_id(config.strong), prices, 1, pricing.STRONG_LONG_SIDE)
    return round(per * max(0, int(n_products)), 4)


def usage_usd(outcomes: List[Mapping[str, Any]]) -> Dict[str, float]:
    """USD the run's verifier calls cost, per role (the usage entries the cascade priced)."""
    out = {"primary": 0.0, "strong": 0.0}
    for o in outcomes:
        for u in (o.get("verifier") or {}).get("usage") or []:
            role = "strong" if u.get("role") == "strong" else "primary"
            out[role] += float(u.get("usd") or 0.0)
    return {k: round(v, 6) for k, v in out.items()}


def confirm(prompt: str, answer: Optional[str] = None, stream=None) -> bool:
    """True only for an explicit yes ('--yes' passes answer='yes'); a closed input is a no."""
    import sys

    stream = stream or sys.stdout
    if answer is None:
        try:
            print(prompt, end=" ", file=stream, flush=True)
            answer = input()
        except EOFError:
            return False
    return str(answer).strip().lower() in ("y", "yes")
