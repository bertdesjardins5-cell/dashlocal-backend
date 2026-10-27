import { Platform } from "react-native";
import * as Notifications from "expo-notifications";
import * as Device from "expo-device";
import { api } from "@/src/api";

// Phone alerts: register this device for the signed-in user (re-run on every app open — tokens rotate).
// Only works in a published build (not Expo Go / web). Never blocks sign-in if denied or unavailable.
export async function registerForPush(userId: string) {
  if (Platform.OS === "web" || !Device.isDevice) return;
  try {
    const current = await Notifications.getPermissionsAsync();
    let status = current.status;
    if (status !== "granted" && current.canAskAgain) status = (await Notifications.requestPermissionsAsync()).status;
    if (status !== "granted") return;
    const token = await Notifications.getDevicePushTokenAsync();
    await api("/register-push", { method: "POST", body: { user_id: userId, platform: Platform.OS, device_token: String(token.data) } });
  } catch (e) {
    console.log("push registration skipped", e);
  }
}
