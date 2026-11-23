import React, { useCallback, useEffect, useState } from "react";

type Driver = { id: string; name: string };
type Preview = { service_date: string; total: number; zones: { zone: string; count: number; driver: Driver | null }[]; drivers: Driver[] };
const BASE = (import.meta.env.VITE_DASHLOCAL_API_URL as string | undefined || "").replace(/[/]$/, "");
const WRITES_ENABLED = import.meta.env.VITE_ENABLE_TEST_DISPATCH === "true";

async function api<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + "/api" + path, {
    ...init, cache: "no-store",
    headers: { Authorization: "Bearer " + token, "Content-Type": "application/json", ...init?.headers }
  });
  if (!response.ok) {
    let message = "Request failed (" + response.status + ")";
    try { const body = await response.json(); if (typeof body.detail === "string") message = body.detail; } catch {}
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export default function DispatchPanel({ token, day, onDispatched }: { token: string; day: string; onDispatched: () => Promise<void> }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selected, setSelected] = useState<Record<string,string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const load = useCallback(async () => {
    try { setPreview(await api<Preview>("/dispatch/preview?date=" + encodeURIComponent(day), token)); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Preview unavailable"); }
  }, [token, day]);
  useEffect(() => { void load(); }, [load]);
  async function dispatch(zone: string) {
    if (!WRITES_ENABLED || !confirmed || !selected[zone] || busy) return;
    const driver = preview?.drivers.find(d => d.id === selected[zone]);
    if (!window.confirm("TEST DISPATCH ONLY: Create a route for " + zone + " assigned to " + (driver?.name || "driver") + " on " + day + "? This will change backend data.")) return;
    setBusy(true);
    try {
      await api("/routes/batch", token, { method: "POST", body: JSON.stringify({ zone, driver_id: selected[zone], service_date: day }) });
      setConfirmed(false); await load(); await onDispatched();
    } catch (e) { setError(e instanceof Error ? e.message : "Dispatch failed"); }
    finally { setBusy(false); }
  }
  return <section className="panel" id="dispatch">
    <div className="panel-head"><div><h2>Dispatch planning</h2><p>Review the backend's actual unassigned orders and driver suggestions</p></div><span className="count">{preview?.total ?? "—"} awaiting dispatch</span></div>
    <div className="dispatch-body">
      {!WRITES_ENABLED && <p className="notice">Safe preview mode: dispatch buttons are disabled. To test dispatching, a developer must explicitly enable VITE_ENABLE_TEST_DISPATCH=true against a non-production backend.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {!preview ? <p className="empty">Loading dispatch preview…</p> : <>
        <div className="dispatch-grid">{preview.zones.map(z => <article className="dispatch-zone" key={z.zone}>
          <div><strong>{z.zone}</strong><span className="count">{z.count} orders</span></div>
          <p>Suggested driver: {z.driver?.name || "None assigned"}</p>
          <label>Assign to driver<select aria-label={"Driver for " + z.zone} value={selected[z.zone] || ""} onChange={e => setSelected(v => ({ ...v, [z.zone]: e.target.value }))}>
            <option value="">Select driver</option>{preview.drivers.map(d => <option value={d.id} key={d.id}>{d.name}</option>)}
          </select></label>
          <button type="button" disabled={!WRITES_ENABLED || !confirmed || !selected[z.zone] || z.count === 0 || busy} onClick={() => void dispatch(z.zone)}>Create batch route</button>
        </article>)}</div>
        {WRITES_ENABLED && <label className="confirm-row"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> I confirm this is a test backend and understand dispatch changes its data.</label>}
      </>}
    </div>
  </section>;
}
