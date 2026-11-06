import { Platform } from "react-native";
import Constants from "expo-constants";
import * as Device from "expo-device";
import { api } from "@/src/api";
import { Notifications } from "@/src/notifications";

// Phone alerts: register this device for the signed-in user (re-run on every app open — tokens rotate).
// Only works in a published build (not Expo Go / web). Never blocks sign-in if denied or unavailable.
export async function registerForPush(userId: string) {
  const N = Notifications; // null on web and in Expo Go
  if (!N || !Device.isDevice) return;
  try {
    const current = await N.getPermissionsAsync();
    let status = current.status;
    if (status !== "granted" && current.canAskAgain) status = (await N.requestPermissionsAsync()).status;
    if (status !== "granted") return;
    const token = await N.getDevicePushTokenAsync();
    // Expo push token only exists in builds linked to your own Expo (EAS) project (self-hosting); skipped otherwise.
    const projectId = Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
    const expo = projectId ? await N.getExpoPushTokenAsync({ projectId }).catch(() => null) : null;
    await api("/register-push", { method: "POST", body: { user_id: userId, platform: Platform.OS, device_token: String(token.data), expo_token: expo?.data ?? null } });
  } catch (e) {
    console.log("push registration skipped", e);
  }
}
