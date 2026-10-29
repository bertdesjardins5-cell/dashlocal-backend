import { Platform } from "react-native";
import * as Location from "expo-location";
import * as TaskManager from "expo-task-manager";
import { api } from "@/src/api";
import { ensureSession, getSessionUser } from "@/src/session";

// Locked-phone location for drivers: the OS keeps sending positions so stops still auto-mark ARRIVED within 50 m.
// Needs an installed app build; Expo Go and the web preview fall back to tracking only while the app is open.
export const BG_TASK = "dashlocal-driver-location";

if (Platform.OS !== "web") {
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

/** Ask for "Always" location (foreground must already be granted) and start OS updates. Returns true when running. */
export async function startBackgroundTracking(): Promise<boolean> {
  if (Platform.OS === "web") return false;
  try {
    let bg = await Location.getBackgroundPermissionsAsync();
    if (!bg.granted && bg.canAskAgain) bg = await Location.requestBackgroundPermissionsAsync();
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
        notificationTitle: "ArriveWise route in progress",
        notificationBody: "Sharing your location so stops mark arrived automatically",
      },
    });
    return true;
  } catch {
    return false; // e.g. Expo Go, which can't run background location
  }
}

export async function stopBackgroundTracking() {
  if (Platform.OS === "web") return;
  try {
    if (await Location.hasStartedLocationUpdatesAsync(BG_TASK)) await Location.stopLocationUpdatesAsync(BG_TASK);
  } catch {}
}
