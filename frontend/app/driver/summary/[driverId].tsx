import React from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeInDown } from "react-native-reanimated";
import { api, dayLabel, Driver, fmtDepotTime } from "@/src/api";
import { Display, ErrorBlock, Header, Loading, Mono, Pill, statusTone } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";
import WeekScorecard, { ScoreDay, ScoreTotals } from "@/src/components/WeekScorecard";
import Badges, { Badge } from "@/src/components/Badges";

type Summary = {
  driver: Driver;
  date: string;
  routes: { id: string; name: string; status: string }[];
  all_completed: boolean;
  total_stops: number;
  delivered: number;
  failed: number;
  remaining: number;
  on_time: number;
  on_time_rate: number | null;
  windowed_delivered: number;
  windowed_on_time: number;
  km_driven: number;
  planned_km: number;
  hours: number;
  drops_per_hour: number;
  geofence_arrivals: number;
  avg_rating: number | null;
  ratings: number;
  pod: { photo: number; signature: number; qr: number };
  stops: { seq: number; route_id: string; customer_name: string; tracking_code: string; status: string; delivered_at: string | null; window: string | null; on_time: boolean | null; fail_reason: string | null }[];
};

export default function DriverSummary() {
  const { driverId } = useLocalSearchParams<{ driverId: string }>();
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["driver-summary", driverId], queryFn: () => api<Summary>(`/drivers/${driverId}/summary`) });
  const week = useQuery({ queryKey: ["driver-scorecard", driverId], queryFn: () => api<{ days: ScoreDay[]; totals: ScoreTotals; badges: Badge[]; rank: number | null }>(`/drivers/${driverId}/scorecard`) });
  const d = q.data;
  const hrs = d ? `${Math.floor(d.hours)}H ${String(Math.round((d.hours % 1) * 60)).padStart(2, "0")}M` : "";

  const kpis = d
    ? [
        { k: "DROPS", v: `${d.delivered}`, sub: `${d.failed} FAILED · ${d.remaining} LEFT`, id: "summary-drops" },
        { k: "ON-TIME", v: d.on_time_rate == null ? "—" : `${d.on_time_rate}%`, sub: `${d.windowed_on_time}/${d.windowed_delivered} WINDOWS HIT`, id: "summary-on-time" },
        { k: "KM DRIVEN", v: d.km_driven.toFixed(1), sub: `${d.planned_km.toFixed(1)} KM PLANNED`, id: "summary-km" },
        { k: "DROPS/HR", v: `${d.drops_per_hour}`, sub: `${hrs} ON ROUTE`, id: "summary-pace" },
        { k: "RATING", v: d.avg_rating == null ? "—" : `${d.avg_rating}★`, sub: `${d.ratings} CUSTOMER RATINGS`, id: "summary-rating" },
        { k: "GEOFENCE", v: `${d.geofence_arrivals}`, sub: "AUTO ARRIVALS", id: "summary-geofence" },
      ]
    : [];

  return (
    <View style={s.root} testID="driver-summary-screen">
      <Header title="DAY SUMMARY" subtitle={d ? `${d.driver.name} · ${dayLabel(d.date)}` : undefined} />
      {q.isLoading ? <Loading /> : q.error || !d ? <ErrorBlock message={(q.error as Error)?.message ?? "Not found"} onRetry={() => { q.refetch(); }} /> : (
        <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); week.refetch(); }} />}>
          <View style={[s.hero, d.all_completed && { backgroundColor: colors.success }]} testID="summary-hero">
            <Mono style={s.heroKicker}>{d.routes.length ? d.routes.map((r) => r.name).join(" · ") : "NO ROUTES STARTED TODAY"}</Mono>
            <Display style={s.heroTitle}>{d.all_completed ? "SHIFT\nCOMPLETE" : d.routes.length ? "SHIFT IN\nPROGRESS" : "NOTHING\nYET"}</Display>
          </View>
          <View style={s.grid}>
            {kpis.map((x, i) => (
              <Animated.View key={x.k} entering={FadeInDown.delay(60 * i)} style={[s.kpi, i % 2 === 0 && { borderRightWidth: 2, borderColor: colors.border }]} testID={x.id}>
                <Mono style={s.kpiLabel}>{x.k}</Mono>
                <Display style={s.kpiVal}>{x.v}</Display>
                <Mono style={s.kpiSub}>{x.sub}</Mono>
              </Animated.View>
            ))}
          </View>
          <View style={s.podRow} testID="summary-pod">
            {[
              ["GEOFENCE", d.geofence_arrivals],
              ["PHOTO", d.pod.photo],
              ["SIGN", d.pod.signature],
              ["QR", d.pod.qr],
            ].map(([k, v], i) => (
              <View key={k as string} style={[s.podCell, i > 0 && { borderLeftWidth: 2, borderColor: colors.border }]}>
                <Display style={{ fontSize: 22 }}>{v}</Display>
                <Mono style={s.kpiLabel}>{k}</Mono>
              </View>
            ))}
          </View>
          <Mono style={s.section}>THIS WEEK · LAST 7 DAYS{week.data?.rank ? ` · RANK #${week.data.rank}` : ""}</Mono>
          {week.data?.badges?.length ? (
            <View style={{ padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: colors.border }} testID="week-badges">
              <Mono style={{ fontSize: 11, fontWeight: "700", letterSpacing: 1 }}>YOUR BADGES THIS WEEK</Mono>
              <Badges badges={week.data.badges} testIDPrefix="week-badge" />
            </View>
          ) : null}
          {week.data ? <WeekScorecard days={week.data.days} totals={week.data.totals} /> : <Loading testID="week-loading" />}
          <Mono style={s.section}>DROPS · {d.total_stops}</Mono>
          {d.stops.map((x) => (
            <View key={`${x.route_id}-${x.tracking_code}`} style={s.row} testID={`summary-stop-${x.tracking_code}`}>
              <Display style={s.seq}>{String(x.seq).padStart(2, "0")}</Display>
              <View style={{ flex: 1, gap: 2 }}>
                <Display style={{ fontSize: 16 }} numberOfLines={1}>{x.customer_name}</Display>
                <Mono style={s.meta}>
                  {x.tracking_code}
                  {x.delivered_at ? ` · ${fmtDepotTime(x.delivered_at)}` : ""}
                  {x.window ? ` · WIN ${x.window}` : ""}
                  {x.fail_reason ? ` · ${x.fail_reason.toUpperCase()}` : ""}
                </Mono>
              </View>
              <View style={{ gap: 4, alignItems: "flex-end" }}>
                <Pill label={x.status} tone={statusTone(x.status)} />
                {x.on_time != null ? <Pill label={x.on_time ? "ON TIME" : "LATE"} tone={x.on_time ? "green" : "red"} /> : null}
              </View>
            </View>
          ))}
        </ScrollView>
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  hero: { backgroundColor: c.surfaceInverse, padding: spacing.xl, gap: spacing.sm },
  heroKicker: { color: c.onSurfaceInverse, fontSize: 12, fontWeight: "700", letterSpacing: 1 },
  heroTitle: { color: c.onSurfaceInverse, fontSize: 40, lineHeight: 42, letterSpacing: -1 },
  grid: { flexDirection: "row", flexWrap: "wrap", borderTopWidth: 2, borderColor: c.border },
  kpi: { width: "50%", padding: spacing.lg, gap: spacing.xs, borderBottomWidth: 2, borderColor: c.border },
  kpiLabel: { fontSize: 11, fontWeight: "700", letterSpacing: 1, color: c.muted },
  kpiVal: { fontSize: 36, letterSpacing: -1 },
  kpiSub: { fontSize: 11, color: c.onSurface },
  podRow: { flexDirection: "row", borderBottomWidth: 2, borderColor: c.border },
  podCell: { flex: 1, padding: spacing.md, alignItems: "center", gap: 2 },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderBottomWidth: 2, borderColor: c.border, minHeight: 64 },
  seq: { fontSize: 20, width: 32 },
  meta: { fontSize: 11, color: c.muted },
}));
