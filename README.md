# FPP Command Center

A self-hosted web app to discover, monitor, control and back up every controller in the light show:
FPP players/remotes plus non-FPP controllers such as the Falcon F48. It replaces FPPMon. The full spec is in
[`fpp-command-center-requirements.md`](fpp-command-center-requirements.md).

**Status: Phase 5.** Discovery, the live dashboard, controller management, the controller detail page (status, outputs/ports,
logs, actions), the generic FPP proxy, the raw API console, configuration backups (nightly + on demand, versioned,
diff, restore), the Show page (playback, schedule editor, FPP commands, volume, test mode), the app's own REST API
for Home Assistant and scripts, the Content page (missing-file check, copy sequences from the player to remotes) and
the Fleet page (versions, FPP and OS updates, restart/reboot, settings compare) are done.

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
* `backend/app/backups/`: configuration backups. FPP backups come from FPP's own backup page (`backup.php`), so they
  are in FPP's native format, and restores post the file back to the same page. A Falcon backup is an export of its XML
  config files plus network settings, kept for reference and diffing; it is restored by hand. Files are gzipped
  under `/var/lib/fppcc/backups`, mode 0600 (they contain WiFi keys and passwords). Nightly at `FPPCC_BACKUP_TIME`
  in `FPPCC_TIMEZONE`. Retention: everything from the last 48 h, then the newest backup per day (14), week (8) and
  month (12); pinned backups are kept forever. Every restore first takes a pinned backup of the target.
* `backend/app/content.py`: compares each remote's sequences with the player's and checks every playlist's files.
  Sizes are not compared, because xLights' FPP Connect gives each remote a trimmed copy. Remotes with no sequences are
  assumed to be fed over the network. A copy streams the player's file through the VM into the remote's upload folder,
  checks the byte count, then has FPP move it into place, so a failed copy never leaves a truncated sequence.
* `backend/app/updates.py`: FPP update (`manualUpdate.php`), version upgrade (`upgradefpp.php`, then reboot) and OS
  upgrade (`upgradeOS.php`), the same pages FPP's own UI uses. Success is read from their streamed output. Updates
  run one controller at a time, remotes first and the player last; each controller is backed up first, and the run stops
  at the first failure. The show must be idle unless `force` is set.
* `backend/app/jobs.py`: long operations run as in-memory background jobs with per-controller status, progress and
  logs. A service restart forgets the job list, but not what the jobs did.
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
| GET | `/api/backups/status` | schedule, retention, last good backup per controller |
| GET | `/api/backups?host_id=` | backup history |
| POST | `/api/backups/run` | back up now: `{}` for the fleet or `{"host_ids": [1, 2]}` |
| GET | `/api/backups/{id}/download` | the backup file (FPP's own restore page accepts it as-is) |
| GET | `/api/backups/diff?a=&b=` | differences between two backups (same or different controllers); secrets masked |
| POST | `/api/backups/{id}/restore` | `{"confirm": true, "target_host_id": 3, "area": "all", "keep_network": true, "keep_mode": true}` |
| PATCH/DELETE | `/api/backups/{id}` | pin (`{"pinned": true}`), add a note, delete |
| GET | `/api/content` | player's sequences and media, what each remote has, and problems (files playlists need but can't find) |
| POST | `/api/content/push` | copy sequences to remotes: `{"confirm": true}` copies whatever each remote is missing; optional `host_ids`, `files`, `overwrite`, `force`. Returns a job |
| POST | `/api/content/delete` | `{"host_id": 4, "files": ["Old.fseq"], "confirm": true}` (remotes only) |
| GET | `/api/fleet/versions?os=true` | FPP version, branch, commit, OS image and available updates per controller; `os=true` also finds the matching OS image |
| POST | `/api/fleet/update` | `{"op": "update" \| "upgrade" \| "os", "host_ids": [...], "version": "10.4", "os_image": "Pi-10.3_2026-10b.fppos", "confirm": true}`. Returns a job |
| GET | `/api/fleet/settings?host_ids=` | settings that differ between controllers (from their latest backups) plus installed plugins; secrets masked |
| GET | `/api/jobs`, `/api/jobs/{id}` | background jobs with per-controller status, progress and log |
| POST | `/api/jobs/{id}/cancel` | skip the steps that haven't started (a controller mid-update is left to finish) |

### Show and fleet API (for Home Assistant and scripts)

Everything under `/api/show` acts on the player unless a `host_id` is given.

| Method | Path | |
|---|---|---|
| GET | `/api/show/status` | flat status: `playing`, `status`, `playlist`, `sequence`, `seconds_remaining`, controllers online/offline, `out_of_sync`, alert counts |
| POST | `/api/show/start` | `{"name": "Christmas_2026", "repeat": true}` (a playlist, or a sequence such as `"Song.fseq"`) |
| POST | `/api/show/stop` | `{"mode": "graceful"}` (or `"now"`, `"after_loop"`) |
| POST | `/api/show/pause`, `/resume`, `/next`, `/prev` | transport |
| GET/POST | `/api/show/volume` | `{"volume": 70}` |
| GET | `/api/show/playlists`, `/api/show/playlists/{name}` | playlists, playable items, sequences; one playlist's items |
| GET | `/api/show/schedule` | entries (with warnings for missing or empty playlists) and upcoming items |
| PUT | `/api/show/schedule` | `{"entries": [...], "confirm": true}`. Takes a backup of the player first |
| GET | `/api/show/commands` | the player's FPP commands (with argument descriptions) and command presets |
| POST | `/api/show/command` | `{"command": "All Lights Off", "args": [], "multisync": false}`: any FPP command |
| GET | `/api/show/test`, `/api/show/test/patterns[/{pattern}]` | test-mode state per controller; available patterns |
| POST | `/api/show/test/start` | `{"pattern": "RGB Chase", "interval_ms": 1000, "host_ids": null}`: blank args use each controller's own channel range |
| POST | `/api/show/test/stop` | `{"host_ids": null}` |
| GET | `/api/fleet/status` | one compact row per controller |
| POST | `/api/fleet/actions/{action}` | `restart_fppd`, `reboot`, … on every online controller (or `host_ids`); needs `"confirm": true` |

Home Assistant example (`configuration.yaml`):

```yaml
rest_command:
  show_start:
    url: "http://<vm>/api/show/start"
    method: post
    content_type: application/json
    payload: '{"name": "{{ playlist }}", "repeat": true}'
  show_stop:
    url: "http://<vm>/api/show/stop"
    method: post
    content_type: application/json
    payload: '{"mode": "graceful"}'
sensor:
  - platform: rest
    name: Light show
    resource: "http://<vm>/api/show/status"
    value_template: "{{ value_json.status }}"
    json_attributes: [playlist, sequence, seconds_remaining, controllers_online, controllers_offline, alerts]
    scan_interval: 15
```

Native Home Assistant devices and alerts over MQTT come in phase 6.

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
