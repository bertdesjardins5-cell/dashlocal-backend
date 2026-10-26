import { clearSession, getToken } from "@/src/session";

export const BACKEND_URL = process.env.EXPO_PUBLIC_BACKEND_URL as string;
export const API = `${BACKEND_URL}/api`;
export const WS_BASE = `${BACKEND_URL.replace(/^http/, "ws")}/api/ws`;

export type Stop = {
  id: string;
  route_id: string | null;
  seq: number;
  customer_name: string;
  customer_phone: string;
  address: string;
  postal_code: string;
  zone: string;
  lat: number;
  lng: number;
  status: "pending" | "arrived" | "delivered" | "failed";
  tracking_code: string;
  notes: string;
  arrived_at: string | null;
  arrival_method: string | null;
  delivered_at: string | null;
  pod_photo_path: string | null;
  pod_signature: string | null;
  pod_qr_verified: boolean;
  window_start: string | null;
  window_end: string | null;
  window_date: string | null;
  rating?: number | null;
  rating_comment?: string | null;
  apology_code?: string | null;
  apology_sms_status?: string | null;
  planned_eta?: string | null;
  nearby_sms_status?: string | null;
  priority?: boolean;
  fee?: { base: number; km: number; distance_fee: number; priority_fee: number; total: number; currency: string } | null;
  late_min?: number;
  fail_reason: string | null;
  customer_note?: string | null;
  customer_note_at?: string | null;
  customer_note_seen?: boolean;
  customer_note_read_at?: string | null;
  doorstep_hint?: { photo_url: string; delivered_at: string | null } | null;
  customer_note_source?: string | null;
  pickup_run?: PickupRun | null;
};

export type PickupRun = { run: string; label: string; pickup_label: string; date: string | null; pharmacy: string | null; pickup_address: string | null };
export const pickupRunText = (p: PickupRun) =>
  `${p.label}${p.date && p.date !== depotToday() ? ` ${dayLabel(p.date)}` : ""} · pickup ${p.pickup_label}${p.pharmacy ? ` · ${p.pharmacy}` : ""}`.toUpperCase();

export type Driver = { id: string; name: string; phone: string; vehicle: string; lat: number; lng: number };
export type LatLng = { lat: number; lng: number };

export type RouteDetail = {
  id: string;
  name: string;
  zone: string;
  driver_id: string | null;
  status: "planned" | "active" | "completed";
  planned_distance_m: number;
  service_date: string;
  actual_distance_m: number;
  started_at: string | null;
  resequenced_at?: string | null;
  resequence_seen?: boolean;
  driver: Driver | null;
  stops: Stop[];
  total: number;
  completed: number;
  late_stops: number;
  next_stop_id: string | null;
  depot: LatLng & { name: string };
};

export type PublicStop = Pick<Stop, "id" | "tracking_code" | "status" | "arrived_at" | "delivered_at" | "pod_qr_verified" | "window_start" | "window_end" | "window_date" | "rating" | "nearby_sms_status"> & {
  has_photo: boolean;
  has_signature: boolean;
  customer_note_read?: boolean;
};

// Shown only after the customer enters the last 4 digits of their phone.
export type OrderPrivate = {
  customer_name: string;
  address: string;
  lat: number;
  lng: number;
  pod_photo_url: string | null;
  pod_signature: string | null;
  apology_code: string | null;
  customer_note: string | null;
  customer_note_source?: string | null;
};

export type Tracking = {
  stop: PublicStop;
  route_status: string | null;
  driver: { name: string; vehicle: string; lat: number; lng: number } | null;
  stops_away: number | null;
  eta_minutes: number | null;
  eta_time: string | null;
  path: LatLng[];
  alert: string | null;
  late_min?: number;
  depot: LatLng;
};

export async function api<T = any>(path: string, opts: { method?: string; body?: any; headers?: Record<string, string> } = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { ...opts.headers };
  if (opts.body) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, {
    method: opts.method ?? "GET",
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    // A wrong customer last-4 check is not a staff sign-in failure, so it must not sign staff out.
    if (res.status === 401 && token && !path.startsWith("/auth/login") && !path.startsWith("/track/") && !opts.headers?.["X-Order-Token"]) clearSession(); // expired or revoked: back to sign-in
    let msg = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : msg;
    } catch {}
    throw Object.assign(new Error(msg), { status: res.status });
  }
  return res.json();
}

// Proof files need a sign-in; images can't send headers, so the staff session token rides along as ?t=.
export const fileUrl = (path: string) => `${API}/files/${path}?t=${encodeURIComponent(getToken() ?? "")}`;

// Delivery windows and ETAs are shown in the depot's timezone so they line up with customer windows.
export const DEPOT_TZ = "America/Toronto";
export function fmtDepotTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  try {
    return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: DEPOT_TZ });
  } catch {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
}
export const windowLabel = (s: { window_start: string | null; window_end: string | null; window_date?: string | null }) =>
  s.window_start && s.window_end
    ? `${s.window_date && s.window_date !== depotToday() ? `${dayLabel(s.window_date)} ` : ""}${s.window_start}–${s.window_end}`
    : null;

export const smsNote = (status?: string) =>
  status === "sent" ? " · CONFIRMATION TEXT SENT" : status === "failed" ? " · TEXT COULD NOT BE SENT" : "";

// Depot-local calendar helpers (dates are YYYY-MM-DD strings).
export function depotToday(): string {
  try {
    return new Date().toLocaleDateString("en-CA", { timeZone: DEPOT_TZ });
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}
export function addDays(d: string, n: number): string {
  const t = new Date(`${d}T12:00:00Z`);
  t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
}
export function dayLabel(d: string): string {
  const t = new Date(`${d}T12:00:00Z`);
  return t.toLocaleDateString("en-US", { weekday: "short", day: "numeric", timeZone: "UTC" }).toUpperCase();
}
