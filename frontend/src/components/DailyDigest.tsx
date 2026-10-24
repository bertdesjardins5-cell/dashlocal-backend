import React, { useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { addDays, api, dayLabel, depotToday, fmtDepotTime } from "@/src/api";
import { Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Digest = {
  date: string; routes: number; total_stops: number; delivered: number; failed: number; remaining: number;
  drops_per_hour: number; late_count: number; on_time_rate: number | null; complaint_count: number; avg_rating: number | null;
  hourly: { hour: number; count: number }[];
  late: { stop_id: string; tracking_code: string; customer_name: string; driver: string; window: string; delivered_at: string; late_min: number }[];
  failed_stops: { stop_id: string; tracking_code: string; customer_name: string; driver: string; reason: string | null }[];
  complaints: { stop_id: string; tracking_code: string; customer_name: string; driver: string; rating: number; comment: string | null; follow_up_status: string | null }[];
  drivers: { driver_id: string; name: string; delivered: number; failed: number; late: number; complaints: number; drops_per_hour: number }[];
};

const LIMIT = 5;

// Admin: end-of-day summary for one depot day (today or the last 6 days).
export default function DailyDigest() {
  const s = useStyles();
  const { colors } = useTheme();
  const today = depotToday();
  const [day, setDay] = useState(today);
  const days = Array.from({ length: 7 }, (_, i) => addDays(today, -i));
  const q = useQuery({ queryKey: ["digest", day], queryFn: () => api<Digest>(`/digest?date=${day}`), refetchInterval: day === today ? 30000 : false });
  const d = q.data;
  const maxHour = Math.max(1, ...(d?.hourly ?? []).map((h) => h.count));

  const list = <T,>(title: string, rows: T[], id: string, render: (r: T) => { key: string; main: string; sub: string; tag: string }) =>
    rows.length ? (
      <View style={s.block} testID={`digest-${id}`}>
        <Mono style={s.blockTitle}>{title} · {rows.length}</Mono>
        {rows.slice(0, LIMIT).map((r) => {
          const x = render(r);
          return (
            <View key={x.key} style={s.item} testID={`digest-${id}-${x.key}`}>
              <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                <Display style={s.itemMain} numberOfLines={1}>{x.main}</Display>
                <Mono style={s.meta} numberOfLines={2}>{x.sub}</Mono>
              </View>
              <Mono style={s.tag}>{x.tag}</Mono>
            </View>
          );
        })}
        {rows.length > LIMIT ? <Mono style={s.meta}>+{rows.length - LIMIT} MORE</Mono> : null}
      </View>
    ) : null;

  return (
    <View testID="daily-digest">
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={s.days} contentContainerStyle={s.daysInner}>
        {days.map((x, i) => (
          <Pressable key={x} testID={`digest-day-${i}`} onPress={() => setDay(x)} style={[s.chip, x === day && { backgroundColor: colors.surfaceInverse }]}>
            <Mono style={[s.chipText, x === day && { color: colors.onSurfaceInverse }]}>{i === 0 ? "TODAY" : i === 1 ? "YDAY" : dayLabel(x)}</Mono>
          </Pressable>
        ))}
      </ScrollView>
      {!d ? (
        q.isError ? (
          <Pressable testID="digest-retry" onPress={() => q.refetch()} style={s.block}>
            <Mono style={[s.blockTitle, { color: colors.brandPrimary }]}>COULD NOT LOAD DIGEST · TAP TO RETRY</Mono>
          </Pressable>
        ) : (
          <View style={s.block} testID="digest-loading"><ActivityIndicator color={colors.onSurface} /></View>
        )
      ) : d.total_stops === 0 ? (
        <Mono style={s.empty} testID="digest-empty">NO ROUTES RAN ON {dayLabel(d.date)}</Mono>
      ) : (
        <>
          <View style={s.totals}>
            {[
              { k: "DROPS/HR", v: String(d.drops_per_hour), id: "digest-dph", hot: false },
              { k: "DELIVERED", v: `${d.delivered}/${d.total_stops}`, id: "digest-delivered", hot: false },
              { k: "LATE", v: String(d.late_count), id: "digest-late-count", hot: d.late_count > 0 },
              { k: "COMPLAINTS", v: String(d.complaint_count), id: "digest-complaint-count", hot: d.complaint_count > 0 },
            ].map((c, i) => (
              <View key={c.k} style={[s.cell, i > 0 && { borderLeftWidth: 2, borderColor: colors.border }]} testID={c.id}>
                <Display style={[s.cellVal, c.hot && { color: colors.brandPrimary }]} numberOfLines={1}>{c.v}</Display>
                <Mono style={s.label}>{c.k}</Mono>
              </View>
            ))}
          </View>
          <Mono style={s.note} testID="digest-note">
            {d.routes} ROUTE{d.routes === 1 ? "" : "S"} · {d.failed} FAILED · {d.remaining} LEFT · ON-TIME {d.on_time_rate == null ? "—" : `${d.on_time_rate}%`} · {d.avg_rating ? `${d.avg_rating}★` : "NO RATINGS"}
          </Mono>
          {d.hourly.length ? (
            <View style={s.block} testID="digest-hourly">
              <Mono style={s.blockTitle}>DROPS BY HOUR</Mono>
              <View style={s.chart}>
                {d.hourly.map((h) => (
                  <View key={h.hour} style={s.col} testID={`digest-hour-${h.hour}`}>
                    <Mono style={s.colVal}>{h.count || ""}</Mono>
                    <View style={[s.colBar, { height: 4 + (h.count / maxHour) * 56, backgroundColor: h.count === maxHour ? colors.brandPrimary : colors.surfaceInverse }]} />
                    <Mono style={s.colLabel}>{String(h.hour).padStart(2, "0")}</Mono>
                  </View>
                ))}
              </View>
            </View>
          ) : null}
          {list("LATE DELIVERIES", d.late, "late", (r) => ({
            key: r.tracking_code, main: r.customer_name, sub: `${r.tracking_code} · ${r.driver} · WINDOW ${r.window} · DROPPED ${fmtDepotTime(r.delivered_at)}`, tag: `+${r.late_min}M`,
          }))}
          {list("COMPLAINTS", d.complaints, "complaints", (r) => ({
            key: r.tracking_code, main: r.customer_name, sub: `${r.tracking_code} · ${r.driver}${r.comment ? ` · “${r.comment}”` : ""}${r.follow_up_status === "resolved" ? " · RESOLVED" : ""}`, tag: `${r.rating}★`,
          }))}
          {list("FAILED DROPS", d.failed_stops, "failed", (r) => ({
            key: r.tracking_code, main: r.customer_name, sub: `${r.tracking_code} · ${r.driver}`, tag: (r.reason ?? "FAILED").toUpperCase().slice(0, 14),
          }))}
          <View style={s.block} testID="digest-drivers">
            <Mono style={s.blockTitle}>BY DRIVER</Mono>
            {d.drivers.map((x) => (
              <View key={x.driver_id ?? x.name} style={s.item} testID={`digest-driver-${x.driver_id}`}>
                <Display style={[s.itemMain, { flex: 1 }]} numberOfLines={1}>{x.name}</Display>
                <Mono style={s.meta}>{x.drops_per_hour}/HR · {x.delivered} DROPS · {x.late} LATE · {x.complaints} BAD</Mono>
              </View>
            ))}
          </View>
        </>
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  days: { height: 60, flexGrow: 0, borderBottomWidth: 2, borderColor: c.border },
  daysInner: { gap: spacing.sm, alignItems: "center", paddingHorizontal: spacing.lg },
  chip: { height: 44, flexShrink: 0, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 12, fontWeight: "700" },
  empty: { padding: spacing.lg, fontSize: 13, color: c.muted, borderBottomWidth: 2, borderColor: c.border },
  totals: { flexDirection: "row", borderBottomWidth: 2, borderColor: c.border },
  cell: { flex: 1, padding: spacing.md, alignItems: "center", gap: 2 },
  cellVal: { fontSize: 22 },
  label: { fontSize: 9, fontWeight: "700", letterSpacing: 1, color: c.muted },
  note: { fontSize: 11, paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  block: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  blockTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5, color: c.muted },
  chart: { flexDirection: "row", alignItems: "flex-end", gap: spacing.sm },
  col: { flex: 1, alignItems: "center", gap: 2, maxWidth: 40 },
  colVal: { fontSize: 10, fontWeight: "700" },
  colBar: { width: "100%" },
  colLabel: { fontSize: 10, color: c.muted },
  item: { flexDirection: "row", alignItems: "center", gap: spacing.md, minHeight: 44 },
  itemMain: { fontSize: 15 },
  meta: { fontSize: 11, color: c.muted },
  tag: { fontSize: 12, fontWeight: "700", color: c.brandPrimary },
}));
