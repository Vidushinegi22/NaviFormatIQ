"""Export service — turns a finished run into downloadable deliverables.

`builder.list_exports` enumerates what a run can produce; `builder.build_export`
produces the bytes for one deliverable. Both are pure of DB/HTTP and SYNC — the
route layer (`app/api/v1/routes/exports.py`) handles persistence + streaming and
must invoke them via `run_sync` / `run_office`.
"""
from app.services.export.builder import (  # noqa: F401
    CATEGORY_ORDER,
    ExportError,
    ExportSpec,
    build_export,
    export_filename,
    list_exports,
)
