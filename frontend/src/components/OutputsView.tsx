import { useState } from "react";
import type { HostDetail, OutputGroup, Port, VirtualString } from "../types";

// Falcons mark an unused port as an unnamed 1-pixel string, so don't count those as used.
const isUsed = (p: Port) => p.pixels > 1 || (p.pixels === 1 && p.strings.some((v) => v.description));

const channelsPerPixel = (v: VirtualString) => (v.color_order && /^[RGBW]{3,4}$/.test(v.color_order) ? v.color_order.length : 3);

export default function OutputsView({ detail, kind }: { detail: HostDetail; kind: string }) {
  const [showEmpty, setShowEmpty] = useState(false);
  if (detail.outputs.length === 0) return <p className="muted">This controller reports no channel outputs.</p>;
  return (
    <>
      <label className="check"><input type="checkbox" checked={showEmpty} onChange={(e) => setShowEmpty(e.target.checked)} /> Show unused ports</label>
      {detail.outputs.map((g, i) => <Group key={i} g={g} showEmpty={showEmpty} kind={kind} />)}
      {kind === "falcon" && <p className="muted hint">Start channels are shown exactly as the Falcon reports them.</p>}
    </>
  );
}

function Group({ g, showEmpty, kind }: { g: OutputGroup; showEmpty: boolean; kind: string }) {
  const used = g.ports.filter(isUsed);
  const totalPixels = used.reduce((n, p) => n + p.pixels, 0);
  const ports = showEmpty ? g.ports : used;
  const hasCurrent = g.ports.some((p) => p.current_ma != null);
  const hasPower = g.ports.some((p) => p.enabled != null || p.fuse_ok != null);

  return (
    <section className="panel output-group">
      <div className="panel-head">
        <h2>{g.name} {!g.enabled && <span className="badge">disabled</span>}</h2>
        <span className="muted">
          {[
            g.ports.length > 0 && `${used.length} of ${g.ports.length} ports used · ${totalPixels.toLocaleString()} pixels`,
            g.start_channel != null && `start ch ${g.start_channel}`,
            g.channel_count != null && `${g.channel_count} channels`,
          ].filter(Boolean).join(" · ")}
        </span>
      </div>
      {g.ports.length > 0 && (
        <div className="table-scroll">
          <table className="plain ports">
            <thead>
              <tr>
                <th>Port</th><th>String</th><th className="r">Pixels</th><th className="r">Start ch</th>
                {kind === "fpp" && <th className="r">End ch</th>}
                <th>Details</th>
                {hasPower && <th>Power</th>}
                {hasCurrent && <th className="r">Current</th>}
              </tr>
            </thead>
            <tbody>
              {ports.map((p) => {
                const strings = p.strings.length ? p.strings : [null];
                return strings.map((v, j) => (
                  <tr key={`${p.number}-${j}`} className={`${j > 0 ? "sub" : ""} ${isUsed(p) ? "" : "muted"}`}>
                    <td>{j === 0 ? p.number : ""}</td>
                    <td>{v?.description || (v ? <span className="muted">—</span> : "")}</td>
                    <td className="r num">{v ? v.pixels.toLocaleString() : 0}</td>
                    <td className="r num">{v?.start_channel?.toLocaleString() ?? ""}</td>
                    {kind === "fpp" && (
                      <td className="r num">{v && v.pixels && v.start_channel != null
                        ? (v.start_channel + v.pixels * channelsPerPixel(v) - 1).toLocaleString() : ""}</td>
                    )}
                    <td className="muted small">{v && [
                      j === 0 && p.protocol, v.color_order, v.brightness != null && `${v.brightness}%`,
                      v.group && v.group > 1 && `group ${v.group}`, v.reverse && "reversed", !v.enabled && "disabled",
                    ].filter(Boolean).join(" · ")}</td>
                    {hasPower && <td>{j === 0 && <PowerCell enabled={p.enabled} fuse={p.fuse_ok} />}</td>}
                    {hasCurrent && <td className="r num">{j === 0 && p.current_ma != null ? `${p.current_ma} mA` : ""}</td>}
                  </tr>
                ));
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function PowerCell({ enabled, fuse }: { enabled: boolean | null; fuse: boolean | null }) {
  if (fuse === false) return <span className="badge bad">fuse tripped</span>;
  if (enabled == null) return null;
  return <span className={enabled ? "" : "muted"}>{enabled ? "on" : "off"}</span>;
}
