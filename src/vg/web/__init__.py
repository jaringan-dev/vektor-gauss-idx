"""Local superadmin dashboard (RENCANA-UI-UX §3). Read-only web layer over vg.

Kept dependency-light: importing this package must NOT require the `web` extra.
`status` is pure stdlib; `app` (which imports FastAPI/Jinja2) is only imported
when actually serving, via `vg serve`.
"""
