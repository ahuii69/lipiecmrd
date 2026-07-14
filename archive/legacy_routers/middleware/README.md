# Legacy middleware (archived 19.07)

`FirewallMiddleware` and `EventRecorderMiddleware` were never registered on
`aihub.main:app`. Archived so they cannot be silently remounted with the old
broad ALWAYS_ALLOW prefix list.
