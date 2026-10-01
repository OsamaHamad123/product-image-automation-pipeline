"""catalog_match: the search-and-select core that finds the correct product image.

Stages, each in its own module:
    identity  - sheet row -> SkuSpec (bilingual brand, size, variants, GTIN)
    retrieve  - deterministic query plan -> pooled candidates from sanctioned providers
    score     - identity evidence -> tier + hard rejects (never image quality)
    fetch     - download + decode + content-addressed store
    quality   - soft quality features; only truly broken images are hard-rejected
    verify    - one comparative Gemini call per batch, fail-closed
    decide    - route to AUTO_PUBLISH / REVIEW_* / NOT_FOUND / *_DOWN
    pipeline  - wires the stages together
    facade    - adapts SearchOutcome to the legacy dict that main.py / cli_bridge.py expect

The shared types live in catalog_match.models.
"""
