import React from "react";
import { View } from "react-native";
import { Stack } from "expo-router";
import { useSession } from "@/src/session";
import SignInForm from "@/src/components/SignInForm";
import { Header, Loading } from "@/src/ui";
import { makeStyles } from "@/src/theme";

// Every Admin screen requires the dispatch admin sign-in.
export default function AdminLayout() {
  const s = useStyles();
  const { ready, user } = useSession();
  if (!ready) return <View style={s.root}><Header title="CONTROL CENTER" /><Loading /></View>;
  if (user?.role !== "admin") {
    return (
      <View style={s.root} testID="admin-sign-in-screen">
        <Header title="ADMIN SIGN-IN" subtitle="Dispatch · Batching · Analytics" />
        <SignInForm role="admin" title="DISPATCH ADMIN" hint="Sign in with the dispatch admin account to open the control center." />
      </View>
    );
  }
  return <Stack screenOptions={{ headerShown: false }} />;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
}));
