import React, { useCallback } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, useToast } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

export type SetupStatus = { show: boolean; done: boolean; steps: { pricing: boolean; store: boolean; driver: boolean } };

const STEPS = [
  { key: "pricing", title: "1 · CHECK YOUR PRICES", text: "Per-drop fee, distance rate and rush fee every store pays.", href: "/admin/pricing", cta: "OPEN PRICING" },
  { key: "store", title: "2 · ADD YOUR FIRST STORE", text: "Create the store and the login its staff use to send orders.", href: "/admin/merchants", cta: "ADD A STORE" },
  { key: "driver", title: "3 · ADD YOUR FIRST DRIVER", text: "Name, mobile number, vehicle and the 6-digit PIN they sign in with.", href: "/admin/drivers", cta: "ADD A DRIVER" },
] as const;

// Admin: step-by-step guide to set up the first real store, driver and pricing after the launch cleanup.
export default function SetupGuide() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { colors } = useTheme();
  const toast = useToast();
  const q = useQuery({ queryKey: ["setup"], queryFn: () => api<SetupStatus>("/admin/setup") });
  useFocusEffect(useCallback(() => { q.refetch(); }, [q.refetch])); // ticks update when coming back from a step

  const post = async (path: string, msg: string) => {
    try {
      await api(path, { method: "POST" });
      toast.show(msg, "green");
      q.refetch();
      return true;
    } catch (e: any) {
      toast.show(e.message, "red");
      return false;
    }
  };
  const d = q.data;
  const left = d ? Object.values(d.steps).filter((v) => !v).length : 3;

  return (
    <View style={s.root} testID="setup-guide-screen">
      <Header title="SETUP GUIDE" subtitle="Your first store, driver and prices" />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : d && (
        <ScrollView contentContainerStyle={{ padding: spacing.md, gap: spacing.md, paddingBottom: insets.bottom + spacing.xl }}
          refreshControl={<RefreshControl refreshing={false} onRefresh={q.refetch} />}>
          <Mono style={s.meta} testID="setup-progress">{left === 0 ? "ALL SET — YOU'RE READY TO TAKE ORDERS" : `${3 - left} OF 3 DONE`}</Mono>
          {STEPS.map((st) => {
            const ok = d.steps[st.key];
            return (
              <View key={st.key} style={[s.card, ok && { borderColor: colors.success }]} testID={`setup-step-${st.key}`}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }}>
                  <Ionicons name={ok ? "checkmark-circle" : "ellipse-outline"} size={26} color={ok ? colors.success : colors.onSurface} />
                  <Display style={s.title}>{st.title}</Display>
                </View>
                <Mono style={s.meta}>{st.text}</Mono>
                <View style={{ flexDirection: "row", gap: spacing.sm, flexWrap: "wrap" }}>
                  <Btn testID={`setup-open-${st.key}`} label={ok && st.key !== "pricing" ? "ADD ANOTHER" : st.cta} variant={ok ? "outline" : "primary"}
                    onPress={() => router.push(st.href)} />
                  {st.key === "pricing" && !ok ? (
                    <Btn testID="setup-pricing-ok" label="PRICES LOOK GOOD" variant="outline" onPress={() => post("/admin/setup/pricing-reviewed", "PRICES CONFIRMED")} />
                  ) : null}
                </View>
              </View>
            );
          })}
          {left === 0 || d.done ? (
            <Btn testID="setup-done-button" label="BACK TO DASHBOARD" variant="dark" onPress={() => router.replace("/admin")} />
          ) : (
            <Pressable testID="setup-skip-button" onPress={() => post("/admin/setup/finish", "SETUP GUIDE HIDDEN").then((ok) => ok && router.replace("/admin"))} style={s.skip}>
              <Mono style={s.skipText}>I WILL FINISH LATER — HIDE THIS GUIDE</Mono>
            </Pressable>
          )}
        </ScrollView>
      )}
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  card: { borderWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary, padding: spacing.md, gap: spacing.sm },
  title: { fontSize: 17, color: c.onSurface, flex: 1 },
  meta: { fontSize: 13, color: c.muted },
  skip: { minHeight: 48, alignItems: "center", justifyContent: "center" },
  skipText: { fontSize: 12, color: c.onSurface, textDecorationLine: "underline" },
}));
