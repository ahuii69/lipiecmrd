# Archive — legacy routers removed from the runtime tree

Created during the 06.07 repair sprint (`06.07naprawa.md`, P0 security fix).

Files here are **not** part of the `aihub` Python package. They are not on any Python import
path used by the running application, not referenced by `aihub/main.py`, and not covered by any
"import smoke" test that pretends they are safe because they're merely "unmounted". This
directory exists purely so the historical code is not silently deleted, while making it
structurally impossible for it to be mounted by accident (no `include_router`, no package
`__init__.py`, outside `aihub/`).

## `ai_compat_router.py`

- **Origin:** was `aihub/api/ai_compat_router.py`, marked `LEGACY / UNMOUNTED`.
- **Why archived (not just left unmounted):** exposed `POST /python/run`, which executed
  arbitrary attacker-supplied Python source via `subprocess.run([python, "-c", code])` — remote
  code execution if ever mounted on `aihub.main:app`. Also shelled out to `docker info` / `docker
  ps`. A previous test (`tests/test_aihub_api_legacy_import_smoke.py`) imported this module solely
  to prove it *could* be imported, which is not a security control.
- **Decision:** archive, do not re-mount, do not "hgarden" into a supported feature. If arbitrary
  code execution as a product feature is ever required, it needs a new, explicitly designed,
  reviewed, sandboxed implementation — not this file.
- **Do not** move this back into `aihub/` or reference it from `aihub/main.py` without a
  deliberate, reviewed security decision recorded in `SECURITY.md`/`VAULT.md`.

## `ops_router.py`

- **Origin:** was `aihub/api/ops_router.py` (`/system/ops/*`), marked `LEGACY / UNMOUNTED`.
- **Why archived:** hardcoded `ROOT_DIR = Path("/root/ai-hub")`, which does not match this
  deployment's actual path (`/home/ubuntu/mrd`); `POST /system/ops/rollback` extracts a tarball
  and then shells out to `systemctl restart aihub` via `subprocess.Popen(["bash", "-lc", ...])`.
  It did have an explicit opt-in flag (`AIHUB_ALLOW_OPS`) and path-traversal guards on tar
  extraction — better hygiene than `ai_compat_router.py` — but a stale hardcoded host path and an
  assumed systemd unit name make it unsafe to trust as-is on this or any other host without a
  rewrite.
- **Decision:** archive, do not re-mount as-is. If snapshot/rollback tooling is needed, rebuild it
  using `aihub.config.DATA_DIR`/`FS_ROOT` (no hardcoded paths) and the project's own
  `start.sh`/`stop.sh` process model instead of a guessed systemd unit name.

## `admin_router.py`

- **Origin:** was `aihub/api/admin_router.py` (`/admin/events/*`), marked `LEGACY / UNMOUNTED`.
- **Why archived:** same `/admin` prefix as the canonical, mounted `aihub/admin_api.py`
  (`GET /admin/ping`) — a namespace collision. `GET /admin/events/body?id=...` returned
  **unredacted** base64-encoded HTTP request/response bodies with no secret/PII redaction, and
  depended on `aihub/middleware/recorder.py`, which is not registered on the app either.
- **Decision:** `aihub/admin_api.py` is the single canonical admin router. This module is
  archived, not merged, because merging it as-is would reintroduce the unredacted body leak;
  a future admin "recent requests" feature would need to be redesigned with redaction from the
  start, not adapted from this file.

## Restoring reference-only access

The file remains readable here for anyone who needs to see what the old compat surface looked
like. It is intentionally excluded from `python -m compileall aihub tests scripts` (it lives
outside those trees) and from all import-smoke and pytest collection.
