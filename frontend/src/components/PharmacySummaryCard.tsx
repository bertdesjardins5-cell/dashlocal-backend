import React, { useState } from "react";
import { Pressable, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, depotToday } from "@/src/api";
import { Btn, Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Summary = {
  week_start: string; week_end: string; drops: number; delivered: number; failed: number; pickups: number;
  pickups_checked_in: number; pickups_on_time: number; on_time_pct: number | null; batch_rate_drops: number;
  savings: number; avg_rating: number | null; ratings: number; top_tags: [string, number][];
};

const fmtWeekDay = (iso: string) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric" }).toUpperCase();

const shift = (iso: string, days: number) => {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
};

// Pharmacy portal: weekly drops, on-time pickups, batch-rate savings and patient feedback (also emailed Mondays).
export default function PharmacySummaryCard({ merchantId, notify }: { merchantId: string; notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [week, setWeek] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const q = useQuery({
    queryKey: ["pharmacy-summary", merchantId, week],
    queryFn: () => api<Summary>(`/merchants/${merchantId}/pharmacy-summary${week ? `?week=${week}` : ""}`),
  });
  const d = q.data;
  const todayIso = depotToday();
  const email = async () => {
    setBusy(true);
    try {
      const r = await api<{ sent_to: string[] }>(`/merchants/${merchantId}/pharmacy-summary/email${d ? `?week=${d.week_start}` : ""}`, { method: "POST" });
      notify(`SUMMARY EMAILED TO ${r.sent_to.join(", ").toUpperCase()}`, "green");
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };
  const stat = (label: string, value: string, id: string, good?: boolean) => (
    <View style={s.stat} testID={id}>
      <Display style={[s.value, good && { color: colors.brandForest }]}>{value}</Display>
      <Mono style={s.meta}>{label}</Mono>
    </View>
  );

  return (
    <View testID="pharmacy-summary-card">
      <Mono style={s.section}>WEEKLY SUMMARY</Mono>
      <View style={s.block}>
        <View style={s.row}>
          <Pressable testID="summary-prev-week" onPress={() => d && setWeek(shift(d.week_start, -7))} style={s.nav}><Mono style={s.navText}>‹</Mono></Pressable>
          <Mono style={[s.label, { flex: 1, textAlign: "center" }]} testID="summary-week-label">
            {d ? `${fmtWeekDay(d.week_start)} – ${fmtWeekDay(d.week_end)}` : "…"}
          </Mono>
          <Pressable testID="summary-next-week" disabled={!week || !d || d.week_end >= todayIso} onPress={() => d && setWeek(shift(d.week_start, 7))} style={[s.nav, (!week || !d || d.week_end >= todayIso) && { opacity: 0.3 }]}><Mono style={s.navText}>›</Mono></Pressable>
        </View>
        {q.error ? <Mono style={s.meta}>{(q.error as Error).message}</Mono> : d ? (
          <>
            <View style={s.grid}>
              {stat("DROPS", String(d.drops), "summary-drops")}
              {stat("ON-TIME PICKUPS", d.on_time_pct === null ? "—" : `${d.on_time_pct}%`, "summary-on-time", (d.on_time_pct ?? 0) >= 90)}
              {stat("BATCH-RATE SAVINGS", `$${d.savings.toFixed(2)}`, "summary-savings", d.savings > 0)}
              {stat("PATIENT RATING", d.avg_rating ? `${d.avg_rating}★` : "—", "summary-rating")}
            </View>
            <Mono style={s.meta}>
              {d.delivered} delivered · {d.failed} not delivered · {d.pickups_on_time}/{d.pickups_checked_in} pickups on time · {d.batch_rate_drops} drops at the batch rate · {d.ratings} rating{d.ratings === 1 ? "" : "s"}
            </Mono>
            {d.top_tags.length ? <Mono style={s.meta} testID="summary-tags">Patients said: {d.top_tags.map(([t, n]) => `${t.toLowerCase()} (${n})`).join(" · ")}</Mono> : null}
            <Btn testID="summary-email-button" label="EMAIL ME THIS SUMMARY" icon="mail" variant="dark" loading={busy} disabled={!d.drops} onPress={email} />
            <Mono style={s.meta}>Sent automatically every Monday morning for the week before.</Mono>
          </>
        ) : <Mono style={s.meta}>Loading…</Mono>}
      </View>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  nav: { width: 44, height: 44, borderWidth: 2, borderColor: c.border, alignItems: "center", justifyContent: "center" },
  navText: { fontSize: 20, fontWeight: "700" },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  stat: { flexBasis: "47%", flexGrow: 1, borderWidth: 2, borderColor: c.border, padding: spacing.md, gap: 2 },
  value: { fontSize: 24 },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 1 },
  meta: { fontSize: 12, color: c.muted },
}));
