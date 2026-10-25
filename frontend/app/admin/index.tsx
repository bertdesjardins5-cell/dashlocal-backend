import React, { useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { addDays, api, dayLabel, depotToday, Driver } from "@/src/api";
import LowRatingAlerts from "@/src/components/LowRatingAlerts";
import AlertTrends from "@/src/components/AlertTrends";
import ApologyCodes from "@/src/components/ApologyCodes";
import DailyDigest from "@/src/components/DailyDigest";
import { clearSession } from "@/src/session";
import Badges, { Badge } from "@/src/components/Badges";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, statusTone, useToast } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Zone = { zone: string; postal_code: string; count: number };
type RouteRow = { id: string; name: string; status: string; total: number; completed: number; driver: Driver | null; planned_distance_m: number; service_date: string };
type Analytics = {
  drops_per_hour: number; avg_deviation_pct: number; delivered: number; failed: number; total_orders: number; unassigned: number;
  success_rate: number; geofence_arrival_pct: number; active_routes: number; avg_rating: number | null;
  routes: { id: string; name: string; drops_per_hour: number; deviation_pct: number | null; planned_km: number; actual_km: number }[];
};

export default function AdminDashboard() {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const [assign, setAssign] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const [day, setDay] = useState(depotToday());
  const days = Array.from({ length: 7 }, (_, i) => addDays(depotToday(), i));
  const an = useQuery({ queryKey: ["analytics"], queryFn: () => api<Analytics>("/analytics"), refetchInterval: 10000 });
  const zones = useQuery({ queryKey: ["zones", day], queryFn: () => api<Zone[]>(`/zones?date=${day}`) });
  const routes = useQuery({ queryKey: ["routes"], queryFn: () => api<RouteRow[]>("/routes"), refetchInterval: 10000 });
  const drivers = useQuery({ queryKey: ["drivers"], queryFn: () => api<(Driver & { route: any })[]>("/drivers") });
  const cards = useQuery({
    queryKey: ["scorecards"],
    queryFn: () => api<{ driver: Driver; on_time_rate: number | null; drops_per_hour: number; delivered: number; avg_rating: number | null; trend: number[]; badges: Badge[] }[]>("/scorecards"),
  });

  const refresh = () => { an.refetch(); zones.refetch(); routes.refetch(); drivers.refetch(); cards.refetch(); };

  const batch = async (zone: string) => {
    const driver_id = assign[zone];
    if (!driver_id) return toast.show("PICK A DRIVER FIRST", "red");
    setBusy(zone);
    try {
      const r = await api<{ id: string; name: string }>("/routes/batch", { method: "POST", body: { zone, driver_id, service_date: day } });
      toast.show(`${r.name} CREATED & OPTIMIZED`, "green");
      refresh();
      router.push(`/admin/route/${r.id}`);
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  const reset = async () => {
    try {
      await api("/admin/reset", { method: "POST" });
      toast.show("DEMO DATA RESET");
      refresh();
    } catch (e: any) {
      toast.show(e.message, "red");
    }
  };

  const kpis = an.data ? [
    { k: "DROPS/HR", v: an.data.drops_per_hour, id: "kpi-drops-per-hour" },
    { k: "AVG DEVIATION", v: `${an.data.avg_deviation_pct}%`, id: "kpi-deviation" },
    { k: "DELIVERED", v: `${an.data.delivered}/${an.data.total_orders}`, id: "kpi-delivered" },
    { k: "SUCCESS", v: `${an.data.success_rate}%`, id: "kpi-success" },
    { k: "GEOFENCE ARRIVALS", v: `${an.data.geofence_arrival_pct}%`, id: "kpi-geofence" },
    { k: "UNASSIGNED", v: an.data.unassigned, id: "kpi-unassigned" },
    { k: "AVG RATING", v: an.data.avg_rating == null ? "—" : `${an.data.avg_rating}★`, id: "kpi-rating" },
  ] : [];

  return (
    <View style={s.root} testID="admin-dashboard-screen">
      <Header title="CONTROL CENTER" subtitle="Dispatch · Batching · Analytics" right={
        <View style={{ flexDirection: "row", gap: spacing.sm }}>
          <Pressable testID="admin-reset-button" onPress={reset} style={s.iconBtn}><Ionicons name="refresh" size={22} color={colors.onSurfaceInverse} /></Pressable>
          <Pressable testID="admin-sign-out-button" onPress={() => clearSession()} style={s.iconBtn}><Ionicons name="log-out" size={22} color={colors.onSurfaceInverse} /></Pressable>
        </View>
      } />
      {an.isLoading ? <Loading /> : an.error ? <ErrorBlock message={(an.error as Error).message} onRetry={refresh} /> : (
        <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} refreshControl={<RefreshControl refreshing={false} onRefresh={refresh} />}>
          <Pressable testID="admin-merchant-accounts-link" onPress={() => router.push("/admin/merchants")} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }]}>
            <Ionicons name="storefront" size={24} color={colors.onSurface} />
            <View style={{ flex: 1, gap: 2 }}>
              <Display style={s.rowTitle}>MERCHANT ACCOUNTS</Display>
              <Mono style={s.meta}>Create store logins · reset passwords · admin sign-in</Mono>
            </View>
            <Ionicons name="chevron-forward" size={22} color={colors.onSurface} />
          </Pressable>
          <Pressable testID="admin-driver-accounts-link" onPress={() => router.push("/admin/drivers")} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }]}>
            <Ionicons name="key" size={24} color={colors.onSurface} />
            <View style={{ flex: 1, gap: 2 }}>
              <Display style={s.rowTitle}>DRIVER PINS</Display>
              <Mono style={s.meta}>Set or reset each driver's 6-digit sign-in PIN</Mono>
            </View>
            <Ionicons name="chevron-forward" size={22} color={colors.onSurface} />
          </Pressable>
          <Pressable testID="admin-pricing-link" onPress={() => router.push("/admin/pricing")} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }]}>
            <Ionicons name="pricetag" size={24} color={colors.onSurface} />
            <View style={{ flex: 1, gap: 2 }}>
              <Display style={s.rowTitle}>PRICING & BILLING</Display>
              <Mono style={s.meta}>Per-drop fee · distance · priority surcharge · weekly bills</Mono>
            </View>
            <Ionicons name="chevron-forward" size={22} color={colors.onSurface} />
          </Pressable>
          <Mono style={s.section}>DAILY OPS DIGEST</Mono>
          <DailyDigest />
          <LowRatingAlerts notify={toast.show} />
          <Mono style={s.section}>LOW RATING TRENDS · 7 DAYS</Mono>
          <AlertTrends notify={toast.show} />
          <Mono style={s.section}>APOLOGY CODES · USAGE & SPEND</Mono>
          <ApologyCodes notify={toast.show} />
          <Mono style={s.section}>ANALYTICS</Mono>
          <View style={s.grid}>
            {kpis.map((x, i) => (
              <View key={x.k} style={[s.kpi, i % 2 === 0 && { borderRightWidth: 2, borderColor: colors.border }]} testID={x.id}>
                <Mono style={s.kpiLabel}>{x.k}</Mono>
                <Display style={[s.kpiVal, i === 0 && { color: colors.brandPrimary }]}>{x.v}</Display>
              </View>
            ))}
          </View>

          <Mono style={s.section}>DRIVER SCORECARDS · LAST 7 DAYS</Mono>
          {cards.data?.map((c, i) => (
            <Pressable key={c.driver.id} testID={`scorecard-${c.driver.id}`} onPress={() => router.push(`/driver/summary/${c.driver.id}`)} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }]}>
              <Display style={[s.rank, i === 0 && { color: colors.brandPrimary }]}>{i + 1}</Display>
              <View style={{ flex: 1, gap: 4 }}>
                <Display style={s.rowTitle}>{c.driver.name}</Display>
                <Badges badges={c.badges} testIDPrefix={`scorecard-${c.driver.id}-badge`} />
                <Mono style={s.meta}>
                  {c.on_time_rate == null ? "—" : `${c.on_time_rate}%`} ON-TIME · {c.drops_per_hour}/HR · {c.delivered} DROPS · {c.avg_rating ? `${c.avg_rating}★` : "NO RATINGS"}
                </Mono>
              </View>
              <View style={s.spark}>
                {c.trend.map((v, j) => (
                  <View key={j} style={[s.sparkBar, { height: 4 + (v / Math.max(1, ...c.trend)) * 24, backgroundColor: j === c.trend.length - 1 ? colors.brandPrimary : colors.surfaceInverse }]} />
                ))}
              </View>
            </Pressable>
          ))}

          <Mono style={s.section}>ROUTES</Mono>
          {routes.data?.length === 0 ? <Mono style={s.emptyText}>NO ACTIVE ROUTES</Mono> : null}
          {routes.data?.map((r) => {
            const ar = an.data?.routes.find((x) => x.id === r.id);
            return (
              <Pressable key={r.id} testID={`admin-route-${r.name.split(" ")[0]}`} onPress={() => router.push(`/admin/route/${r.id}`)} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }]}>
                <View style={{ flex: 1, gap: 4 }}>
                  <View style={{ flexDirection: "row", gap: spacing.sm, alignItems: "center" }}>
                    <Display style={s.rowTitle}>{r.name}</Display>
                    <Pill label={r.status} tone={statusTone(r.status)} />
                  </View>
                  <Mono style={s.meta}>{r.service_date !== depotToday() ? `${dayLabel(r.service_date)} · ` : ""}{r.driver?.name ?? "UNASSIGNED"} · {r.completed}/{r.total} DROPS · {(r.planned_distance_m / 1000).toFixed(1)} KM PLAN</Mono>
                  {ar ? <Mono style={s.meta}>{ar.drops_per_hour} DROPS/HR · DEV {ar.deviation_pct ?? "—"}{ar.deviation_pct != null ? "%" : ""} · {ar.actual_km} KM DRIVEN</Mono> : null}
                </View>
                <Ionicons name="chevron-forward" size={22} color={colors.onSurface} />
              </Pressable>
            );
          })}

          <Mono style={s.section}>UNASSIGNED · BATCH BY ZONE & DAY</Mono>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ height: 56, flexGrow: 0, borderBottomWidth: 2, borderColor: colors.border }} contentContainerStyle={{ gap: spacing.sm, alignItems: "center", paddingHorizontal: spacing.lg }}>
            {days.map((d, i) => (
              <Pressable key={d} testID={`batch-day-${i}`} onPress={() => setDay(d)} style={[s.chip, d === day && { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary }]}>
                <Mono style={[s.chipText, d === day && { color: colors.onBrandPrimary }]}>{i === 0 ? "TODAY" : i === 1 ? "TMRW" : dayLabel(d)}</Mono>
              </Pressable>
            ))}
          </ScrollView>
          {zones.data?.length === 0 ? <Mono style={s.emptyText}>ALL ORDERS ASSIGNED</Mono> : null}
          {zones.data?.map((z) => (
            <View key={z.zone} style={s.zone} testID={`zone-${z.zone}`}>
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
                <Display style={s.rowTitle}>{z.zone.toUpperCase()}</Display>
                <Mono style={s.meta}>{z.postal_code} · {z.count} ORDERS</Mono>
              </View>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm }} style={{ height: 40, flexGrow: 0 }}>
                {drivers.data?.map((d) => {
                  const sel = assign[z.zone] === d.id;
                  return (
                    <Pressable key={d.id} testID={`zone-${z.zone}-driver-${d.id}`} onPress={() => setAssign((a) => ({ ...a, [z.zone]: d.id }))} style={[s.chip, sel && { backgroundColor: colors.surfaceInverse }]}>
                      <Mono style={[s.chipText, sel && { color: colors.onSurfaceInverse }]}>{d.name.split(" ")[0].toUpperCase()} · {d.vehicle}</Mono>
                    </Pressable>
                  );
                })}
              </ScrollView>
              <Btn testID={`zone-${z.zone}-batch-button`} label="BATCH & OPTIMIZE" icon="git-network" loading={busy === z.zone} onPress={() => batch(z.zone)} variant={assign[z.zone] ? "primary" : "outline"} />
            </View>
          ))}
        </ScrollView>
      )}
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  iconBtn: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.onSurfaceInverse },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  grid: { flexDirection: "row", flexWrap: "wrap" },
  kpi: { width: "50%", padding: spacing.lg, gap: spacing.xs, borderBottomWidth: 2, borderColor: c.border },
  kpiLabel: { fontSize: 11, fontWeight: "700", letterSpacing: 1, color: c.muted },
  kpiVal: { fontSize: 30, letterSpacing: -1 },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.lg, borderBottomWidth: 2, borderColor: c.border },
  rowTitle: { fontSize: 18 },
  rank: { fontSize: 26, width: 28 },
  spark: { flexDirection: "row", alignItems: "flex-end", gap: 2, height: 30 },
  sparkBar: { width: 5 },
  meta: { fontSize: 11, color: c.muted },
  emptyText: { padding: spacing.lg, fontSize: 13, color: c.muted },
  zone: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  chip: { height: 36, flexShrink: 0, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 12, fontWeight: "700" },
}));
