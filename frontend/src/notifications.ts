import { Platform } from "react-native";
import Constants, { ExecutionEnvironment } from "expo-constants";

// Phone alerts only exist in real (published/dev) builds. Expo Go no longer ships the push module,
// so importing expo-notifications there crashes the app — load it lazily and only where it works.
const isExpoGo = Constants.executionEnvironment === ExecutionEnvironment.StoreClient;

export const Notifications: typeof import("expo-notifications") | null =
  Platform.OS === "web" || isExpoGo ? null : require("expo-notifications");
