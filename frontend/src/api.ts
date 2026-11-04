import { useEffect, useRef, useState } from "react";
import type { Snapshot } from "./types";

export class ApiError extends Error {}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  // Only send Content-Type with a body: FPP's PHP API 404s on GETs that carry Content-Type: application/json.
  const headers = init?.body != null ? { "Content-Type": "application/json", ...(init?.headers || {}) } : init?.headers;
  const r = await fetch(path, { ...init, headers });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch { /* not json */ }
    throw new ApiError(msg);
  }
  return (r.status === 204 ? undefined : await r.json()) as T;
}

/** Live fleet snapshot over WebSocket, with reconnect. */
export function useSnapshot() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const retry = useRef(1000);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let ping: number | undefined;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/ws`);
      ws.onopen = () => {
        setConnected(true);
        retry.current = 1000;
        ping = window.setInterval(() => ws?.readyState === 1 && ws.send("ping"), 25000);
      };
      ws.onmessage = (e) => setSnap(JSON.parse(e.data));
      ws.onclose = () => {
        setConnected(false);
        window.clearInterval(ping);
        if (!closed) {
          timer = window.setTimeout(connect, retry.current);
          retry.current = Math.min(retry.current * 2, 15000);
        }
      };
    };
    api<Snapshot>("/api/snapshot").then(setSnap).catch(() => {});
    connect();
    return () => { closed = true; window.clearTimeout(timer); window.clearInterval(ping); ws?.close(); };
  }, []);

  return { snap, connected };
}
