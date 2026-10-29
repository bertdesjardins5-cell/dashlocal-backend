import { Platform } from "react-native";
import Constants, { ExecutionEnvironment } from "expo-constants";
import { requireOptionalNativeModule } from "expo";

// Phone alerts only exist in real (published/dev) builds. Expo Go no longer ships the push modules,
// so importing expo-notifications there crashes the app. Load it lazily and only when its native
// modules are actually present (works regardless of how the app was launched).
function loadNotifications(): typeof import("expo-notifications") | null {
  if (Platform.OS === "web" || Constants.executionEnvironment === ExecutionEnvironment.StoreClient) return null;
  const native = ["ExpoPushTokenManager", "ExpoNotificationsHandlerModule", ...(Platform.OS === "android" ? ["ExpoTopicSubscriptionModule"] : [])];
  if (native.some((name) => !requireOptionalNativeModule(name))) return null;
  try {
    return require("expo-notifications"); // eslint-disable-line @typescript-eslint/no-require-imports
  } catch {
    return null;
  }
}

export const Notifications = loadNotifications();
