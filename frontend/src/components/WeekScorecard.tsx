import React from "react";
import { View } from "react-native";
import { dayLabel } from "@/src/api";
import { Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

export type ScoreDay = {
  date: string;
  delivered: number;
  failed: number;
  on_time_rate: number | null;
  drops_per_hour: number;
  km_driven: number;
  avg_rating: number | null;
  routes: number;
};
export type ScoreTotals = {
  delivered: number;
  failed: number;
  days_worked: number;
  on_time_rate: number | null;
  drops_per_hour: number;
  km_driven: number;
  avg_rating: number | null;
};

// Weekly scorecard: totals strip + daily bar chart (drops) + per-day rows.
export default function WeekScorecard({ days, totals }: { days: ScoreDay[]; totals: ScoreTotals }) {
  const s = useStyles();
  const { colors } = useTheme();
  const max = Math.max(1, ...days.map((d) => d.delivered));
  const cells = [
    { k: "ON-TIME", v: totals.on_time_rate == null ? "—" : `${totals.on_time_rate}%`, id: "week-on-time" },
    { k: "DROPS/HR", v: `${totals.drops_per_hour}`, id: "week-pace" },
    { k: "DROPS", v: `${totals.delivered}`, id: "week-drops" },
    { k: "RATING", v: totals.avg_rating == null ? "—" : `${totals.avg_rating}★`, id: "week-rating" },
  ];
  return (
    <View testID="week-scorecard">
      <View style={s.totals}>
        {cells.map((c, i) => (
          <View key={c.k} style={[s.cell, i > 0 && { borderLeftWidth: 2, borderColor: colors.border }]} testID={c.id}>
            <Display style={s.cellVal}>{c.v}</Display>
            <Mono style={s.label}>{c.k}</Mono>
          </View>
        ))}
      </View>
      <View style={s.chart}>
        {days.map((d, i) => {
          const today = i === days.length - 1;
          return (
            <View key={d.date} style={s.barCol} testID={`week-bar-${i}`}>
              <Mono style={s.barVal}>{d.routes ? d.delivered : ""}</Mono>
              <View style={s.barTrack}>
                <View style={[s.bar, { height: `${(d.delivered / max) * 100}%`, backgroundColor: today ? colors.brandPrimary : colors.surfaceInverse }]} />
              </View>
              <Mono style={[s.barLabel, today && { color: colors.brandPrimary }]}>{dayLabel(d.date).split(" ")[0]}</Mono>
            </View>
          );
        })}
      </View>
      {[...days].reverse().map((d) => (
        <View key={d.date} style={s.row} testID={`week-row-${d.date}`}>
          <Mono style={s.day}>{dayLabel(d.date)}</Mono>
          {d.routes ? (
            <Mono style={s.rowText}>
              {d.delivered} DROPS · {d.on_time_rate == null ? "—" : `${d.on_time_rate}%`} ON-TIME · {d.drops_per_hour}/HR · {d.km_driven.toFixed(1)} KM{d.avg_rating ? ` · ${d.avg_rating}★` : ""}
            </Mono>
          ) : (
            <Mono style={[s.rowText, { color: colors.muted }]}>OFF</Mono>
          )}
        </View>
      ))}
      <Mono style={s.foot}>{totals.days_worked} DAYS WORKED · {totals.km_driven} KM · {totals.failed} FAILED</Mono>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  totals: { flexDirection: "row", borderBottomWidth: 2, borderColor: c.border },
  cell: { flex: 1, padding: spacing.md, alignItems: "center", gap: 2 },
  cellVal: { fontSize: 22 },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.muted },
  chart: { flexDirection: "row", height: 140, paddingHorizontal: spacing.lg, paddingTop: spacing.md, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  barCol: { flex: 1, alignItems: "center", gap: 4, paddingBottom: spacing.sm },
  barVal: { fontSize: 11, fontWeight: "700", height: 14 },
  barTrack: { flex: 1, width: "100%", justifyContent: "flex-end", backgroundColor: c.surfaceSecondary },
  bar: { width: "100%" },
  barLabel: { fontSize: 10, fontWeight: "700" },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.lg, minHeight: 44, borderBottomWidth: 1, borderColor: c.divider },
  day: { width: 64, fontSize: 11, fontWeight: "700" },
  rowText: { flex: 1, fontSize: 11 },
  foot: { padding: spacing.lg, fontSize: 11, color: c.muted },
}));
