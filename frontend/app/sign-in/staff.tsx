import React, { useState } from "react";
import { Pressable, Text, View } from "react-native";
import { Redirect, useLocalSearchParams, useRouter } from "expo-router";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { SessionUser, useSession } from "@/src/session";
import SignInForm from "@/src/components/SignInForm";
import DriverSignInForm from "@/src/components/DriverSignInForm";
import BrandHeader, { Kicker } from "@/src/components/brand/BrandHeader";
import { fonts, makeStyles } from "@/src/theme";

// Staff sign-in from the landing page: dispatch admin (email) -> control centre, driver (phone + PIN) -> manifest.
export default function StaffSignIn() {
  const s = useStyles();
  const router = useRouter();
  const { ready, user } = useSession();
  const params = useLocalSearchParams<{ as?: string }>();
  const [mode, setMode] = useState<"admin" | "driver">(params.as === "driver" ? "driver" : "admin");
  const [carried, setCarried] = useState("");
  const go = (u: SessionUser) => router.replace(u.role === "admin" ? "/admin" : `/driver/${u.driver_id}`);
  if (ready && user?.role === "admin") return <Redirect href="/admin" />;
  if (ready && user?.role === "driver" && user.driver_id) return <Redirect href={`/driver/${user.driver_id}`} />;
  return (
    <View style={s.root} testID="staff-sign-in-screen">
      <BrandHeader />
      <KeyboardAwareScrollView contentContainerStyle={s.wrap} keyboardShouldPersistTaps="handled" bottomOffset={24}>
        <View style={s.intro}>
          <Kicker>Admins & drivers</Kicker>
          <View style={s.toggle} accessibilityRole="tablist">
            {(["admin", "driver"] as const).map((m) => (
              <Pressable key={m} testID={`staff-toggle-${m}`} onPress={() => setMode(m)} style={[s.seg, mode === m && s.segOn]} accessibilityRole="tab" accessibilityState={{ selected: mode === m }}>
                <Text style={[s.segText, mode === m && s.segTextOn]}>{m === "admin" ? "Admin · email" : "Driver · phone + PIN"}</Text>
              </Pressable>
            ))}
          </View>
        </View>
        {mode === "admin" ? (
          <SignInForm scroll={false} role="admin" title="Dispatch control centre" hint="Sign in with the dispatch admin account to open routes, merchants, pricing and analytics. Drivers: tap Driver above."
            onSignedIn={go} onPhone={(p) => { setCarried(p); setMode("driver"); }} />
        ) : (
          <DriverSignInForm key={carried} scroll={false} onSignedIn={go} initialPhone={carried}
            notice={carried ? "That looks like a phone number, so we switched you to driver sign-in. Enter your 6-digit PIN." : null} />
        )}
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.brandSoftGray },
  wrap: { padding: 24, alignItems: "center", gap: 20 },
  intro: { width: "100%", maxWidth: 440, gap: 12 },
  toggle: { flexDirection: "row", backgroundColor: c.surface, borderRadius: 14, padding: 4, borderWidth: 1, borderColor: c.brandLine },
  seg: { flex: 1, minHeight: 44, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  segOn: { backgroundColor: c.brandCharcoal },
  segText: { fontFamily: fonts.brandHeadingMedium, fontSize: 15, color: c.brandCharcoal },
  segTextOn: { color: c.onBrandCharcoal },
}));
