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
