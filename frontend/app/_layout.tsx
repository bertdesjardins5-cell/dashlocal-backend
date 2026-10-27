import { QueryClientProvider } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { useFonts } from "expo-font";
import { useEffect } from "react";
import { Alert, LogBox, Platform, StyleSheet, View } from "react-native";
import { StatusBar } from "expo-status-bar";
import * as Notifications from "expo-notifications";
import * as Linking from "expo-linking";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { KeyboardProvider } from "react-native-keyboard-controller";

import { ErrorBoundary } from "@/src/components/error-boundary";
import { queryClient } from "@/src/query-client";
import { useTheme } from "@/src/theme";
import "@/src/driverTracking"; // registers the background location task at startup

// Disable logbox errors etc so that users can see the app
// and agent works as expected.
LogBox.ignoreAllLogs(true);

// Phone alerts: show while the app is open, and the Android channel must exist before any push arrives.
if (Platform.OS !== "web") {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({ shouldShowAlert: true, shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false }),
  });
}
if (Platform.OS === "android") {
  Notifications.setNotificationChannelAsync("default", { name: "Default", importance: Notifications.AndroidImportance.MAX, sound: "default" });
}

function usePushTaps(ready: boolean) {
  const router = useRouter();
  useEffect(() => {
    if (Platform.OS === "web" || !ready) return; // wait until the screens are mounted (cold-start taps)
    const open = (response: Notifications.NotificationResponse | null) => {
      const data: any = response?.notification.request.content.data || {};
      const url = String(data.deeplink || data.action_url || "");
      // only in-app screens we send alerts for; anything else (e.g. external links) is ignored
      if (/^\/(driver|merchant|admin)(\/[\w-]+)*$/.test(url)) router.push(url as any);
    };
    const tapSub = Notifications.addNotificationResponseReceivedListener(open); // tapped while the app is open
    Notifications.getLastNotificationResponseAsync().then(open); // tapped while the app was closed
    (async () => {
      // alerts turned off and the phone won't ask again: remind once a week how to turn them back on
      const { status, canAskAgain } = await Notifications.getPermissionsAsync();
      if (status !== "denied" || canAskAgain) return;
      const last = await AsyncStorage.getItem("pushNudgeAt");
      if (last && Date.now() - Number(last) <= 7 * 24 * 60 * 60 * 1000) return;
      const stamp = () => AsyncStorage.setItem("pushNudgeAt", String(Date.now()));
      Alert.alert("Turn on alerts", "Get a buzz the moment dispatch gives you a new run.", [
        { text: "Later", style: "cancel", onPress: stamp },
        { text: "Open Settings", onPress: () => { stamp(); Linking.openSettings(); } },
      ]);
    })();
    return () => tapSub.remove();
  }, [router, ready]);
}

export default function RootLayout() {
  const { colors } = useTheme();
  const [loaded] = useFonts({
    SpaceGrotesk: require("../assets/fonts/SpaceGrotesk.ttf"),
    JetBrainsMono: require("../assets/fonts/JetBrainsMono.ttf"),
    "Poppins-SemiBold": require("../assets/fonts/Poppins-SemiBold.ttf"),
    "Poppins-Medium": require("../assets/fonts/Poppins-Medium.ttf"),
    PTSerif: require("../assets/fonts/PT_Serif-Web-Regular.ttf"),
    "PTSerif-Bold": require("../assets/fonts/PT_Serif-Web-Bold.ttf"),
  });
  usePushTaps(loaded);
  // One app level ErrorBoundary; a render crash shows a reload screen
  // instead of a blank app. GestureHandlerRootView must wrap the whole tree
  // or any gesture/swipe/drawer screen crashes in Expo Go.
  if (!loaded) return <View style={{ flex: 1, backgroundColor: colors.surface }} />;
  return (
    <GestureHandlerRootView style={styles.root}>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <KeyboardProvider>
            <StatusBar style="light" />
            <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.surface } }} />
          </KeyboardProvider>
        </QueryClientProvider>
      </ErrorBoundary>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
  },
});
