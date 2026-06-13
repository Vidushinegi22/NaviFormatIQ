"""
Headless LibreOffice subprocess wrapper.

Two jobs:
  1. Refresh TOC + page-number fields by round-tripping the .docx through
     LibreOffice (open + save updates Word fields).
  2. Export the .docx to PDF or PDF/A-1b for the final downloadable artifact.

LibreOffice is invoked via the ``soffice`` binary resolved by ``config.py``.
If it can't be found we raise ``LibreOfficeUnavailable`` so the orchestrator
can degrade gracefully (return the .docx without a refreshed TOC, or skip
PDF export with a warning).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from config import settings


class LibreOfficeUnavailable(RuntimeError):
    """Raised when the ``soffice`` binary cannot be located."""


OutputFormat = Literal["docx", "pdf", "pdfa"]


def _soffice_or_raise() -> str:
    bin_ = settings.resolved_soffice()
    if not bin_:
        raise LibreOfficeUnavailable(
            "LibreOffice (soffice) not found on PATH. Install LibreOffice or "
            "set SOFFICE_BIN to the full binary path."
        )
    return bin_


def _format_args(out_format: OutputFormat) -> tuple[str, str]:
    """Return (extension, --convert-to value) for the requested format."""
    if out_format == "docx":
        return ("docx", "docx")
    if out_format == "pdf":
        return ("pdf", "pdf")
    if out_format == "pdfa":
        # SelectPdfVersion=1 → PDF/A-1b
        return (
            "pdf",
            "pdf:writer_pdf_Export:SelectPdfVersion=1,UseTaggedPDF=true",
        )
    raise ValueError(f"Unknown output format: {out_format}")


def convert(docx_bytes: bytes, out_format: OutputFormat) -> bytes:
    """Convert a .docx to the requested format and return the output bytes.

    ``docx``  → round-trip through soffice (updates TOC, page-number fields).
    ``pdf``   → standard PDF export.
    ``pdfa``  → PDF/A-1b export.
    """
    soffice = _soffice_or_raise()

    with tempfile.TemporaryDirectory() as workdir:
        in_path = Path(workdir) / "input.docx"
        in_path.write_bytes(docx_bytes)
        out_dir = Path(workdir) / "out"
        out_dir.mkdir()

        ext, convert_to = _format_args(out_format)
        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nofirststartwizard",
            "--convert-to",
            convert_to,
            "--outdir",
            str(out_dir),
            str(in_path),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=180,
                check=False,
            )
        except FileNotFoundError as e:  # pragma: no cover
            raise LibreOfficeUnavailable(str(e)) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("LibreOffice conversion timed out") from e

        if proc.returncode != 0:
            raise RuntimeError(
                f"LibreOffice failed ({proc.returncode}): "
                f"{proc.stderr.decode('utf-8', errors='replace')[:500]}"
            )

        # Find the produced file
        produced = list(out_dir.glob(f"*.{ext}"))
        if not produced:
            raise RuntimeError("LibreOffice produced no output file.")
        return produced[0].read_bytes()


def refresh_fields(docx_bytes: bytes) -> bytes:
    """Round-trip through soffice to update TOC/page-number fields."""
    return convert(docx_bytes, "docx")


def export_pdf(docx_bytes: bytes, pdfa: bool = False) -> bytes:
    return convert(docx_bytes, "pdfa" if pdfa else "pdf")


def available() -> bool:
    """True iff a usable soffice binary can be located."""
    return settings.resolved_soffice() is not None
