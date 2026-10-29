import React, { useState } from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, useToast } from "@/src/ui";
import { makeStyles, spacing } from "@/src/theme";

type Order = { tracking_code: string; phone_last4: string; address: string };
type Kit = { exists: boolean; store_email: string; driver_phone: string; route: { status: string; service_date: string } | null; orders: Order[];
  store_password?: string; driver_pin?: string };

// Admin: one tap creates (or resets) the Google Play reviewer store, driver and a 3-stop route for today.
export default function ReviewerKitScreen() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const toast = useToast();
  const q = useQuery({ queryKey: ["reviewer-kit"], queryFn: () => api<Kit>("/admin/reviewer-kit") });
  const [made, setMade] = useState<Kit | null>(null);
  const [busy, setBusy] = useState(false);

  const make = async () => {
    setBusy(true);
    try {
      const r = await api<Kit>("/admin/reviewer-kit", { method: "POST" });
      setMade(r);
      q.refetch();
      toast.show("REVIEWER ACCOUNTS READY", "green");
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  const d = made || q.data;
  return (
    <View style={s.root} testID="reviewer-kit-screen">
      <Header title="PLAY STORE REVIEWER" subtitle="Test accounts for Google's app review" />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : d && (
        <ScrollView contentContainerStyle={{ padding: spacing.md, gap: spacing.md, paddingBottom: insets.bottom + spacing.xl }}
          refreshControl={<RefreshControl refreshing={false} onRefresh={q.refetch} />}>
          <View style={s.card}>
            <Display style={s.title}>WHAT THIS CREATES</Display>
            <Mono style={s.meta}>A review store, a review driver and a 3-stop route for today in Downtown Kitchener. Terms are pre-accepted. Phone numbers are fictional, so no real texts are sent.</Mono>
            {d.exists ? <Pill label={d.route ? `ROUTE ${d.route.status.toUpperCase()} · ${d.route.service_date}` : "NO ROUTE"} tone="green" /> : <Pill label="NOT CREATED YET" tone="yellow" />}
            <Btn testID="reviewer-kit-create" label={d.exists ? "RESET & GET NEW PASSWORD / PIN" : "CREATE REVIEWER ACCOUNTS"} loading={busy} disabled={busy} onPress={make} />
            <Mono style={s.meta}>Reset again on the day you submit, so the route is dated today. Each reset makes a new password and PIN.</Mono>
          </View>
          {made?.store_password ? (
            <View style={s.card} testID="reviewer-kit-credentials">
              <Pill label="COPY NOW · SHOWN ONCE" tone="red" />
              <Display style={s.title}>PASTE INTO PLAY CONSOLE › APP ACCESS</Display>
              <Mono style={s.label}>STORE (Merchant sign-in)</Mono>
              <Mono selectable style={s.value} testID="reviewer-store-login">{made.store_email} / {made.store_password}</Mono>
              <Mono style={s.label}>DRIVER (Staff sign-in › Driver)</Mono>
              <Mono selectable style={s.value} testID="reviewer-driver-login">{made.driver_phone} / PIN {made.driver_pin}</Mono>
            </View>
          ) : null}
          <View style={s.card}>
            <Mono style={s.label}>CUSTOMER (Track my order, no login)</Mono>
            {d.orders.map((o) => (
              <Mono selectable key={o.tracking_code} style={s.value}>{o.tracking_code} · last 4 digits {o.phone_last4} · {o.address}</Mono>
            ))}
          </View>
        </ScrollView>
      )}
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  card: { borderWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary, padding: spacing.md, gap: spacing.sm },
  title: { fontSize: 18, color: c.onSurface },
  meta: { fontSize: 13, color: c.onSurfaceSecondary },
  label: { fontSize: 12, color: c.onSurfaceSecondary, marginTop: spacing.xs },
  value: { fontSize: 15, color: c.onSurface },
}));
