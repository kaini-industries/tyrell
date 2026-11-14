#!/bin/bash
# Executed under the VM-wide flock by systemd; retain outcome across SSH/VM restarts.
set -euo pipefail
job=${1:?job identifier}
input=${2:?input directory}
mode=${3:-fresh}
bundle_sha=${4:?bundle checksum}
result="${input%/input}/result.json"
finish() {
    status=$?
    trap - EXIT
    python3 - "$result" "$job" "$status" "$bundle_sha" <<'PY'
import json,os,sys
from pathlib import Path
p=Path(sys.argv[1]); temporary=p.with_suffix('.partial')
temporary.write_text(json.dumps({'job_id':sys.argv[2],'exit_status':int(sys.argv[3]),'bundle_sha256':sys.argv[4]})+'\n')
os.replace(temporary,p)
PY
    exit "$status"
}
trap finish EXIT
printf '%s  %s\n' "$bundle_sha" "${input%/input}/image-input.tar" | sha256sum -c -
build_mode=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("build_mode", "source"))' "$input/image-metadata.json")
case "$build_mode" in
    source) /bin/bash "$input/builder/build-image.sh" "$input" "$mode" ;;
    stock-image) /bin/bash "$input/builder/build-stock-image.sh" "$input" "$mode" ;;
    *) printf 'Unsupported image build mode: %s\n' "$build_mode" >&2; exit 1 ;;
esac
