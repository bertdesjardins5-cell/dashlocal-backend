import React, { useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, ErrorBlock, Header, Loading, Mono, Pill, useToast } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type StoreRow = { id: string; name: string; business_type: string | null; accepted: boolean; reminded_at: string | null;
  users: { email: string; accepted: boolean; version: string | null; accepted_at: string | null; last_login_at: string | null }[] };
type DriverRow = { id: string; name: string; phone: string; accepted: boolean; accepted_at: string | null; last_login_at: string | null };
type Report = { version: string; driver_version: string; stores: StoreRow[]; drivers: DriverRow[] };
const fmt = (iso: string) => new Date(iso).toLocaleString("en-CA", { timeZone: "America/Toronto", month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
const FILTERS = ["all", "pending", "accepted"] as const;

// Admin: who accepted the latest Terms of Service (stores) and independent-contractor terms (drivers), with reminder emails.
export default function TermsReport() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [busy, setBusy] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["terms-report"], queryFn: () => api<Report>("/admin/terms-report") });
  const d = q.data;
  const keep = (accepted: boolean) => filter === "all" || (filter === "accepted") === accepted;

  const remind = async (st: StoreRow) => {
    setBusy(st.id);
    try {
      const r = await api<{ sent: number }>(`/admin/terms-report/${st.id}/remind`, { method: "POST" });
      toast.show(`REMINDER SENT TO ${r.sent} ${r.sent === 1 ? "PERSON" : "PEOPLE"} AT ${st.name.toUpperCase()}`, "green");
      q.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  const storesOk = d?.stores.filter((x) => x.accepted).length ?? 0;
  const driversOk = d?.drivers.filter((x) => x.accepted).length ?? 0;
  return (
    <View style={s.root} testID="admin-terms-screen">
      <Header title="TERMS ACCEPTANCE" subtitle={d ? `VERSION ${d.version}` : undefined} />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : d ? (
        <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={q.refetch} />}>
          <View style={s.block}>
            <View style={s.stats}>
              <Pill label={`STORES ${storesOk}/${d.stores.length} ACCEPTED`} tone={storesOk === d.stores.length ? "green" : "yellow"} />
              <Pill label={`DRIVERS ${driversOk}/${d.drivers.length} ACCEPTED`} tone={driversOk === d.drivers.length ? "green" : "yellow"} />
            </View>
            <Mono style={s.meta}>Anyone who hasn&apos;t accepted sees the terms the next time they open the app and can&apos;t continue until they accept.</Mono>
            <View style={s.stats}>
              {FILTERS.map((f) => (
                <Pressable key={f} testID={`terms-filter-${f}`} onPress={() => setFilter(f)} style={[s.chip, filter === f && { backgroundColor: colors.surfaceInverse }]}>
                  <Mono style={[s.chipText, filter === f && { color: colors.onSurfaceInverse }]}>{f.toUpperCase()}</Mono>
                </Pressable>
              ))}
            </View>
          </View>

          <Mono style={s.section}>STORES · TERMS OF SERVICE {d.version}</Mono>
          {d.stores.filter((x) => keep(x.accepted)).map((st) => (
            <View key={st.id} style={s.row} testID={`terms-store-${st.id}`}>
              <View style={s.rowC}>
                <Mono style={[s.name, { flex: 1 }]} numberOfLines={1}>{st.name}</Mono>
                <Pill label={st.accepted ? "accepted" : st.users.length ? "pending" : "no login"} tone={st.accepted ? "green" : st.users.length ? "red" : "light"} />
              </View>
              {st.users.map((u) => (
                <Mono key={u.email} style={s.meta} numberOfLines={2}>
                  {u.email} · {u.accepted && u.accepted_at ? `accepted ${fmt(u.accepted_at)}` : u.version ? `accepted older version ${u.version}` : "not accepted yet"}
                </Mono>
              ))}
              {!st.accepted && st.users.length ? (
                <View style={s.rowC}>
                  <Mono style={[s.meta, { flex: 1 }]}>{st.reminded_at ? `Last reminder ${fmt(st.reminded_at)}` : "No reminder sent yet"}</Mono>
                  <Btn testID={`terms-remind-${st.id}`} label="EMAIL REMINDER" icon="mail" variant="dark" loading={busy === st.id} onPress={() => remind(st)} />
                </View>
              ) : null}
            </View>
          ))}

          <Mono style={s.section}>DRIVERS · INDEPENDENT CONTRACTOR TERMS {d.driver_version}</Mono>
          {d.drivers.filter((x) => keep(x.accepted)).map((dr) => (
            <View key={dr.id} style={s.row} testID={`terms-driver-${dr.id}`}>
              <View style={s.rowC}>
                <Mono style={[s.name, { flex: 1 }]} numberOfLines={1}>{dr.name}</Mono>
                <Pill label={dr.accepted ? "accepted" : "pending"} tone={dr.accepted ? "green" : "red"} />
              </View>
              <Mono style={s.meta}>{dr.phone} · {dr.accepted && dr.accepted_at ? `accepted ${fmt(dr.accepted_at)}` : "asked before their next shift"}</Mono>
            </View>
          ))}
          {!d.drivers.length ? <Mono style={[s.meta, { padding: spacing.lg }]}>No drivers with a PIN yet.</Mono> : null}
        </ScrollView>
      ) : null}
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  row: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, gap: spacing.xs, borderBottomWidth: 1, borderColor: c.border },
  rowC: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  stats: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  name: { fontSize: 14, fontWeight: "700" },
  meta: { fontSize: 12, color: c.muted },
  chip: { minHeight: 44, paddingHorizontal: spacing.md, justifyContent: "center", borderWidth: 2, borderColor: c.border },
  chipText: { fontSize: 11, fontWeight: "700" },
}));
