"""Guideline ingestion + compliance audit services.

Reusable, DB-pure building blocks: parse a guideline PDF into a section tree
(``pdf_outline``), extract atomic checkable requirements per section
(``requirement_extractor``), embed + index the guideline text into a
per-guideline Qdrant collection (``embed_index``), and orchestrate the three
(``ingest``). The route/script layer persists the results to Neon.
"""
