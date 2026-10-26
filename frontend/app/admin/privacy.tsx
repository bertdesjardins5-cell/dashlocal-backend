import React, { useState } from "react";
import { Pressable, RefreshControl, ScrollView, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, ErrorBlock, Header, Loading, Mono, Pill, useToast } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";
import CustomerDataRequests from "@/src/components/CustomerDataRequests";
import PrivacyOfficerForm from "@/src/components/PrivacyOfficerForm";

type Privacy = {
  retention_days: number; last_run_at: string | null; last_run_erased: number | null; due_now: number;
  due_within_30_days: number; erased_total: number; consent_text: string;
};
type Row = { id: string; at: string; actor: string; role: string; method: string; path: string; data: string; status: number; ip: string };
const ROLES = [null, "admin", "merchant", "driver", "customer", "public"] as const;
const fmt = (iso: string) => new Date(iso).toLocaleString("en-CA", { timeZone: "America/Toronto", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });

// Admin: PIPEDA / PHIPA tools — data retention and the access audit log.
export default function PrivacyScreen() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const p = useQuery({ queryKey: ["privacy"], queryFn: () => api<Privacy>("/admin/privacy") });
  const [days, setDays] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [role, setRole] = useState<(typeof ROLES)[number]>(null);
  const [rows, setRows] = useState<Row[] | null>(null);
  const qs = `${search.trim() ? `&q=${encodeURIComponent(search.trim())}` : ""}${role ? `&role=${role}` : ""}`;
  const audit = useQuery({ queryKey: ["audit", qs], queryFn: () => api<{ rows: Row[]; total: number }>(`/admin/audit?limit=50${qs}`) });
  const shown = rows ?? audit.data?.rows ?? [];

  const saveDays = async () => {
    const n = parseInt(days ?? "", 10);
    if (!(n >= 30 && n <= 3650)) return toast.show("RETENTION MUST BE 30–3650 DAYS", "red");
    setBusy("save");
    try {
      await api("/admin/privacy", { method: "PUT", body: { retention_days: n } });
      setDays(null);
      p.refetch();
      toast.show("RETENTION SAVED", "green");
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const runNow = async () => {
    setBusy("run");
    try {
      const r = await api<{ erased_orders: number; deleted_saved_notes: number }>("/admin/privacy/run-retention", { method: "POST" });
      toast.show(`ERASED ${r.erased_orders} ORDERS · ${r.deleted_saved_notes} SAVED NOTES`, "green");
      p.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const more = async () => {
    const last = shown[shown.length - 1];
    if (!last) return;
    setBusy("more");
    try {
      const r = await api<{ rows: Row[] }>(`/admin/audit?limit=50${qs}&before=${encodeURIComponent(last.at)}`);
      setRows([...shown, ...r.rows]);
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  return (
    <View style={s.root} testID="admin-privacy-screen">
      <Header title="PRIVACY" subtitle="PIPEDA · PHIPA" />
      <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={audit.isRefetching} onRefresh={() => { setRows(null); audit.refetch(); p.refetch(); }} />}>
        <Mono style={s.section}>DATA RETENTION</Mono>
        {p.isLoading ? <Loading /> : p.error ? <ErrorBlock message={(p.error as Error).message} onRetry={p.refetch} /> : p.data ? (
          <View style={s.block} testID="retention-card">
            <Mono style={s.meta}>Orders older than this have the customer&apos;s name, phone, address, notes, photo and signature erased automatically. Invoice numbers and totals are kept for tax records.</Mono>
            <View style={s.rowC}>
              <TextInput testID="retention-days-input" value={days ?? String(p.data.retention_days)} onChangeText={setDays} keyboardType="number-pad" style={s.input} />
              <Mono style={s.label}>DAYS ({((parseInt(days ?? String(p.data.retention_days), 10) || 0) / 365).toFixed(1)} YEARS)</Mono>
              <Btn testID="retention-save" label="SAVE" loading={busy === "save"} disabled={days === null} onPress={saveDays} />
            </View>
            <View style={s.stats}>
              <Pill label={`${p.data.due_now} DUE NOW`} tone={p.data.due_now ? "red" : "light"} />
              <Pill label={`${p.data.due_within_30_days} DUE IN 30 DAYS`} tone={p.data.due_within_30_days ? "yellow" : "light"} />
              <Pill label={`${p.data.erased_total} ERASED`} tone="light" />
            </View>
            <Mono style={s.meta} testID="retention-last-run">
              {p.data.last_run_at ? `Last check ${fmt(p.data.last_run_at)} · ${p.data.last_run_erased ?? 0} erased · runs every 6 hours` : "Runs automatically every 6 hours"}
            </Mono>
            <Btn testID="retention-run-now" label="RUN CHECK NOW" variant="dark" icon="trash" loading={busy === "run"} onPress={runNow} />
          </View>
        ) : null}

        <Mono style={s.section}>PRIVACY OFFICER</Mono>
        <PrivacyOfficerForm notify={toast.show} />

        <Mono style={s.section}>CUSTOMER DATA REQUESTS</Mono>
        <CustomerDataRequests notify={toast.show} />

        <Mono style={s.section}>AUDIT LOG · {audit.data?.total ?? "…"} ENTRIES</Mono>
        <View style={s.block}>
          <TextInput testID="audit-search" value={search} onChangeText={(v) => { setSearch(v); setRows(null); }} placeholder="Search person, data or page"
            placeholderTextColor={colors.muted} style={[s.input, { width: "100%" }]} autoCapitalize="none" />
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm }}>
            {ROLES.map((r) => (
              <Pressable key={r ?? "all"} testID={`audit-role-${r ?? "all"}`} onPress={() => { setRole(r); setRows(null); }}
                style={[s.chip, role === r && { backgroundColor: colors.surfaceInverse }]}>
                <Mono style={[s.chipText, role === r && { color: colors.onSurfaceInverse }]}>{(r ?? "all").toUpperCase()}</Mono>
              </Pressable>
            ))}
          </ScrollView>
        </View>
        {audit.isLoading ? <Loading /> : audit.error ? <ErrorBlock message={(audit.error as Error).message} onRetry={audit.refetch} /> : (
          <>
            {shown.map((r) => (
              <View key={r.id} style={s.logRow} testID="audit-row">
                <View style={s.rowC}>
                  <Mono style={[s.label, { flex: 1 }]} numberOfLines={1}>{r.actor}</Mono>
                  <Pill label={r.role} tone={r.status >= 400 ? "red" : "light"} />
                </View>
                <Mono style={s.metaDark}>{r.method === "GET" ? "Viewed" : "Changed"} {r.data} · {r.status}</Mono>
                <Mono style={s.meta} numberOfLines={1}>{r.method} {r.path}</Mono>
                <Mono style={s.meta}>{fmt(r.at)} · {r.ip}</Mono>
              </View>
            ))}
            {!shown.length ? <Mono style={[s.meta, { padding: spacing.lg }]}>No entries match.</Mono> : null}
            {shown.length && shown.length < (audit.data?.total ?? 0) ? (
              <View style={s.block}><Btn testID="audit-more" label="LOAD MORE" variant="dark" loading={busy === "more"} onPress={more} /></View>
            ) : null}
          </>
        )}
      </ScrollView>
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  rowC: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  stats: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 1 },
  meta: { fontSize: 12, color: c.muted },
  metaDark: { fontSize: 13, color: c.onSurface, fontWeight: "700" },
  input: { height: 48, minWidth: 90, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
  chip: { minHeight: 40, paddingHorizontal: spacing.md, justifyContent: "center", borderWidth: 2, borderColor: c.border },
  chipText: { fontSize: 11, fontWeight: "700" },
  logRow: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, gap: 2, borderBottomWidth: 1, borderColor: c.border },
}));
