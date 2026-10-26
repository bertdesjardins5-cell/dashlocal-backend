import React from "react";
import { View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, dayLabel } from "@/src/api";
import { Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Feedback = {
  count: number; avg: number | null; avg_30d: number | null; count_30d: number; stars: Record<string, number>;
  compliments: [string, number][]; issues: [string, number][]; recent: { stars: number; tags: string[]; rated_at: string | null }[];
};

// Driver: their own patient ratings and top compliments (no patient names or addresses).
export default function DriverFeedbackCard({ driverId }: { driverId: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["driver-feedback", driverId], queryFn: () => api<Feedback>(`/drivers/${driverId}/feedback`) });
  const d = q.data;
  if (!d) return null;
  const max = Math.max(1, ...Object.values(d.stars));
  return (
    <View testID="driver-feedback-card">
      <Mono style={s.section}>PATIENT FEEDBACK</Mono>
      <View style={s.block}>
        {!d.count ? <Mono style={s.meta}>No ratings yet — they appear here after patients rate a delivery.</Mono> : (
          <>
            <View style={s.row}>
              <View style={{ flex: 1 }}>
                <Display style={s.big} testID="driver-feedback-avg">{d.avg?.toFixed(1)}★</Display>
                <Mono style={s.meta}>{d.count} rating{d.count === 1 ? "" : "s"} all time</Mono>
              </View>
              <View style={{ flex: 1 }}>
                <Display style={s.big} testID="driver-feedback-30d">{d.avg_30d ? `${d.avg_30d.toFixed(1)}★` : "—"}</Display>
                <Mono style={s.meta}>last 30 days · {d.count_30d}</Mono>
              </View>
            </View>
            {["5", "4", "3", "2", "1"].map((k) => (
              <View key={k} style={s.row}>
                <Mono style={[s.meta, { width: 22 }]}>{k}★</Mono>
                <View style={s.track}><View style={{ width: `${(d.stars[k] / max) * 100}%`, height: "100%", backgroundColor: Number(k) >= 4 ? colors.brandForest : colors.brandPrimary }} /></View>
                <Mono style={[s.meta, { width: 28, textAlign: "right" }]}>{d.stars[k]}</Mono>
              </View>
            ))}
            {d.compliments.length ? (
              <View style={{ gap: spacing.xs }} testID="driver-compliments">
                <Mono style={s.label}>TOP COMPLIMENTS</Mono>
                <View style={s.wrap}>
                  {d.compliments.map(([t, n]) => <Mono key={t} style={[s.chip, { borderColor: colors.brandForest }]}>{t} · {n}</Mono>)}
                </View>
              </View>
            ) : null}
            {d.issues.length ? (
              <View style={{ gap: spacing.xs }} testID="driver-issues">
                <Mono style={s.label}>TO WORK ON</Mono>
                <View style={s.wrap}>{d.issues.map(([t, n]) => <Mono key={t} style={s.chip}>{t} · {n}</Mono>)}</View>
              </View>
            ) : null}
            <Mono style={s.label}>RECENT</Mono>
            {d.recent.map((r, i) => (
              <Mono key={i} style={s.meta} testID="driver-feedback-recent">
                {"★".repeat(r.stars)}{"☆".repeat(5 - r.stars)} {r.rated_at ? dayLabel(new Date(r.rated_at).toLocaleDateString("en-CA", { timeZone: "America/Toronto" })) : ""}{r.tags.length ? ` · ${r.tags.join(", ").toLowerCase()}` : ""}
              </Mono>
            ))}
          </>
        )}
      </View>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderTopWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  block: { padding: spacing.lg, gap: spacing.sm },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  big: { fontSize: 32 },
  track: { flex: 1, height: 10, borderWidth: 2, borderColor: c.border, overflow: "hidden" },
  wrap: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  chip: { fontSize: 11, fontWeight: "700", borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.sm, paddingVertical: 6 },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 1, marginTop: spacing.sm },
  meta: { fontSize: 12, color: c.muted },
}));
