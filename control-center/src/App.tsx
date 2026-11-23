import React, { useCallback, useEffect, useState } from "react";

type Driver = { id: string; name: string; phone?: string | null; route?: { id: string; name: string; status: string } | null };
type Route = { id: string; name: string; status: string; total?: number; completed?: number; driver?: Driver | null; service_date: string; planned_distance_m?: number };
type Analytics = { drops_per_hour: number; avg_deviation_pct: number; delivered: number; failed: number; total_orders: number; unassigned: number; success_rate: number; active_routes: number; avg_rating: number | null };
type Zone = { zone: string; postal_code: string; count: number };
type DispatchPreview = unknown;
type Session = { token: string; user: { role: string; email?: string } };
const BASE = (import.meta.env.VITE_DASHLOCAL_API_URL as string | undefined || "").replace(/\/$/, "");

async function request<T>(path: string, token?: string, options?: RequestInit): Promise<T> {
  if (!BASE) throw new Error("Configure VITE_DASHLOCAL_API_URL in control-center/.env first.");
  const r = await fetch(BASE + "/api" + path, {
    ...options, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}), ...(options?.headers || {}) },
    cache: "no-store",
  });
  if (!r.ok) {
    let detail = "Request failed (" + r.status + ")";
    try { const body = await r.json(); if (typeof body.detail === "string") detail = body.detail; } catch { /* keep generic message */ }
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}
function todayLocal(): string {
  const f = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Toronto", year:"numeric", month:"2-digit", day:"2-digit" });
  return f.format(new Date());
}
function formatNumber(n: number | null | undefined) { return typeof n === "number" ? n.toLocaleString("en-CA") : "—"; }

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [signingIn, setSigningIn] = useState(false);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [busy, setBusy] = useState(false);
  const [day, setDay] = useState(todayLocal);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);

  async function signIn(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSigningIn(true); setError("");
    try {
      const response = await request<Session>("/auth/login", undefined, { method: "POST", body: JSON.stringify({ email, password }) });
      const current = await request<{ role: string; email?: string }>("/auth/me", response.token);
      if (current.role !== "admin") throw new Error("The Control Center requires an administrator account.");
      setSession({ token: response.token, user: current }); setPassword("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not sign in"); } finally { setSigningIn(false); }
  }

  const refresh = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    const results = await Promise.allSettled([
      request<Analytics>("/analytics", session.token),
      request<Route[]>("/routes", session.token),
      request<Driver[]>("/drivers", session.token),
      request<Zone[]>("/zones?date=" + encodeURIComponent(day), session.token),
    ]);
    const failure = results.find(r => r.status === "rejected");
    results.forEach((r, i) => {
      if (r.status !== "fulfilled") return;
      if (i === 0) setAnalytics(r.value as Analytics);
      if (i === 1) setRoutes(r.value as Route[]);
      if (i === 2) setDrivers(r.value as Driver[]);
      if (i === 3) setZones(r.value as Zone[]);
    });
    if (failure && failure.status === "rejected") {
      const reason = failure.reason;
      if (reason && typeof reason === "object" && String(reason).includes("401")) setSession(null);
      setError(reason instanceof Error ? reason.message : "Unable to load some data");
    } else { setError(""); setUpdatedAt(new Date()); }
    setBusy(false);
  }, [session, day]);

  useEffect(() => {
    if (!session) return;
    void refresh();
    const interval = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 15000);
    return () => window.clearInterval(interval);
  }, [session, refresh]);

  if (!session) return <main className="login-wrap"><div className="login-card">
    <div className="brand-mark">D</div><h1>DashLocal</h1><p className="subtitle">Control Center · Administrator Sign In</p>
    <form onSubmit={signIn}><label>Email<input type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} required /></label>
      <label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
      <button type="submit" disabled={signingIn}>{signingIn ? "Signing in…" : "Sign in"}</button></form>
    {error && <p role="alert" className="error">{error}</p>}
    <p className="hint">Phase 1: read-only. Please use a non-production backend first.</p>
  </div></main>;

  const stats = [
    ["Orders", formatNumber(analytics?.total_orders)],
    ["Delivered", formatNumber(analytics?.delivered)],
    ["Unassigned", formatNumber(analytics?.unassigned)],
    ["Active routes", formatNumber(analytics?.active_routes)],
    ["Success rate", analytics ? analytics.success_rate + "%" : "—"],
    ["Drops / hour", formatNumber(analytics?.drops_per_hour)],
  ];
  return <div className="layout"><aside className="sidebar"><div className="logo"><span className="brand-mark">D</span><span>DashLocal<small>CONTROL CENTER</small></span></div>
    <nav><a href="#overview">Overview</a><a href="#routes">Routes</a><a href="#drivers">Drivers</a><a href="#zones">Dispatch backlog</a></nav>
    <div className="side-note">READ-ONLY PREVIEW<br/><small>Does not change live deliveries</small></div></aside>
    <main className="main"><header className="topbar"><div><div className="eyebrow">OPERATIONS / DASHBOARD</div><h1 id="overview">Delivery overview</h1><p className="subtitle">Your drivers, routes and orders in one place.</p></div>
    <div className="top-actions"><span className="readonly">Read-only</span><button className="secondary" onClick={() => void refresh()} disabled={busy}>Refresh</button>
      <button className="secondary" onClick={() => { setSession(null); setAnalytics(null); setRoutes([]); setDrivers([]); setZones([]); setError(""); }}>Sign out</button></div></header>
    <div className="toolbar"><span>Service date</span><input aria-label="Service date" type="date" value={day} onChange={e => setDay(e.target.value)} /><span>{updatedAt ? "Last updated " + updatedAt.toLocaleTimeString("en-CA") : "Waiting for API data"}</span></div>
    {error && <div className="error banner" role="alert">{error}</div>}
    <section className="stats">{stats.map(([label, value]) => <article className="stat" key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
    <div className="two-col"><section className="panel" id="routes"><div className="panel-head"><div><h2>Delivery routes</h2><p>Active and upcoming routes from your backend</p></div><span className="count">{routes.length}</span></div>
      <div className="table-wrap"><table><thead><tr><th>Route</th><th>Driver</th><th>Progress</th><th>Status</th></tr></thead><tbody>{routes.length ? routes.map(r => <tr key={r.id}><td><b>{r.name}</b><small>{r.service_date}</small></td><td>{r.driver?.name || "Unassigned"}</td><td>{formatNumber(r.completed)} / {formatNumber(r.total)}</td><td><span className={"status " + r.status}>{r.status}</span></td></tr>) : <tr><td colSpan={4} className="empty">No current or upcoming routes</td></tr>}</tbody></table></div></section>
    <section className="panel" id="drivers"><div className="panel-head"><div><h2>Drivers</h2><p>Accounts and active assignments</p></div><span className="count">{drivers.length}</span></div>
      <div className="driver-list">{drivers.length ? drivers.map(d => <div className="driver" key={d.id}><div className="avatar">{d.name.slice(0,1).toUpperCase()}</div><div><b>{d.name}</b><small>{d.route?.name || "No active assignment"}</small></div><span className={"status " + (d.route?.status || "idle")}>{d.route?.status || "Idle"}</span></div>) : <p className="empty">No drivers returned</p>}</div></section></div>
    <section className="panel" id="zones"><div className="panel-head"><div><h2>Dispatch backlog</h2><p>Unassigned delivery zones for {day}</p></div><span className="count">{zones.reduce((sum,z) => sum + (z.count || 0),0)} deliveries</span></div>
      <div className="zone-grid">{zones.length ? zones.map((z,i) => <div className="zone" key={i}><strong>{z.zone}</strong><span>{z.postal_code}</span><b>{formatNumber(z.count)}</b></div>) : <p className="empty">No unassigned zones returned for this date</p>}</div>
    </section>
    <footer>DashLocal Control Center · Phase 1 · Read-only integration with existing API</footer>
    </main></div>;
}
