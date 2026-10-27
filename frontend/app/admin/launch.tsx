import React, { useState } from "react";
import { RefreshControl, ScrollView, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { queryClient } from "@/src/query-client";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, useToast } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Status = { preview: boolean; done_at: string | null; available: boolean; counts: Record<string, number> };
const LABELS: [string, string][] = [["merchants", "Stores"], ["logins", "Store & driver logins"], ["drivers", "Drivers"], ["stops", "Orders"], ["routes", "Routes"], ["invoices", "Invoices"]];

// Admin: one-time launch cleanup — wipes every store, driver, order and invoice, keeping only the admin login.
export default function LaunchCleanupScreen() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const q = useQuery({ queryKey: ["launch-cleanup"], queryFn: () => api<Status>("/admin/launch-cleanup") });
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      await api("/admin/launch-cleanup", { method: "POST", body: { confirm } });
      setConfirm("");
      queryClient.invalidateQueries();
      toast.show("ALL DATA REMOVED · READY FOR LAUNCH", "green");
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  const d = q.data;
  return (
    <View style={s.root} testID="launch-cleanup-screen">
      <Header title="LAUNCH CLEANUP" subtitle="Remove all data before going live" />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : d && (
        <ScrollView contentContainerStyle={{ padding: spacing.md, gap: spacing.md, paddingBottom: insets.bottom + spacing.xl }}
          keyboardShouldPersistTaps="handled" refreshControl={<RefreshControl refreshing={false} onRefresh={q.refetch} />}>
          <View style={s.card}>
            <Display style={s.title}>WHAT GETS DELETED</Display>
            <Mono style={s.meta}>Every store, driver, login, order, route, invoice, rating and proof photo. Only your admin login stays.</Mono>
            {LABELS.map(([k, label]) => (
              <View key={k} style={s.line}>
                <Mono style={s.meta}>{label}</Mono>
                <Mono style={s.count} testID={`launch-count-${k}`}>{d.counts[k] ?? 0}</Mono>
              </View>
            ))}
            <Mono style={s.meta}>Kept for privacy law: the access audit log, consent records and privacy request history. Settings and pricing stay too.</Mono>
          </View>
          {d.done_at ? (
            <View style={s.card} testID="launch-cleanup-done">
              <Pill label="DONE" tone="green" />
              <Mono style={s.meta}>Cleaned on {new Date(d.done_at).toLocaleString("en-CA", { timeZone: "America/Toronto" })} — sample data will never be added again and the demo reset is off.</Mono>
            </View>
          ) : d.preview ? (
            <View style={s.card} testID="launch-cleanup-preview-note">
              <Pill label="LIVE APP ONLY" tone="yellow" />
              <Mono style={s.meta}>This is the preview, which keeps its sample data for testing. Open this screen in your published app to clean it before launch.</Mono>
              <Btn label="REMOVE ALL DATA" variant="dark" disabled onPress={() => {}} testID="launch-cleanup-button" />
            </View>
          ) : (
            <View style={s.card}>
              <Pill label="CAN'T BE UNDONE" tone="red" />
              <Mono style={s.meta}>Type DELETE to confirm.</Mono>
              <TextInput testID="launch-cleanup-confirm-input" value={confirm} onChangeText={setConfirm} autoCapitalize="characters"
                autoCorrect={false} placeholder="DELETE" placeholderTextColor={colors.onSurfaceTertiary} style={s.input} />
              <Btn label="REMOVE ALL DATA" variant="dark" loading={busy} disabled={confirm.trim().toUpperCase() !== "DELETE" || busy} onPress={run} testID="launch-cleanup-button" />
            </View>
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
  title: { fontSize: 18, color: c.onSurface },
  meta: { fontSize: 13, color: c.onSurfaceSecondary },
  line: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", minHeight: 28 },
  count: { fontSize: 15, color: c.onSurface },
  input: { borderWidth: 2, borderColor: c.border, padding: spacing.md, minHeight: 48, fontFamily: fonts.mono, fontSize: 16, color: c.onSurface, backgroundColor: c.surface },
}));
