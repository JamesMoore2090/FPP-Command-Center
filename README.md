# FPP Command Center

A self-hosted web app to discover, monitor, control and back up every controller in the light show:
FPP players/remotes plus non-FPP controllers such as the Falcon F48. It replaces FPPMon. The full spec is in
[`fpp-command-center-requirements.md`](fpp-command-center-requirements.md).

**Status: Phase 2.** Discovery, the live dashboard, controller management, the controller detail page (status, outputs/ports,
logs, actions), the generic FPP proxy and the raw API console are done.

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

## API

| Method | Path | |
|---|---|---|
| GET | `/api/health` | liveness |
| GET | `/api/snapshot` | banner, summary, alerts, enabled hosts with live status |
| WS | `/api/ws` | pushes a snapshot after every poll cycle |
| GET/POST | `/api/hosts` | list (including disabled) / add (`address`, optional `name`, `kind`; auto-detected) |
| GET/PATCH/DELETE | `/api/hosts/{id}` | view / edit (`name`, `address`, `kind`, `notes`, `enabled`) / remove |
| POST | `/api/discovery/run` | run discovery now (~30 s) |
| GET | `/api/discovery/last` | last discovery result |
| GET | `/api/hosts/{id}/detail` | driver detail: system info, outputs/ports, network, warnings, available actions |
| POST | `/api/hosts/{id}/actions/{action}` | FPP: `restart_fppd`, `stop_fppd`, `start_fppd`, `reboot`, `shutdown` |
| any | `/api/hosts/{id}/fpp/{path}` | proxy to `http://<controller>/api/{path}` (FPP only; method, query and body forwarded, streamed) |
| any | `/api/hosts/{id}/http/{path}` | proxy to `http://<controller>/{path}` (any controller, e.g. Falcon `status.xml`) |
| GET | `/api/fpp/endpoints` | FPP 10.3 route catalog (API console autocomplete) |

Examples: `curl https://<vm>/api/hosts/3/fpp/file/logs/fppd.log?tail=100`,
`curl -X POST https://<vm>/api/hosts/1/actions/restart_fppd`. The UI asks for confirmation before destructive
actions. The API itself does not, so scripts and Home Assistant can call it directly.

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
cd backend && python3 -m venv .venv-dev && .venv-dev/bin/pip install -e '.[dev]'   # .venv is the production venv
.venv-dev/bin/pytest                                   # tests use SQLite + recorded device payloads
.venv-dev/bin/uvicorn app.main:app --reload --port 8001   # SQLite dev DB by default (fppcc-dev.db)
cd ../frontend && npm install && FPPCC_API=http://127.0.0.1:8001 npm run dev   # proxies /api (default :8000)
```

Schema changes: edit `app/models.py`, then run
`.venv-dev/bin/alembic revision --autogenerate -m "..."` with `FPPCC_DATABASE_URL` pointing at a migrated DB.
