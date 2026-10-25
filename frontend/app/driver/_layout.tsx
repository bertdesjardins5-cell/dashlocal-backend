import React from "react";
import { View } from "react-native";
import { Redirect, Stack, usePathname } from "expo-router";
import { useSession } from "@/src/session";
import DriverSignInForm from "@/src/components/DriverSignInForm";
import { Header, Loading } from "@/src/ui";
import { makeStyles } from "@/src/theme";

// Driver screens: a driver only ever sees their own manifest, stops and proof screens (dispatch admin can view all).
export default function DriverLayout() {
  const s = useStyles();
  const path = usePathname();
  const { ready, user } = useSession();
  if (!ready) return <View style={s.root}><Header title="DRIVER" /><Loading /></View>;
  if (user?.role !== "driver" && user?.role !== "admin") {
    return (
      <View style={s.root} testID="driver-sign-in-screen">
        <Header title="DRIVER SIGN-IN" subtitle="Phone number + PIN" />
        <DriverSignInForm />
      </View>
    );
  }
  if (user.role === "driver" && user.driver_id) {
    const other = path.match(/^\/driver\/(?:summary\/)?(drv-[^/]+)/);
    if (path === "/driver" || (other && other[1] !== user.driver_id)) return <Redirect href={`/driver/${user.driver_id}`} />;
  }
  return <Stack screenOptions={{ headerShown: false }} />;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
}));
