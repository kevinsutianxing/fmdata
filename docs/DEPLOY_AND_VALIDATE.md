# fmdata Deployment and End-to-End Validation Runbook

## Purpose

This runbook validates the real deployment chain, not only unit tests:

```text
fmdata code
  -> isolated Python environment
  -> authenticated research API
  -> immutable snapshot store
  -> real cached dataset
  -> raw/normalized hash verification
  -> multi-agent pipeline materialization
  -> deterministic acquisition gate
```

A successful service start is not enough. A production candidate must pass the acceptance criteria at the end of this document.

## Scope and safety

This runbook targets the implementation in:

```text
repository: kevinsutianxing/fmdata
branch: feat/research-snapshot-api
Draft PR: https://github.com/kevinsutianxing/fmdata/pull/1
```

The cross-repository validation uses:

```text
repository: kevinsutianxing/multi-agent-pipeline
branch: feat/codex-financial-research-system
Draft PR: https://github.com/kevinsutianxing/multi-agent-pipeline/pull/1
```

Do not merge either PR merely because the service starts. Keep both PRs in Draft until a real snapshot and the downstream acquisition gate pass.

The example systemd unit assumes:

```text
service user: ubuntu
repository path: /home/ubuntu/fmdata
virtualenv: /home/ubuntu/fmdata/.venv
listen address: 127.0.0.1:1934
```

When the actual host differs, update `User`, `Group`, `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` in `fmdata.service` before installing it.

---

# Phase 0 — Record the current deployment

Run this on the fmdata host before changing anything:

```bash
set -euo pipefail

whoami
hostname
python3 --version
git --version

if systemctl list-unit-files | grep -q '^fmdata.service'; then
  sudo systemctl status fmdata --no-pager || true
  sudo systemctl cat fmdata || true
  sudo journalctl -u fmdata -n 100 --no-pager || true
fi
```

When `/home/ubuntu/fmdata` already exists:

```bash
cd /home/ubuntu/fmdata

git status --short
git branch --show-current
git rev-parse HEAD
```

**Stop here when `git status --short` shows uncommitted changes.** Preserve or commit them before switching branches. Do not overwrite a working data service with an unreviewed checkout.

Back up the current unit and environment file when present:

```bash
BACKUP_DIR="$HOME/fmdata-deploy-backup-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"

sudo cp -a /etc/systemd/system/fmdata.service "$BACKUP_DIR/" 2>/dev/null || true
cp -a /home/ubuntu/fmdata/.env "$BACKUP_DIR/" 2>/dev/null || true

printf 'Backup directory: %s\n' "$BACKUP_DIR"
```

Do not print `.env` to the terminal or paste it into an issue, PR, or chat.

---

# Phase 1 — Check out the research snapshot branch

For a new checkout:

```bash
cd /home/ubuntu

git clone https://github.com/kevinsutianxing/fmdata.git fmdata
cd fmdata
git switch feat/research-snapshot-api
```

For an existing clean checkout:

```bash
cd /home/ubuntu/fmdata

git fetch origin
git switch feat/research-snapshot-api
git pull --ff-only origin feat/research-snapshot-api
```

Record the exact revision:

```bash
git rev-parse HEAD | tee /tmp/fmdata-deployed-commit.txt
```

Expected result: the current branch is `feat/research-snapshot-api` and the worktree is clean.

```bash
git branch --show-current
git status --short
```

---

# Phase 2 — Build an isolated environment and run tests

Create a fresh virtual environment:

```bash
cd /home/ubuntu/fmdata

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e . pytest httpx
```

Compile the new research modules:

```bash
python -m py_compile \
  fmdata/research_snapshot.py \
  fmdata/research_server.py
```

Run the snapshot contract suite:

```bash
python -m pytest -q --tb=short tests/test_research_snapshot.py
```

Run the existing fmdata tests as a compatibility check:

```bash
python -m pytest -q --tb=short tests/test_fmdata.py tests/test_server.py
```

A failure in the existing suite is a deployment blocker unless it is independently confirmed to be an already-known environment-only failure and recorded with evidence.

Record installed versions:

```bash
python -m pip freeze | sort > /tmp/fmdata-pip-freeze.txt
python -V
```

---

# Phase 3 — Configure the research-only boundary

Generate a separate research key. Do not reuse a provider token such as a Tushare token.

```bash
RESEARCH_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
printf 'Generated a research key in shell memory. Do not print it.\n'
```

Create a protected snapshot directory:

```bash
sudo install -d \
  -o ubuntu \
  -g ubuntu \
  -m 0700 \
  /var/lib/fmdata/snapshots
```

Safely update `.env` without displaying existing secrets:

```bash
cd /home/ubuntu/fmdata
umask 077
touch .env
chmod 600 .env

RESEARCH_KEY="$RESEARCH_KEY" python - <<'PY'
from pathlib import Path
import os

path = Path('.env')
updates = {
    'FMDATA_RESEARCH_KEY': os.environ['RESEARCH_KEY'],
    'FMDATA_SNAPSHOT_DIR': '/var/lib/fmdata/snapshots',
}

existing = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
output = []
seen = set()
for line in existing:
    stripped = line.strip()
    if not stripped or stripped.startswith('#') or '=' not in line:
        output.append(line)
        continue
    key, _ = line.split('=', 1)
    key = key.strip()
    if key in updates:
        output.append(f'{key}={updates[key]}')
        seen.add(key)
    else:
        output.append(line)
for key, value in updates.items():
    if key not in seen:
        output.append(f'{key}={value}')
path.write_text('\n'.join(output).rstrip() + '\n', encoding='utf-8')
PY
```

Confirm permissions without printing file contents:

```bash
stat -c '%a %U %G %n' .env /var/lib/fmdata/snapshots
```

Expected:

```text
600 ubuntu ubuntu .env
700 ubuntu ubuntu /var/lib/fmdata/snapshots
```

Keep the research key available for the remaining shell session without echoing it:

```bash
export FMDATA_RESEARCH_KEY="$RESEARCH_KEY"
export FMDATA_URL="http://127.0.0.1:1934"
export FMDATA_SNAPSHOT_DIR="/var/lib/fmdata/snapshots"
```

When using a new terminal later, load only the required variables from the protected environment file rather than printing the file.

---

# Phase 4 — Foreground smoke test before systemd activation

Test on an alternate local port first so the existing service remains untouched:

```bash
cd /home/ubuntu/fmdata
. .venv/bin/activate

set -a
. ./.env
set +a

python -m uvicorn fmdata.research_server:app \
  --host 127.0.0.1 \
  --port 1935 \
  >/tmp/fmdata-research-smoke.log 2>&1 &
SMOKE_PID=$!

cleanup() {
  kill "$SMOKE_PID" 2>/dev/null || true
}
trap cleanup EXIT

sleep 3
curl --fail --silent --show-error \
  http://127.0.0.1:1935/research/health \
  | python -m json.tool
```

Expected health response:

```json
{
  "status": "ok",
  "service": "fmdata",
  "contract": "research-snapshot-v1",
  "self_validation": false
}
```

Verify authentication fails closed:

```bash
STATUS_CODE="$(curl --silent --output /tmp/fmdata-unauth.json --write-out '%{http_code}' \
  http://127.0.0.1:1935/research/catalog)"

printf 'Unauthenticated catalogue HTTP status: %s\n' "$STATUS_CODE"
python -m json.tool /tmp/fmdata-unauth.json || true

test "$STATUS_CODE" = "403"
```

Verify the research key succeeds:

```bash
curl --fail --silent --show-error \
  -H "X-Research-Key: $FMDATA_RESEARCH_KEY" \
  http://127.0.0.1:1935/research/catalog \
  > /tmp/fmdata-catalog.json

python - <<'PY'
import json
from pathlib import Path

catalog = json.loads(Path('/tmp/fmdata-catalog.json').read_text())
print('dataset_count =', catalog.get('count'))
rows = []
for name, record in sorted((catalog.get('datasets') or {}).items()):
    rows.append({
        'dataset': name,
        'rows': record.get('rows'),
        'file': record.get('file'),
        'research_ready': record.get('research_ready'),
        'limitations': record.get('limitations'),
    })
for row in rows[:25]:
    print(row)
PY
```

Stop the alternate-port smoke server:

```bash
cleanup
trap - EXIT
```

Inspect logs when any command fails:

```bash
tail -n 200 /tmp/fmdata-research-smoke.log
```

---

# Phase 5 — Install and start the systemd service

The committed service unit expects `/home/ubuntu/fmdata/.venv` and runs as the unprivileged `ubuntu` user.

Review it first:

```bash
cd /home/ubuntu/fmdata
cat fmdata.service
```

Install and start:

```bash
sudo cp fmdata.service /etc/systemd/system/fmdata.service
sudo systemctl daemon-reload
sudo systemctl enable fmdata
sudo systemctl restart fmdata
```

Check status and logs:

```bash
sudo systemctl status fmdata --no-pager
sudo journalctl -u fmdata -n 200 --no-pager
```

Verify the active process uses the virtual environment and loopback address:

```bash
sudo systemctl show fmdata \
  -p User \
  -p Group \
  -p ExecStart \
  -p EnvironmentFiles \
  -p ActiveState \
  -p SubState

ss -lntp | grep ':1934'
```

Expected:

- user/group are `ubuntu`;
- executable is `/home/ubuntu/fmdata/.venv/bin/python`;
- listener is `127.0.0.1:1934`, not `0.0.0.0:1934`;
- service state is active/running.

Run the production-port health and authentication checks again:

```bash
curl --fail --silent --show-error \
  "$FMDATA_URL/research/health" \
  | python -m json.tool

curl --fail --silent --show-error \
  -H "X-Research-Key: $FMDATA_RESEARCH_KEY" \
  "$FMDATA_URL/research/catalog" \
  > /tmp/fmdata-production-catalog.json
```

---

# Phase 6 — Select a real cached dataset

The repository does not commit the production CSV files, so choose a nonempty registered file from the deployed host rather than assuming a dataset exists.

```bash
DATASET="$(python - <<'PY'
import json
from pathlib import Path

catalog = json.loads(Path('/tmp/fmdata-production-catalog.json').read_text())
for name, record in sorted((catalog.get('datasets') or {}).items()):
    file_name = str(record.get('file') or '')
    rows = int(record.get('rows') or 0)
    if rows > 0 and file_name and not file_name.endswith('/'):
        print(name)
        break
PY
)"

if [ -z "$DATASET" ]; then
  echo 'No nonempty file-backed dataset is registered.' >&2
  exit 1
fi

printf 'Selected dataset: %s\n' "$DATASET"
```

Inspect that dataset's research metadata:

```bash
DATASET="$DATASET" python - <<'PY'
import json
import os
from pathlib import Path

catalog = json.loads(Path('/tmp/fmdata-production-catalog.json').read_text())
record = catalog['datasets'][os.environ['DATASET']]
print(json.dumps(record, ensure_ascii=False, indent=2))
PY
```

A dataset with `research_ready=false` may still be used for the **fail-closed smoke test**, but not for a passing research acquisition gate.

---

# Phase 7 — Create and verify a real immutable snapshot

Use the current date as the operational smoke-test as-of date. Historical backtests require a separately verified historical availability policy.

```bash
AS_OF="$(date -u +%F)"
export DATASET AS_OF

python - <<'PY' > /tmp/fmdata-snapshot-request.json
import json
import os

print(json.dumps({
    'dataset': os.environ['DATASET'],
    'as_of': os.environ['AS_OF'],
    'parameters': {},
    'fields': [],
    'entity_ids': [],
    'start_date': None,
    'end_date': None,
    'expected_semantics': {},
}, ensure_ascii=False, indent=2))
PY

curl --fail --silent --show-error \
  -X POST \
  -H "X-Research-Key: $FMDATA_RESEARCH_KEY" \
  -H 'Content-Type: application/json' \
  --data @/tmp/fmdata-snapshot-request.json \
  "$FMDATA_URL/research/snapshots" \
  > /tmp/fmdata-snapshot-response.json

python -m json.tool /tmp/fmdata-snapshot-response.json
```

Extract identity fields without requiring `jq`:

```bash
eval "$(python - <<'PY'
import json
import shlex
from pathlib import Path

value = json.loads(Path('/tmp/fmdata-snapshot-response.json').read_text())
for shell_name, key in (
    ('SNAPSHOT_ID', 'snapshot_id'),
    ('SNAPSHOT_STATUS', 'status'),
    ('NORMALIZED_SHA256', 'content_sha256'),
    ('RAW_SHA256', 'raw_content_sha256'),
):
    print(f'export {shell_name}={shlex.quote(str(value.get(key) or ""))}')
PY
)"

printf 'snapshot_id=%s\nstatus=%s\n' "$SNAPSHOT_ID" "$SNAPSHOT_STATUS"
test -n "$SNAPSHOT_ID"
```

Download the server manifest and both immutable objects:

```bash
curl --fail --silent --show-error \
  -H "X-Research-Key: $FMDATA_RESEARCH_KEY" \
  "$FMDATA_URL/research/snapshots/$SNAPSHOT_ID/manifest" \
  > /tmp/fmdata-snapshot-manifest.json

curl --fail --silent --show-error \
  -H "X-Research-Key: $FMDATA_RESEARCH_KEY" \
  "$FMDATA_URL/research/snapshots/$SNAPSHOT_ID/data" \
  > /tmp/fmdata-normalized.csv

curl --fail --silent --show-error \
  -H "X-Research-Key: $FMDATA_RESEARCH_KEY" \
  "$FMDATA_URL/research/snapshots/$SNAPSHOT_ID/raw" \
  > /tmp/fmdata-raw.bin
```

Verify identity and hashes independently:

```bash
SNAPSHOT_ID="$SNAPSHOT_ID" \
NORMALIZED_SHA256="$NORMALIZED_SHA256" \
RAW_SHA256="$RAW_SHA256" \
python - <<'PY'
import hashlib
import json
import os
from pathlib import Path

response = json.loads(Path('/tmp/fmdata-snapshot-response.json').read_text())
manifest = json.loads(Path('/tmp/fmdata-snapshot-manifest.json').read_text())

for field in (
    'snapshot_id',
    'source_id',
    'evidence_id',
    'dataset_id',
    'request_sha256',
    'content_sha256',
    'raw_content_sha256',
):
    assert response.get(field) == manifest.get(field), field

normalized = Path('/tmp/fmdata-normalized.csv').read_bytes()
raw = Path('/tmp/fmdata-raw.bin').read_bytes()
assert hashlib.sha256(normalized).hexdigest() == os.environ['NORMALIZED_SHA256']
assert hashlib.sha256(raw).hexdigest() == os.environ['RAW_SHA256']
assert manifest['validation_status'] == 'PENDING'
assert manifest['snapshot_id'] == os.environ['SNAPSHOT_ID']

print('snapshot identity and both hashes verified')
print('status =', manifest['status'])
print('row_count =', manifest['row_count'])
print('limitations =', manifest.get('limitations') or [])
print('conflicts =', manifest.get('conflicts') or [])
PY
```

Verify snapshot files were written to protected storage:

```bash
sudo find /var/lib/fmdata/snapshots \
  -maxdepth 2 \
  -type f \
  -printf '%m %u %g %s %p\n' \
  | tail -n 20
```

## Interpreting the snapshot result

### `status=OK`

The declared Recipe semantics are complete enough for the current contract and no direct conflict was detected. Continue to the cross-repository test.

### `status=PARTIAL`

The service is working correctly but the selected Recipe lacks material semantics. This is a successful **fail-closed infrastructure test**, not a research pass.

Inspect:

```bash
python - <<'PY'
import json
from pathlib import Path
value = json.loads(Path('/tmp/fmdata-snapshot-manifest.json').read_text())
print('\n'.join(value.get('limitations') or []))
PY
```

Update and independently verify the selected Recipe's `semantics` before rerunning. Do not invent values merely to obtain `OK`.

### `status=CONFLICTED`

The requested semantics, source timing, or as-of boundary conflicts with the data. Stop. Do not run downstream analysis.

### `status=ERROR`

The dataset, file, request, or parsing path is invalid. Inspect the API response and service logs.

```bash
sudo journalctl -u fmdata -n 300 --no-pager
```

---

# Phase 8 — Verify idempotency

Repeat the exact request without refreshing the underlying cache:

```bash
curl --fail --silent --show-error \
  -X POST \
  -H "X-Research-Key: $FMDATA_RESEARCH_KEY" \
  -H 'Content-Type: application/json' \
  --data @/tmp/fmdata-snapshot-request.json \
  "$FMDATA_URL/research/snapshots" \
  > /tmp/fmdata-snapshot-response-repeat.json

python - <<'PY'
import json
from pathlib import Path

a = json.loads(Path('/tmp/fmdata-snapshot-response.json').read_text())
b = json.loads(Path('/tmp/fmdata-snapshot-response-repeat.json').read_text())

for field in (
    'snapshot_id',
    'request_sha256',
    'content_sha256',
    'raw_content_sha256',
    'row_count',
    'schema_fingerprint',
):
    assert a.get(field) == b.get(field), field
print('idempotency verified')
PY
```

A changed snapshot ID is acceptable only when the source bytes or bounded request changed, and the reason is recorded.

---

# Phase 9 — Run the multi-agent pipeline preflight

On the machine that will run the external controller:

```bash
cd "$HOME"

if [ ! -d multi-agent-pipeline/.git ]; then
  git clone https://github.com/kevinsutianxing/multi-agent-pipeline.git
fi

cd multi-agent-pipeline
git fetch origin
git switch feat/codex-financial-research-system
git pull --ff-only origin feat/codex-financial-research-system
```

Set the fmdata connection without printing the key:

```bash
export FMDATA_URL="http://127.0.0.1:1934"
export FMDATA_RESEARCH_KEY="$RESEARCH_KEY"
```

When the pipeline runs on a different host, do not expose fmdata publicly. Use an authenticated private network, SSH tunnel, or equivalent restricted transport, and set `FMDATA_URL` to that private endpoint.

Create a run directory:

```bash
RUN_ID="fmdata-live-$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="runs/$RUN_ID"
mkdir -p "$RUN_DIR/tasks/fmdata-live"
export RUN_ID RUN_DIR
```

Create an infrastructure-only requirement. This verifies service connectivity but does not require the selected Recipe to be research-ready:

```bash
DATASET="$DATASET" python - <<'PY' > "$RUN_DIR/fmdata_requirements.smoke.json"
import json
import os
print(json.dumps({
    'datasets': [{
        'dataset': os.environ['DATASET'],
        'research_ready': False,
        'expected_semantics': {},
    }]
}, ensure_ascii=False, indent=2))
PY

python scripts/fmdata_preflight.py \
  --base-url "$FMDATA_URL" \
  --requirements "$RUN_DIR/fmdata_requirements.smoke.json" \
  --report "$RUN_DIR/fmdata_preflight_report.json"
```

Expected: `status=PASS` for service/auth/catalog readiness.

For a research acceptance run, replace `research_ready:false` with `true` and include independently verified expected semantics such as frequency, timezone, currency, adjustment, revision policy, accounting basis, or vintage policy as applicable.

---

# Phase 10 — Materialize the snapshot into a research run

Copy the exact bounded request used above:

```bash
cp /tmp/fmdata-snapshot-request.json \
  "$RUN_DIR/tasks/fmdata-live/fmdata_request.json"
```

Run the external materializer:

```bash
python adapters/fmdata_client.py snapshot \
  --base-url "$FMDATA_URL" \
  --run-dir "$RUN_DIR" \
  --task-id fmdata-live \
  --request-file "$RUN_DIR/tasks/fmdata-live/fmdata_request.json" \
  | tee "$RUN_DIR/fmdata_materialization.stdout.json"
```

Expected behavior:

- `OK` snapshot: adapter downloads and verifies both objects, checks CSV quality and semantics, and writes source/dataset manifest segments.
- `PARTIAL`, `CONFLICTED`, or `ERROR`: adapter exits nonzero and does not produce a validated dataset manifest segment.

A nonzero exit caused by `PARTIAL` is an expected safety result. It proves the pipeline does not promote semantically incomplete data.

Inspect generated artifacts after an `OK` materialization:

```bash
find "$RUN_DIR" -maxdepth 4 -type f -print | sort

python -m json.tool \
  "$RUN_DIR/source_manifest.fmdata.fmdata-live.json"

python -m json.tool \
  "$RUN_DIR/dataset_manifest.fmdata.fmdata-live.json"
```

---

# Phase 11 — Merge manifests and run the acquisition gate

This phase is valid only when materialization succeeded and produced both manifest segments.

```bash
python scripts/merge_manifests.py --run-dir "$RUN_DIR"

python scripts/validate_evidence.py \
  --run-dir "$RUN_DIR" \
  --as-of "$AS_OF" \
  --stage acquisition \
  --report "$RUN_DIR/acquisition_gate_report.json"
```

Inspect the gate report:

```bash
python -m json.tool "$RUN_DIR/acquisition_gate_report.json"
```

Required result:

```json
{
  "status": "PASS",
  "summary": {
    "critical_count": 0
  }
}
```

Also verify:

```bash
python - <<'PY'
import json
import os
from pathlib import Path

run_dir = Path(os.environ['RUN_DIR'])
report = json.loads((run_dir / 'acquisition_gate_report.json').read_text())
assert report['status'] == 'PASS'
assert report['summary']['critical_count'] == 0
assert report['summary']['evidence_records'] >= 1
assert report['summary']['dataset_records'] >= 1
print('acquisition gate PASS')
PY
```

Do not begin quantitative or industry analysis before this passes.

---

# Phase 12 — Negative-control tests

These tests confirm the deployment blocks unsafe conditions.

## 12.1 Wrong key

```bash
STATUS_CODE="$(curl --silent --output /tmp/fmdata-wrong-key.json --write-out '%{http_code}' \
  -H 'X-Research-Key: definitely-wrong' \
  "$FMDATA_URL/research/catalog")"

test "$STATUS_CODE" = "403"
```

## 12.2 Tampered downloaded file

Run only inside the test run directory, never against the fmdata immutable store:

```bash
NORMALIZED_FILE="$(find "$RUN_DIR/raw/fmdata" -maxdepth 1 -type f -name '*.csv' | head -n 1)"
test -n "$NORMALIZED_FILE"

cp "$NORMALIZED_FILE" "$NORMALIZED_FILE.before-tamper"
printf '\nTAMPERED\n' >> "$NORMALIZED_FILE"

if python scripts/validate_evidence.py \
  --run-dir "$RUN_DIR" \
  --as-of "$AS_OF" \
  --stage acquisition \
  --report "$RUN_DIR/acquisition_gate_tamper_report.json"; then
  echo 'ERROR: tampered file incorrectly passed' >&2
  exit 1
else
  echo 'tampered snapshot correctly blocked'
fi

mv "$NORMALIZED_FILE.before-tamper" "$NORMALIZED_FILE"
```

The tamper report must include `HASH_MISMATCH`.

## 12.3 Missing semantic metadata

Select or configure a known incomplete Recipe and verify:

- fmdata returns `status=PARTIAL`;
- `validation_status` remains `PENDING`;
- `adapters/fmdata_client.py snapshot` exits nonzero;
- no validated dataset segment is produced.

Do not weaken the adapter or Recipe checks to make this test pass.

---

# Phase 13 — Acceptance criteria

## Infrastructure acceptance

All must be true:

- branch and commit are recorded;
- unit and contract tests pass;
- service runs as an unprivileged user from the project virtualenv;
- service listens only on loopback or a private restricted endpoint;
- unauthenticated/wrong-key catalogue requests are rejected;
- authenticated health and catalogue calls succeed;
- immutable raw and normalized objects are created;
- both downloaded hashes independently match the manifest;
- repeated identical requests return the same snapshot identity;
- snapshot storage is protected from direct Agent writes.

## Research-data acceptance

All must additionally be true for at least one real dataset:

- Recipe semantics were reviewed against primary provider documentation;
- snapshot returns `status=OK`, not merely `PARTIAL`;
- observation start/end are populated and within the run as-of boundary;
- availability/publication rule is explicit and defensible;
- unit, timezone, frequency, revision policy, and applicable currency/adjustment/accounting/vintage fields are explicit;
- external materializer succeeds;
- source and dataset manifest segments are produced;
- deterministic acquisition gate returns `PASS` with zero critical findings;
- tamper negative control fails with `HASH_MISMATCH`.

## Production release acceptance

Before declaring the overall system production-ready, also complete:

- one real market dataset validation;
- one real fundamental dataset validation with announcement timing and period basis;
- one real macro dataset validation with vintage/revision handling;
- historical/delisted entity and index-membership tests;
- provider outage, pagination, rate-limit, and empty-response tests;
- licensing/storage/redistribution review;
- one quantitative and one industry-research run end to end;
- independent Claude Code adversarial review outside the primary execution context.

---

# Rollback

When the new service must be rolled back:

```bash
sudo systemctl stop fmdata
```

Restore the previous unit from the backup directory created in Phase 0, then:

```bash
sudo cp "$BACKUP_DIR/fmdata.service" /etc/systemd/system/fmdata.service
sudo systemctl daemon-reload
sudo systemctl restart fmdata
sudo systemctl status fmdata --no-pager
```

Restore `.env` only when the previous version is known to be required:

```bash
cp "$BACKUP_DIR/.env" /home/ubuntu/fmdata/.env
chmod 600 /home/ubuntu/fmdata/.env
sudo systemctl restart fmdata
```

Do not delete `/var/lib/fmdata/snapshots` during rollback. Preserved snapshot artifacts are part of the audit trail.

---

# Evidence bundle to retain

Keep these files for deployment review:

```text
/tmp/fmdata-deployed-commit.txt
/tmp/fmdata-pip-freeze.txt
/tmp/fmdata-catalog.json
/tmp/fmdata-production-catalog.json
/tmp/fmdata-snapshot-request.json
/tmp/fmdata-snapshot-response.json
/tmp/fmdata-snapshot-manifest.json
runs/<run_id>/fmdata_preflight_report.json
runs/<run_id>/fmdata/<task_id>/request.json
runs/<run_id>/fmdata/<task_id>/response.json
runs/<run_id>/fmdata/<task_id>/manifest.json
runs/<run_id>/fmdata/<task_id>/materialization.json
runs/<run_id>/source_manifest.json
runs/<run_id>/dataset_manifest.json
runs/<run_id>/acquisition_gate_report.json
runs/<run_id>/acquisition_gate_tamper_report.json
```

Never include `.env`, provider tokens, research keys, cookies, or Authorization headers in the evidence bundle.