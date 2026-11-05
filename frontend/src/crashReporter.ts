import { Platform } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";

// Tiny crash reporter (no third-party service): any uncaught JS error is saved on the phone and sent to
// DashLocal (Admin › Crash reports), right away if possible, otherwise on the next launch.
// Imported first in app/_layout.tsx so it also catches errors thrown while the rest of the app loads.
const KEY = "dashlocal_pending_crash";
const URL_BASE = (process.env.EXPO_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");

function device() {
  let model: string | null = null;
  try {
    model = require("expo-device").modelName ?? null; // eslint-disable-line @typescript-eslint/no-require-imports
  } catch {}
  return { platform: Platform.OS, os_version: String(Platform.Version), model, app_version: Constants.expoConfig?.version ?? null };
}

async function send(report: object) {
  if (!URL_BASE) return false;
  try {
    const r = await fetch(`${URL_BASE}/api/crash-reports`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(report) });
    return r.ok;
  } catch {
    return false;
  }
}

export function reportError(e: any, fatal: boolean, where = "global") {
  const report = { message: String(e?.message ?? e).slice(0, 500), stack: String(e?.stack ?? "").slice(0, 4000), fatal, where, at: new Date().toISOString(), ...device() };
  AsyncStorage.setItem(KEY, JSON.stringify(report)).catch(() => {});
  send(report).then((ok) => ok && AsyncStorage.removeItem(KEY).catch(() => {}));
}

if (Platform.OS !== "web") {
  const g: any = global as any;
  const prev = g.ErrorUtils?.getGlobalHandler?.();
  g.ErrorUtils?.setGlobalHandler?.((e: any, fatal?: boolean) => {
    reportError(e, !!fatal);
    prev?.(e, fatal);
  });
  // a crash from the previous launch that couldn't be sent yet
  AsyncStorage.getItem(KEY).then(async (raw) => {
    if (raw && (await send({ ...JSON.parse(raw), delayed: true }))) await AsyncStorage.removeItem(KEY);
  }).catch(() => {});
}
