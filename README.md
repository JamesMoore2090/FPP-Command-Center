# FPP Command Center

A self-hosted web app to discover, monitor, control and back up every controller in the light show:
FPP players/remotes plus non-FPP controllers such as the Falcon F48. It replaces FPPMon. The full spec is in
[`fpp-command-center-requirements.md`](fpp-command-center-requirements.md).

**Status: Phase 1.** Discovery, live read-only dashboard, and controller management are done.

## Architecture

```
browser ──https──> Tailscale Serve ──> nginx :80 ──/──────> /var/www/fppcc (React build)
                                                └─/api/──> uvicorn :8000 (FastAPI)
                                                              ├─ collector (polls controllers every 5 s)
                                                              ├─ discovery (MultiSync + subnet probe, every 10 min)
                                                              └─ PostgreSQL (hosts, raw samples)
```

* `backend/app/drivers/`: one driver per controller kind (`fpp`, `falcon`). Each driver normalizes its
  device's status into `HostStatus`. To add WLED or ESPixelStick, add a driver and register it in `drivers/__init__.py`.
* `backend/app/collector.py`: runs inside the API process. It polls all enabled hosts, keeps live state in
  memory, writes a raw sample every 15 s (kept 7 days), and pushes a snapshot to WebSocket clients after each poll.
* `backend/app/fleet.py`: builds the snapshot. It works out the show banner, each remote's sync status
  relative to the player, and live alerts (offline, temperature, CPU, disk, fppd stopped, version mismatch).
* `backend/app/discovery.py`: gathers FPP MultiSync results from the player plus an HTTP probe of
  `FPPCC_DISCOVERY_SUBNETS`. It dedupes by UUID/address and prefers addresses in subnet order. It adds new devices
  and follows known ones that changed IP. Disabled hosts are left alone.

## API (phase 1)

| Method | Path | |
|---|---|---|
| GET | `/api/health` | liveness |
| GET | `/api/snapshot` | banner, summary, alerts, enabled hosts with live status |
| WS | `/api/ws` | pushes a snapshot after every poll cycle |
| GET/POST | `/api/hosts` | list (including disabled) / add (`address`, optional `name`, `kind`; auto-detected) |
| GET/PATCH/DELETE | `/api/hosts/{id}` | view / edit (`name`, `address`, `kind`, `notes`, `enabled`) / remove |
| POST | `/api/discovery/run` | run discovery now (~30 s) |
| GET | `/api/discovery/last` | last discovery result |

Interactive API docs: `/api/docs`.

## Install on the VM

```bash
sudo deploy/install.sh     # apt packages, Postgres DB, venv, frontend build, systemd, nginx, tailscale serve
deploy/update.sh           # later: pull, rebuild, restart (no full sudo needed)
journalctl -u fppcc -f     # logs
```

Configuration lives in `/etc/fppcc/fppcc.env` (template: `deploy/fppcc.env.example`).

## Development

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                                   # tests use SQLite + recorded device payloads
.venv/bin/uvicorn app.main:app --reload            # SQLite dev DB by default (fppcc-dev.db)
cd ../frontend && npm install && npm run dev       # http://localhost:5173, proxies /api to :8000
```

Schema changes: edit `app/models.py`, then run
`.venv/bin/alembic revision --autogenerate -m "..."` with `FPPCC_DATABASE_URL` pointing at a migrated DB.
