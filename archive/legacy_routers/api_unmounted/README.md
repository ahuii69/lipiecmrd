# Unmounted `aihub/api/*` routers (archived 19.07)

Moved out of the `aihub` package during agent consolidation so they cannot be
accidentally `include_router`'d onto `aihub.main:app`.

Canonical HTTP surface remains `aihub/canonical_http_surface.py`.
FS / memory / psyche production APIs live on `aihub.main` and `*_api.py` routers.
