import React from "react";
import { ActivityIndicator, Pressable, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, dayLabel } from "@/src/api";
import { Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Group = { key: string; label: string; ratings: number; low: number; low_pct: number; top_reasons: { reason: string; count: number }[] };
type Trends = { days: number; total_ratings: number; total_low: number; by_driver: Group[]; by_zone: Group[]; daily: { date: string; low: number }[] };

// Admin coaching view: where low ratings cluster this week (drivers, zones, reasons).
export default function AlertTrends() {
  const s = useStyles();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["alert-trends"], queryFn: () => api<Trends>("/alerts/trends?days=7"), refetchInterval: 30000 });
  const t = q.data;
  if (!t) {
    return q.isError ? (
      <Pressable testID="alert-trends-retry" onPress={() => q.refetch()} style={{ padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border }}>
        <Mono style={{ fontSize: 12, fontWeight: "700", color: colors.brandPrimary }}>COULD NOT LOAD TRENDS · TAP TO RETRY</Mono>
      </Pressable>
    ) : (
      <View testID="alert-trends-loading" style={{ padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border }}>
        <ActivityIndicator color={colors.onSurface} />
      </View>
    );
  }
  const maxDaily = Math.max(1, ...t.daily.map((d) => d.low));

  const table = (title: string, rows: Group[], id: string) => {
    const max = Math.max(1, ...rows.map((r) => r.low));
    return (
      <View style={s.block} testID={`trends-${id}`}>
        <Mono style={s.blockTitle}>{title}</Mono>
        {rows.map((r, i) => (
          <View key={r.key} style={s.row} testID={`trends-${id}-${r.key}`}>
            <View style={s.rowTop}>
              <Display style={[s.name, i === 0 && r.low > 0 && { color: colors.brandPrimary }]} numberOfLines={1}>{r.label}</Display>
              <Mono style={s.count}>{r.low} LOW / {r.ratings} · {r.low_pct}%</Mono>
            </View>
            <View style={s.track}>
              <View style={[s.bar, { width: `${(r.low / max) * 100}%`, backgroundColor: i === 0 && r.low > 0 ? colors.brandPrimary : colors.surfaceInverse }]} />
            </View>
            {r.top_reasons.length ? <Mono style={s.reasons} numberOfLines={1}>{r.top_reasons.map((x) => `${x.reason} ×${x.count}`).join(" · ")}</Mono> : null}
          </View>
        ))}
      </View>
    );
  };

  return (
    <View testID="alert-trends">
      <View style={s.summary}>
        <Display style={s.big} testID="trends-total-low">{t.total_low}</Display>
        <Mono style={s.summaryText}>LOW RATINGS OF {t.total_ratings} IN THE LAST {t.days} DAYS</Mono>
        <View style={s.spark}>
          {t.daily.map((d) => (
            <View key={d.date} style={{ alignItems: "center", gap: 2 }}>
              <View style={[s.sparkBar, { height: 4 + (d.low / maxDaily) * 28 }]} />
              <Mono style={s.sparkLabel}>{dayLabel(d.date).slice(0, 2)}</Mono>
            </View>
          ))}
        </View>
      </View>
      {table("BY DRIVER", t.by_driver, "driver")}
      {table("BY ZONE", t.by_zone, "zone")}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  summary: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.lg, borderBottomWidth: 2, borderColor: c.border },
  big: { fontSize: 36, color: c.brandPrimary },
  summaryText: { flex: 1, fontSize: 11, fontWeight: "700" },
  spark: { flexDirection: "row", alignItems: "flex-end", gap: 3 },
  sparkBar: { width: 8, backgroundColor: c.brandPrimary },
  sparkLabel: { fontSize: 10, fontWeight: "700", color: c.onSurface },
  block: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  blockTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5, color: c.muted },
  row: { gap: 4 },
  rowTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: spacing.sm },
  name: { fontSize: 15, flex: 1 },
  count: { fontSize: 11, fontWeight: "700" },
  track: { height: 8, backgroundColor: c.surfaceSecondary },
  bar: { height: 8 },
  reasons: { fontSize: 10, color: c.muted },
}));
