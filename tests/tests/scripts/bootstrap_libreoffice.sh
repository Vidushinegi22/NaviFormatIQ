#!/usr/bin/env bash
# Bootstrap LibreOffice so the headless `soffice --convert-to docx` pass
# actually refreshes TOC + page-number fields on save.
#
# Why: by default LibreOffice does NOT update field links on open. We need
# the "UpdateLinks" user setting set to 2 (always update without prompt).
#
# Usage:
#   bash tests/scripts/bootstrap_libreoffice.sh
#
# Idempotent — safe to re-run.

set -euo pipefail

if command -v soffice >/dev/null 2>&1; then
  SOFFICE_BIN="$(command -v soffice)"
elif command -v libreoffice >/dev/null 2>&1; then
  SOFFICE_BIN="$(command -v libreoffice)"
elif [[ -x "/Applications/LibreOffice.app/Contents/MacOS/soffice" ]]; then
  SOFFICE_BIN="/Applications/LibreOffice.app/Contents/MacOS/soffice"
else
  echo "ERROR: LibreOffice (soffice) not found." >&2
  echo "Install via:  brew install --cask libreoffice   (macOS)" >&2
  echo "          or: sudo apt-get install -y libreoffice (Debian/Ubuntu)" >&2
  exit 1
fi

# Force-create the user profile so registrymodifications.xcu exists.
TMPDIR_FOR_BOOT="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_FOR_BOOT}"' EXIT

PROBE_DOCX="${TMPDIR_FOR_BOOT}/probe.docx"
# Smallest legal .docx — just a placeholder so soffice has something to open.
python3 -c "
from docx import Document
d = Document()
d.add_paragraph('bootstrap')
d.save('${PROBE_DOCX}')
"

"${SOFFICE_BIN}" --headless --norestore --nolockcheck --nofirststartwizard \
  --convert-to docx --outdir "${TMPDIR_FOR_BOOT}/out" "${PROBE_DOCX}" >/dev/null || true

# Locate registrymodifications.xcu and patch the UpdateLinks setting.
PROFILE_ROOT="${HOME}/Library/Application Support/LibreOffice/4/user"
[[ -d "${PROFILE_ROOT}" ]] || PROFILE_ROOT="${HOME}/.config/libreoffice/4/user"
REG_FILE="${PROFILE_ROOT}/registrymodifications.xcu"

if [[ ! -f "${REG_FILE}" ]]; then
  echo "WARN: registrymodifications.xcu not found at ${REG_FILE}." >&2
  echo "      Skipping UpdateLinks patch — TOCs may not refresh on convert." >&2
  exit 0
fi

if grep -q 'Common/Load/UpdateLinks' "${REG_FILE}"; then
  echo "UpdateLinks already configured."
else
  TMPF="$(mktemp)"
  sed -e 's#</oor:items>#<item oor:path="/org.openoffice.Office.Common/Load"><prop oor:name="UpdateLinks" oor:op="fuse"><value>2</value></prop></item></oor:items>#' "${REG_FILE}" > "${TMPF}"
  mv "${TMPF}" "${REG_FILE}"
  echo "Patched ${REG_FILE} → UpdateLinks=2."
fi

echo "LibreOffice bootstrap complete. Binary: ${SOFFICE_BIN}"
