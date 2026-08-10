#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
python_bin="${PYTHON:-python}"

cd "$repo_root"
"$python_bin" -m PyInstaller --noconfirm --clean guarded-agent.spec
test -x dist/guarded-agent
