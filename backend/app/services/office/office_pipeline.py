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
import subprocess
import tempfile
import zipfile
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Literal

from app.core.config import settings


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


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _program_dir_candidates(soffice: str) -> list[Path]:
    """Likely LibreOffice program dirs for system and App Platform installs."""
    bin_path = Path(soffice)
    parent = bin_path.parent
    candidates: list[Path] = []

    # Direct installs can point SOFFICE_BIN at .../libreoffice/program/soffice.
    if parent.name == "program":
        candidates.append(parent)

    # Package managers usually expose /usr/bin/soffice while the private shared
    # libraries, including libreglo.so, live under ../lib/libreoffice/program.
    for base in (parent, *parent.parents):
        candidates.append(base / "lib" / "libreoffice" / "program")
        candidates.append(base / "usr" / "lib" / "libreoffice" / "program")

    # DigitalOcean's Aptfile buildpack installs packages under this layer path.
    candidates.append(Path("/layers/digitalocean_apt/apt/usr/lib/libreoffice/program"))
    candidates.append(Path("/usr/lib/libreoffice/program"))
    candidates.append(Path("/usr/lib64/libreoffice/program"))
    return _dedupe_paths(candidates)


def _prepend_env_paths(env: dict[str, str], key: str, paths: list[Path]) -> None:
    current = [p for p in env.get(key, "").split(os.pathsep) if p]
    merged: list[str] = []
    for path in [str(p) for p in paths if p.exists()] + current:
        if path and path not in merged:
            merged.append(path)
    if merged:
        env[key] = os.pathsep.join(merged)


def _libreoffice_env(soffice: str) -> dict[str, str]:
    """Return an env where LibreOffice can find its private shared libraries."""
    env = os.environ.copy()
    program_dirs = [p for p in _program_dir_candidates(soffice) if p.exists()]
    if not program_dirs:
        return env

    lib_dirs: list[Path] = []
    for program_dir in program_dirs:
        lib_dirs.append(program_dir)
        # For Aptfile installs, also include the layer's normal library dirs.
        try:
            usr_dir = program_dir.parents[2]  # .../usr
            apt_root = usr_dir.parent
        except IndexError:
            continue
        lib_dirs.extend(
            [
                usr_dir / "lib",
                usr_dir / "lib" / "x86_64-linux-gnu",
                apt_root / "lib",
                apt_root / "lib" / "x86_64-linux-gnu",
            ]
        )

    _prepend_env_paths(env, "LD_LIBRARY_PATH", _dedupe_paths(lib_dirs))
    env.setdefault("UNO_PATH", str(program_dirs[0]))
    fundamentalrc = program_dirs[0] / "fundamentalrc"
    if fundamentalrc.exists():
        env.setdefault("URE_BOOTSTRAP", f"vnd.sun.star.pathname:{fundamentalrc}")
    return env


@lru_cache(maxsize=4)
def _soffice_usable(soffice: str) -> bool:
    del soffice  # cache key; convert resolves the same configured binary.
    try:
        convert(_probe_docx_bytes(), "pdf")
    except (LibreOfficeUnavailable, RuntimeError, OSError, subprocess.TimeoutExpired):
        return False
    return True


@lru_cache(maxsize=1)
def _probe_docx_bytes() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>LibreOffice probe</w:t></w:r></w:p>
    <w:sectPr/>
  </w:body>
</w:document>""",
        )
    return buf.getvalue()


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
        profile_dir = Path(workdir) / "lo-profile"
        profile_dir.mkdir()

        ext, convert_to = _format_args(out_format)
        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile_dir.as_uri()}",
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
                env=_libreoffice_env(soffice),
            )
        except FileNotFoundError as e:  # pragma: no cover
            raise LibreOfficeUnavailable(str(e)) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("LibreOffice conversion timed out") from e

        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")[:500]
            if proc.returncode == 127 or "error while loading shared libraries" in stderr:
                raise LibreOfficeUnavailable(
                    "LibreOffice runtime dependencies are not available: "
                    f"{stderr}"
                )
            raise RuntimeError(
                f"LibreOffice failed ({proc.returncode}): {stderr}"
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
    soffice = settings.resolved_soffice()
    return bool(soffice and _soffice_usable(soffice))
