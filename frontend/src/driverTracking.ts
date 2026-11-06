import { Platform } from "react-native";
import * as Location from "expo-location";
import * as TaskManager from "expo-task-manager";
import { api } from "@/src/api";
import { ensureSession, getSessionUser } from "@/src/session";

// Locked-phone location for drivers (iPhone only): the OS keeps sending positions so stops still auto-mark ARRIVED within 50 m.
// Android is app-open only (no background location permission), so this is skipped there.
// Needs an installed app build; Expo Go and the web preview fall back to tracking only while the app is open.
export const BG_TASK = "dashlocal-driver-location";
const BG_SUPPORTED = Platform.OS === "ios";

if (BG_SUPPORTED) {
  TaskManager.defineTask<{ locations: Location.LocationObject[] }>(BG_TASK, async ({ data, error }) => {
    const loc = data?.locations?.[data.locations.length - 1];
    if (error || !loc) return;
    await ensureSession(); // the app may have been relaunched in the background
    const driverId = getSessionUser()?.driver_id;
    if (!driverId) return;
    try {
      const r = await api<{ route_active?: boolean }>(`/drivers/${driverId}/location`, { method: "POST", body: { lat: loc.coords.latitude, lng: loc.coords.longitude } });
      if (r.route_active === false) await Location.stopLocationUpdatesAsync(BG_TASK); // route finished while the app was closed
    } catch {}
  });
}

export type BgStatus = "granted" | "ask" | "blocked" | "unsupported";

/** "Always" location state, without prompting (so the app can show its disclosure first). */
export async function backgroundStatus(): Promise<BgStatus> {
  if (!BG_SUPPORTED) return "unsupported";
  try {
    const bg = await Location.getBackgroundPermissionsAsync();
    return bg.granted ? "granted" : bg.canAskAgain ? "ask" : "blocked";
  } catch {
    return "unsupported";
  }
}

/** Start OS location updates. Only asks for "Always" when `ask` is true — call it after the driver read the disclosure. */
export async function startBackgroundTracking(ask = false): Promise<boolean> {
  if (!BG_SUPPORTED) return false;
  try {
    let bg = await Location.getBackgroundPermissionsAsync();
    if (!bg.granted && bg.canAskAgain && ask) bg = await Location.requestBackgroundPermissionsAsync();
    if (!bg.granted) return false;
    if (await Location.hasStartedLocationUpdatesAsync(BG_TASK)) return true;
    await Location.startLocationUpdatesAsync(BG_TASK, {
      accuracy: Location.Accuracy.High,
      distanceInterval: 10,
      timeInterval: 4000,
      pausesUpdatesAutomatically: false,
      activityType: Location.ActivityType.AutomotiveNavigation,
      showsBackgroundLocationIndicator: true,
      foregroundService: {
        notificationTitle: "DashLocal route in progress",
        notificationBody: "Sharing your location so stops mark arrived automatically",
      },
    });
    return true;
  } catch {
    return false; // e.g. Expo Go, which can't run background location
  }
}

export async function stopBackgroundTracking() {
  if (!BG_SUPPORTED) return;
  try {
    if (await Location.hasStartedLocationUpdatesAsync(BG_TASK)) await Location.stopLocationUpdatesAsync(BG_TASK);
  } catch {}
}
