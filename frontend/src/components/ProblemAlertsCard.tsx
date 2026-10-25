import React, { useState } from "react";
import { ActivityIndicator, Pressable, Switch, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, fmtDepotTime } from "@/src/api";
import { Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Alert = { id: string; tracking_code: string; customer_name: string; kind: "late" | "delivered_late" | "failed"; late_min: number | null; reason: string | null; status: string; created_at: string };
type Alerts = { enabled: boolean; recent: Alert[] };

const LABEL = { late: "RUNNING LATE", delivered_late: "DELIVERED LATE", failed: "FAILED" };

// Merchant: instant email when one of their drops fails or runs late, plus the latest alerts.
export default function ProblemAlertsCard({ merchantId, notify }: { merchantId: string; notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["merchant-alerts", merchantId], queryFn: () => api<Alerts>(`/merchants/${merchantId}/alerts`), refetchInterval: 30000 });
  const [busy, setBusy] = useState(false);
  const a = q.data;
  if (!a) {
    return q.isError ? (
      <Pressable testID="problem-alerts-retry" onPress={() => q.refetch()} style={s.block}>
        <Mono style={[s.title, { color: colors.brandPrimary }]}>COULD NOT LOAD ALERTS · TAP TO RETRY</Mono>
      </Pressable>
    ) : (
      <View style={s.block}><ActivityIndicator color={colors.onSurface} /></View>
    );
  }

  const toggle = async (enabled: boolean) => {
    setBusy(true);
    try {
      await api(`/merchants/${merchantId}/alerts`, { method: "PUT", body: { enabled } });
      notify(enabled ? "PROBLEM ALERTS ON" : "PROBLEM ALERTS OFF", "green");
      q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.block} testID="problem-alerts-card">
      <View testID="problem-alerts-toggle" style={s.row}>
        <View style={{ flex: 1, gap: 2 }}>
          <Mono style={s.title}>EMAIL ME WHEN A DROP FAILS OR RUNS LATE</Mono>
          <Mono style={s.meta}>One email per drop, as soon as it happens</Mono>
        </View>
        <Switch value={a.enabled} onValueChange={toggle} disabled={busy} trackColor={{ true: colors.brandPrimary, false: colors.surfaceSecondary }} testID="problem-alerts-switch" />
      </View>
      {a.recent.length === 0 ? <Mono style={s.meta} testID="problem-alerts-empty">NO PROBLEM DROPS YET</Mono> : null}
      {a.recent.map((x) => (
        <View key={x.id} style={s.item} testID={`problem-alert-${x.tracking_code}-${x.kind}`}>
          <Mono style={s.tag}>{LABEL[x.kind]}</Mono>
          <Mono style={[s.meta, { flex: 1 }]} numberOfLines={1}>
            {x.tracking_code} · {x.customer_name}{x.late_min ? ` · ~${x.late_min}M` : ""}{x.reason ? ` · ${x.reason}` : ""}
          </Mono>
          <Mono style={s.meta}>{x.status === "sent" ? fmtDepotTime(x.created_at) : x.status.toUpperCase()}</Mono>
        </View>
      ))}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, minHeight: 48 },
  title: { fontSize: 13, fontWeight: "700" },
  meta: { fontSize: 11, color: c.muted },
  item: { flexDirection: "row", alignItems: "center", gap: spacing.sm, minHeight: 32 },
  tag: { fontSize: 10, fontWeight: "700", color: c.brandPrimary, width: 104 },
}));
