import React, { useState } from "react";
import { ActivityIndicator, Linking, Platform, Pressable, ScrollView, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, BACKEND_URL, dayLabel } from "@/src/api";
import { Btn, Display, Mono, Pill } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

export const money = (n: number) => `CA$${n.toFixed(2)}`;

type Line = { stop_id: string; tracking_code: string; customer_name: string; day: string; status: string; priority: boolean; km: number; base: number; distance_fee: number; priority_fee: number; total: number };
type Bill = {
  week_start: string; week_end: string; current_week: boolean; tax_pct: number; lines: Line[]; drops: number;
  base_total: number; distance_total: number; priority_total: number; subtotal: number; tax: number; total: number;
  open_orders: { count: number; estimate: number }; weeks: string[];
  payment: { paid_at: string; note: string | null } | null; can_mark_paid: boolean;
  outstanding: { total: number; weeks: { week_start: string; total: number }[] };
};

const LIMIT = 8;

// Merchant: running bill for this week + weekly statements (paid outside the app).
export default function BillCard({ merchantId, notify }: { merchantId: string; notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [week, setWeek] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const q = useQuery({ queryKey: ["bill", merchantId, week], queryFn: () => api<Bill>(`/merchants/${merchantId}/bill${week ? `?week=${week}` : ""}`), refetchInterval: 30000 });
  const b = q.data;

  const download = async () => {
    setBusy(true);
    try {
      const r = await api<{ path: string }>(`/merchants/${merchantId}/statement-link`, { method: "POST", body: { week: b?.week_start } });
      const url = `${BACKEND_URL}${r.path}`;
      if (Platform.OS === "web") window.location.href = url; // attachment download: page stays put, no blank tab
      else await Linking.openURL(url);
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  if (!b) {
    return q.isError ? (
      <Pressable testID="bill-retry" onPress={() => q.refetch()} style={s.block}><Mono style={[s.title, { color: colors.brandPrimary }]}>COULD NOT LOAD BILL · TAP TO RETRY</Mono></Pressable>
    ) : <View style={s.block}><ActivityIndicator color={colors.onSurface} /></View>;
  }

  return (
    <View testID="bill-card">
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={s.weeks} contentContainerStyle={s.weeksInner}>
        {b.weeks.map((w, i) => (
          <Pressable key={w} testID={`bill-week-${i}`} onPress={() => { setAll(false); setWeek(i === 0 ? null : w); }} style={[s.chip, b.week_start === w && { backgroundColor: colors.surfaceInverse }]}>
            <Mono style={[s.chipText, b.week_start === w && { color: colors.onSurfaceInverse }]}>{i === 0 ? "THIS WEEK" : `WK ${dayLabel(w)}`}</Mono>
          </Pressable>
        ))}
      </ScrollView>
      <View style={s.block}>
        {b.outstanding.total > 0 ? (
          <View style={s.owed} testID="bill-outstanding">
            <Mono style={s.owedText}>TOTAL DUE {money(b.outstanding.total)}</Mono>
            <Mono style={s.owedSub}>{b.outstanding.weeks.length} UNPAID WEEK{b.outstanding.weeks.length === 1 ? "" : "S"}: {b.outstanding.weeks.map((w) => dayLabel(w.week_start)).join(", ")}</Mono>
          </View>
        ) : null}
        <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }}>
          <Mono style={[s.meta, { flex: 1 }]}>{b.current_week ? "RUNNING BILL · " : "STATEMENT · "}{dayLabel(b.week_start)} – {dayLabel(b.week_end)}</Mono>
          {b.payment ? <Pill label={`PAID ${dayLabel(b.payment.paid_at.slice(0, 10))}`} tone="green" /> : b.can_mark_paid && b.total > 0 ? <Pill label="UNPAID" tone="red" /> : null}
        </View>
        <Display style={s.total} testID="bill-total">{money(b.total)}</Display>
        <Mono style={s.meta} testID="bill-breakdown">
          {b.drops} DROPS · FEES {money(b.base_total)} · DISTANCE {money(b.distance_total)} · PRIORITY {money(b.priority_total)} · TAX {b.tax_pct}% {money(b.tax)}
        </Mono>
        {b.current_week && b.open_orders.count ? (
          <Mono style={s.meta} testID="bill-open">+ {b.open_orders.count} OPEN ORDERS ≈ {money(b.open_orders.estimate)} WHEN COMPLETED</Mono>
        ) : null}
        {b.lines.slice(0, all ? undefined : LIMIT).map((x) => (
          <View key={x.stop_id} style={s.line} testID={`bill-line-${x.tracking_code}`}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Mono style={s.lineMain} numberOfLines={1}>{x.tracking_code} · {x.customer_name}</Mono>
              <Mono style={s.meta} numberOfLines={1}>{dayLabel(x.day)} · {x.km} KM{x.priority ? " · PRIORITY" : ""}{x.status === "failed" ? " · FAILED" : ""}</Mono>
            </View>
            <Mono style={s.lineMain}>{money(x.total)}</Mono>
          </View>
        ))}
        {b.lines.length > LIMIT && !all ? (
          <Pressable testID="bill-show-all" onPress={() => setAll(true)} style={s.more}><Mono style={s.chipText}>SHOW ALL {b.lines.length}</Mono></Pressable>
        ) : null}
        {b.lines.length === 0 ? <Mono style={s.meta}>NO COMPLETED DROPS THIS WEEK</Mono> : null}
        <Btn testID="bill-download" label="DOWNLOAD STATEMENT" icon="document-text" variant="outline" loading={busy} onPress={download} />
        <Mono style={s.meta}>Bills run Monday–Sunday (Eastern time) and are emailed with your Monday report. Payment is arranged directly with dispatch.</Mono>
      </View>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  weeks: { height: 60, flexGrow: 0, borderBottomWidth: 2, borderColor: c.border },
  weeksInner: { gap: spacing.sm, alignItems: "center", paddingHorizontal: spacing.lg },
  chip: { height: 44, flexShrink: 0, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 12, fontWeight: "700" },
  block: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  title: { fontSize: 13, fontWeight: "700" },
  total: { fontSize: 32 },
  meta: { fontSize: 11, color: c.muted },
  line: { flexDirection: "row", alignItems: "center", gap: spacing.md, minHeight: 44, borderTopWidth: 1, borderColor: c.surfaceSecondary },
  lineMain: { fontSize: 13, fontWeight: "700" },
  more: { minHeight: 44, justifyContent: "center", alignItems: "center", borderWidth: 2, borderColor: c.border },
  owed: { backgroundColor: c.brandPrimary, padding: spacing.md, gap: 2 },
  owedText: { color: c.onBrandPrimary, fontSize: 15, fontWeight: "700" },
  owedSub: { color: c.onBrandPrimary, fontSize: 11 },
}));
