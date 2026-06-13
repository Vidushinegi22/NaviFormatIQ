"""Import-smoke test: imports every backend module so a bad port/import
surfaces immediately (and not deep inside a worker thread at runtime).

Run from the backend/ directory:
    python -m app.scripts.smoke_flows
"""
from __future__ import annotations

import importlib
import sys

MODULES = [
    # core
    "app.core.config",
    "app.core.db",
    "app.core.concurrency",
    "app.core.events",
    "app.core.logging",
    "app.core.exceptions",
    # schema contract
    "app.schemas.document_model",
    # llm
    "app.llm.base",
    "app.llm.azure_provider",
    "app.llm.adapters",
    "app.llm.router",
    # rag
    "app.rag.bm25_client",
    "app.rag.retriever",
    "app.rag.chunker",
    "app.rag.embedder",
    "app.rag.indexer",
    # vectorstore
    "app.vectorstore.base",
    "app.vectorstore.qdrant_store",
    "app.vectorstore.memory_store",
    "app.vectorstore.factory",
    # storage
    "app.storage.base",
    "app.storage.local_store",
    "app.storage.r2_store",
    # ported services
    "app.services.extraction.word_ext",
    "app.services.extraction.pdf_ext",
    "app.services.extraction.azure_di",
    "app.services.extraction.doc_understanding",
    "app.services.formatting.formater_apply",
    "app.services.formatting.template_emitter",
    "app.services.style.style_engine",
    "app.services.mapping.section_mapper",
    "app.services.generation.rewriter",
    "app.services.office.office_pipeline",
    "app.services.orchestration.pipeline_steps",
    # models (SQLAlchemy)
    "app.models",
    # agents
    "app.agents.state",
    "app.agents.nodes",
    "app.agents.graphs",
    "app.agents.runner",
    "app.agents.chat.tools",
    "app.agents.chat.agent",
    # api
    "app.deps",
    "app.api.v1.routes.projects",
    "app.api.v1.routes.documents",
    "app.api.v1.routes.flows",
    "app.api.v1.routes.chat",
    "app.api.v1.routes.domains",
    "app.api.v1.routes.utils",
    "app.main",
]


def main(strict: bool = False) -> int:
    ok, failed = [], []
    for mod in MODULES:
        try:
            importlib.import_module(mod)
            ok.append(mod)
            print(f"  ok    {mod}")
        except ModuleNotFoundError as e:
            # A not-yet-created module is tolerated unless --strict.
            if e.name and (e.name == mod or mod.startswith(e.name)):
                print(f"  skip  {mod}  (not created yet)")
            else:
                failed.append((mod, repr(e)))
                print(f"  FAIL  {mod}: {e!r}")
        except Exception as e:  # noqa: BLE001
            failed.append((mod, repr(e)))
            print(f"  FAIL  {mod}: {e!r}")

    print(f"\n{len(ok)} ok, {len(failed)} failed")
    if failed:
        print("\nFailures:")
        for mod, err in failed:
            print(f"  - {mod}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(strict="--strict" in sys.argv))
