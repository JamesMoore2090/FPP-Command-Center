export interface Reading { label: string; value: number }

export interface HostStatus {
  online: boolean;
  error: string | null;
  polled_at: string | null;
  hostname: string | null;
  uuid: string | null;
  version: string | null;
  mode: string | null;
  model: string | null;
  status: string | null;
  current_playlist: string | null;
  current_sequence: string | null;
  seconds_played: number | null;
  seconds_remaining: number | null;
  fppd: string | null;
  volume: number | null;
  uptime_s: number | null;
  cpu_pct: number | null;
  mem_pct: number | null;
  disk_free: number | null;
  disk_total: number | null;
  temps: Reading[];
  voltages: Reading[];
  extra: Record<string, unknown>;
}

export type Sync = "in_sync" | "drift" | "out_of_sync" | "idle" | "unknown" | null;

export interface HostView {
  id: number;
  name: string;
  address: string;
  kind: string;
  hostname: string | null;
  uuid: string | null;
  role: string | null;
  model: string | null;
  source: string;
  enabled: boolean;
  notes: string | null;
  last_seen: string | null;
  status: HostStatus;
  sync: Sync;
  disk_pct: number | null;
  temp_max: number | null;
}

export interface Alert {
  severity: "critical" | "warning" | "info";
  code: string;
  message: string;
  host_id: number | null;
  host_name: string | null;
}

export interface Snapshot {
  ts: string;
  banner: {
    player_id: number | null;
    player_name: string | null;
    player_online: boolean;
    status: string | null;
    playlist: string | null;
    sequence: string | null;
    seconds_played: number | null;
    seconds_remaining: number | null;
  };
  summary: { total: number; online: number; offline: number; playing: number; version_majority: string | null };
  alerts: Alert[];
  hosts: HostView[];
}

export interface Found {
  address: string; kind: string; hostname: string | null; mode: string | null;
  model: string | null; version: string | null; via: string; action: string;
}

export interface DiscoveryResult {
  started_at: string; finished_at: string; found: Found[]; added: number; moved: number; errors: string[];
}

export interface VirtualString {
  description: string | null; start_channel: number | null; pixels: number; group: number | null;
  reverse: boolean | null; brightness: number | null; color_order: string | null; enabled: boolean;
}
export interface Port {
  number: number; protocol: string | null; strings: VirtualString[];
  current_ma: number | null; fuse_ok: boolean | null; enabled: boolean | null; pixels: number;
}
export interface OutputGroup {
  name: string; enabled: boolean; start_channel: number | null; channel_count: number | null; ports: Port[];
}
export interface ActionSpec { id: string; label: string; danger: boolean; confirm: string | null }
export interface HostDetail {
  info: Record<string, string | number | boolean | null>;
  outputs: OutputGroup[];
  universes: { universe: number; start_channel: number | null; size: number | null; protocol: string | null; packets: number | null }[];
  interfaces: { name: string; state: string; mac: string; addresses: string | null }[];
  warnings: string[];
  errors: string[];
  has_logs: boolean;
  actions: ActionSpec[];
}
export interface Endpoint { method: string; path: string; handler: string }

export interface Backup {
  id: number; host_id: number | null; host_name: string; kind: string; created_at: string;
  trigger: "scheduled" | "manual" | "pre-restore" | "pre-change"; status: "ok" | "failed" | "skipped"; error: string | null;
  size: number | null; changed: boolean | null; version: string | null; platform: string | null;
  pinned: boolean; note: string | null;
}
export interface HostBackupStatus {
  host_id: number; host_name: string; kind: string; enabled: boolean; running: boolean;
  last_status: string | null; last_error: string | null; last_at: string | null;
  last_ok_at: string | null; last_ok_id: number | null; count: number;
}
export interface BackupStatus {
  enabled: boolean; schedule: string; timezone: string; next_run: string | null;
  last_run: { trigger: string; started_at: string; finished_at: string; ok: number; failed: number; skipped: number } | null;
  retention: { daily: number; weekly: number; monthly: number };
  hosts: HostBackupStatus[];
}
export interface DiffChange {
  area: string; path: string; type: "added" | "removed" | "changed";
  a?: string; b?: string; lines?: string[]; secret?: boolean;
}
export interface DiffResult { a: Backup; b: Backup; changes: DiffChange[]; unchanged: number }
export interface RestoreResult {
  restored: boolean; areas: { area: string; ok: boolean }[]; errors: string[];
  pre_restore_backup_id: number; reboot_recommended: boolean;
}

export interface ScheduleEntryRaw {
  enabled: number; sequence?: number; playlist?: string; command?: string; args?: string[];
  multisyncCommand?: boolean; multisyncHosts?: string;
  day: number; startTime: string; startTimeOffset?: number; endTime: string; endTimeOffset?: number;
  repeat: number; startDate: string; endDate: string; stopType: number;
  [key: string]: unknown;
}
export interface ScheduleEntry { index: number; kind: "playlist" | "sequence" | "command"; day_str: string; stop_str: string; warning: string | null; raw: ScheduleEntryRaw }
export interface UpcomingItem { entry: number; start: number; end: number; start_str: string; end_str: string; command: string; args: string[] }
export interface ScheduleData { host_id: number; host: string; enabled: boolean; entries: ScheduleEntry[]; upcoming: UpcomingItem[] }
export interface CommandArg {
  name: string; description?: string; type: string; optional?: boolean; default?: string;
  min?: number; max?: number; contents?: string[]; contentListUrl?: string; allowBlanks?: boolean;
}
export interface FppCommand { name: string; category?: string; description?: string; args?: CommandArg[] }
export interface HostResult { host_id: number; host: string; ok: boolean; message: string }
export interface PlaylistDetail {
  name: string; description: string | null; repeat: boolean; random: boolean; total_duration: number | null;
  items: { section: string; type: string; enabled: boolean; name: string; duration: number | null }[];
}

// ---- phase 5: jobs, content, fleet ops
export type StepStatus = "pending" | "running" | "ok" | "failed" | "skipped";
export interface JobStep {
  id: number; label: string; host_id: number | null; host: string | null; status: StepStatus;
  message: string | null; progress: number | null; started_at: string | null; finished_at: string | null; log: string[];
}
export interface JobSummary {
  id: string; kind: string; title: string; status: "running" | "ok" | "failed" | "partial" | "cancelled";
  created_at: string; finished_at: string | null; cancel_requested: boolean;
  counts: Record<StepStatus, number>; total: number;
}
export interface Job extends Omit<JobSummary, "counts" | "total"> { steps: JobStep[] }

export interface ContentProblem {
  severity: "critical" | "warning"; host_id: number; host: string; kind: string; file: string; playlists: string[]; message: string;
}
export interface ContentRemote {
  id: number; name: string; online: boolean; error: string | null; uses_sequences: boolean; count: number;
  missing: string[]; extra: string[]; files: Record<string, number | null>;
}
export interface ContentFile { name: string; size: number | null; mtime: string | null; playlists: string[] }
export interface ContentOverview {
  player: { id: number; name: string }; scheduled_playlists: string[];
  sequences: ContentFile[]; media: ContentFile[]; remotes: ContentRemote[]; problems: ContentProblem[];
}

export interface OsImage { tag: string; name: string; filename: string; url: string; size: number | null; prerelease: boolean }
export interface VersionRow {
  id: number; name: string; address: string; kind: string; role: string | null; online: boolean; updatable: boolean;
  is_player: boolean; error?: string; platform?: string | null; variant?: string | null; mode?: string | null;
  version?: string | null; branch?: string | null; os_version?: string | null; os_release?: string | null;
  local_commit?: string | null; remote_commit?: string | null; update_available?: boolean; update_checked?: boolean;
  upgrade?: { version: string; target: string | null; major: boolean } | null; end_of_life?: boolean;
  activity?: { in_progress: boolean; op: string | null; stage: string | null; failed: boolean };
  plugin_updates?: string[]; os_update?: OsImage | null; os_images?: OsImage[]; os_error?: string;
}
export interface VersionsData {
  hosts: VersionRow[];
  summary: { versions: string[]; os_versions: string[]; updates_available: number; upgrades_available: number; in_progress: string[] };
  running_job: JobSummary | null;
}
export interface SettingsRow {
  key: string; area: string; name: string; differs: boolean; expected: boolean; secret: boolean;
  values: Record<string, string | null>;
}
export interface SettingsCompare {
  hosts: { id: number; name: string; backup_id: number | null; taken_at: string | null; version: string | null; error?: string }[];
  rows: SettingsRow[];
}
