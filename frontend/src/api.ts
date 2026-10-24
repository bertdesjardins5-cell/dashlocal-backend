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
  fail_reason: string | null;
};

export type Driver = { id: string; name: string; phone: string; vehicle: string; lat: number; lng: number };
export type LatLng = { lat: number; lng: number };

export type RouteDetail = {
  id: string;
  name: string;
  zone: string;
  driver_id: string | null;
  status: "planned" | "active" | "completed";
  planned_distance_m: number;
  actual_distance_m: number;
  started_at: string | null;
  driver: Driver | null;
  stops: Stop[];
  total: number;
  completed: number;
  next_stop_id: string | null;
  depot: LatLng & { name: string };
};

export type Tracking = {
  stop: Stop;
  route_status: string | null;
  driver: { name: string; vehicle: string; lat: number; lng: number } | null;
  stops_away: number | null;
  eta_minutes: number | null;
  eta_time: string | null;
  path: LatLng[];
  alert: string | null;
  depot: LatLng;
};

export async function api<T = any>(path: string, opts: { method?: string; body?: any } = {}): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    method: opts.method ?? "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : msg;
    } catch {}
    throw new Error(msg);
  }
  return res.json();
}

export const fileUrl = (path: string) => `${API}/files/${path}`;
