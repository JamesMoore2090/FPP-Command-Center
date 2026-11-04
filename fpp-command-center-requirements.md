# FPP Command Center — Requirements (v1.1, 2026-11-02)

Self-hosted replacement/expansion of FPPMon: one web page to discover, monitor, control and back up every
controller in the show. Hosted on an Ubuntu VM, reached over Tailscale.

## Environment
- VM: Ubuntu (24.04 LTS assumed), 4 vCPU, 4 GB RAM, 100 GB disk
- Network: VM reaches both 10.55.x.x (F48) and 10.100.100.x (Kulp/PiCap FPPs) — confirmed by ping
- Access: Tailscale installed on VM by James; **no login** (tailnet membership = access); single user
- Code: GitHub repo
- Runs **natively** (no Docker): systemd services, Python venv, PostgreSQL from apt, Nginx serving the React build
- Dev access: Claude Code installed on the VM (recommended) or James runs scripts from the repo

## Fleet
| Name | Role | Hardware | IP |
|---|---|---|---|
| FPPMain | **Player** | — | TBD |
| Mega Tree | Remote | Kulp K16 | 10.100.100.55 |
| K8-Pi 1 | Remote | Kulp K8-Pi | 10.100.100.51 |
| K8-Pi 2 | Remote | Kulp K8-Pi | 10.100.100.52 |
| Pi Hat 3 | Remote | Falcon PiCap V2 | 10.100.100.53 |
| Pi Hat 4 | Remote | Falcon PiCap V2 | 10.100.100.54 |
| Yard Right | Remote | Kulp K16-Pro (new) | 10.100.100.56 |
| F48 | Non-FPP | Falcon F48 | 10.55.1.102 |

All FPP instances on **FPP 10.3**. FPPMon plugin not installed (not needed).

## Decisions
1. **Falcon F48 is supported** alongside FPP (status, temps, voltages, ports) through a separate Falcon driver.
   Driver design allows WLED/ESPixelStick later.
2. FPPMain = player; everything else = remote. The dashboard shows sync status relative to the player.
3. **Discovery:** automatic via FPP MultiSync (`/api/fppd/multiSyncSystems` on FPPMain) plus a subnet probe;
   manual add/edit/remove for anything not discovered.
4. **API coverage = option A:** every FPP endpoint reachable through a generic proxy (`/api/hosts/{id}/fpp/...`)
   and a raw API console; dedicated screens for the common operations.
5. **The app has its own REST API** (fleet actions such as "start playlist on player", "restart fppd on all", status)
   for Home Assistant and scripts.
6. **Confirmation dialogs** for destructive or fleet-wide actions (reboot, shutdown, update, restore, stop show).
7. **Dashboard priority:** online/offline, FPP version, CPU, temperatures. Then: playing/idle, uptime, disk, sync.
8. **Home Assistant via MQTT** (existing Mosquitto add-on), using HA MQTT discovery: each controller appears as an
   HA device with sensors (online, version, CPU, temps, status, current sequence) and buttons (restart fppd,
   reboot). Alerts (offline, high temp, high CPU, low disk, backup failed, version mismatch) published as HA
   events/binary sensors so HA automations do the notifying.
9. **History retention: 2 years.** Raw samples kept 7 days, 1-minute rollups 90 days, 15-minute rollups 2 years.
10. Stack: Python (FastAPI) backend + collector, **PostgreSQL**, React (Vite) frontend, WebSocket live updates.
11. **Backups: config only** (no sequences/media). The VM is the backup target for all FPP instances and the F48.

## Backup requirements
- Nightly scheduled + on-demand per controller or whole fleet
- FPP: settings/config backup (JSON), playlists, schedule, channel outputs/inputs, network config,
  plugin list
- F48: config export
- Versioned with retention (e.g., 14 daily, 8 weekly, 12 monthly), alert on failure
- Restore: to the same controller or to a replacement SD card/new box (with confirmation)
- Diff two backups / two controllers' settings

## Pages
1. Dashboard (home) — controller cards, show banner, alerts
2. Controller detail — status, outputs/ports, sensors, logs, actions, raw API console
3. Playback & scheduler — playlists, schedule, commands, volume, test mode
4. Content — sequences/media, push from player to remotes, missing-file check
5. Fleet ops — versions, OS updates, restart/reboot, settings compare
6. Backups — history, run now, restore, retention
7. History & alerts — charts, alert rules, MQTT/HA settings

## Phases
1. VM setup (native services, Postgres, Nginx, Tailscale Serve HTTPS), repo scaffold, discovery + read-only dashboard
2. Controller detail, generic proxy, raw API console, Falcon driver
3. Backups (scheduled, versioned, restore)
4. Playback/scheduler/commands, own REST API
5. Content sync, fleet ops (updates)
6. History rollups, alerts, Home Assistant MQTT integration

## Open questions
- FPPMain's IP
- GitHub repo name/owner
- MQTT broker credentials (create a dedicated HA user for the app)
- Models.xlsx IP Addresses tab still shows the retired 172.16.5.x scheme
