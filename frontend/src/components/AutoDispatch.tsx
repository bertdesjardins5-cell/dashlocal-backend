import React, { useState } from "react";
import { Pressable, ScrollView, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, dayLabel, depotToday } from "@/src/api";
import { Btn, Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Preview = {
  service_date: string; total: number;
  zones: { zone: string; count: number; driver: { id: string; name: string } | null }[];
  drivers: { id: string; name: string; vehicle: string; compass_zone: string | null }[];
};
type Result = { dispatched: number; routes: { route_id: string; route_name: string; driver_name: string; zones: string[]; stops: number; created: boolean; fallback: string[] }[] };

const ZONES = ["North", "South", "East", "West"];

// Admin: one-tap dispatch of all unassigned orders for a day into N/S/E/W of the depot, one route per covering driver.
export default function AutoDispatch({ day, onDone, notify }: { day: string; onDone: () => void; notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["dispatch-preview", day], queryFn: () => api<Preview>(`/dispatch/preview?date=${day}`), refetchInterval: 15000 });
  const [busy, setBusy] = useState<string | null>(null);
  const [last, setLast] = useState<Result | null>(null);
  const p = q.data;

  const setZone = async (driverId: string, zone: string | null) => {
    setBusy(driverId);
    try {
      await api(`/admin/drivers/${driverId}/compass-zone`, { method: "PUT", body: { zone } });
      q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  const run = async () => {
    setBusy("run");
    try {
      const r = await api<Result>("/routes/auto-dispatch", { method: "POST", body: { service_date: day } });
      setLast(r);
      notify(`${r.dispatched} ORDERS DISPATCHED TO ${r.routes.length} DRIVER${r.routes.length === 1 ? "" : "S"}`, "green");
      q.refetch();
      onDone();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  if (!p) return <Mono style={s.empty}>{q.isError ? "COULD NOT LOAD DISPATCH" : "LOADING…"}</Mono>;

  return (
    <View testID="auto-dispatch">
      <View style={s.grid}>
        {p.zones.map((z) => (
          <View key={z.zone} style={s.cell} testID={`dispatch-zone-${z.zone}`}>
            <Display style={s.count}>{z.count}</Display>
            <Mono style={s.zone}>{z.zone.toUpperCase()}</Mono>
            <Mono style={[s.meta, !z.driver && { color: colors.brandPrimary }]} numberOfLines={1}>
              {z.driver ? z.driver.name.split(" ")[0].toUpperCase() : "NO DRIVER"}
            </Mono>
          </View>
        ))}
      </View>
      <View style={s.block}>
        <Mono style={s.label}>DRIVER COVERAGE</Mono>
        {p.drivers.map((d) => (
          <View key={d.id} style={s.driverRow} testID={`coverage-${d.id}`}>
            <Mono style={s.driverName} numberOfLines={1}>{d.name.split(" ")[0].toUpperCase()}</Mono>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.xs }} style={{ flexGrow: 0, flexShrink: 1 }}>
              {[...ZONES, null].map((z) => {
                const sel = d.compass_zone === z;
                return (
                  <Pressable key={z ?? "none"} testID={`coverage-${d.id}-${z ?? "none"}`} disabled={busy === d.id} onPress={() => setZone(d.id, z)}
                    style={[s.chip, sel && { backgroundColor: colors.surfaceInverse }]}>
                    <Mono style={[s.chipText, sel && { color: colors.onSurfaceInverse }]}>{z ? z[0] : "—"}</Mono>
                  </Pressable>
                );
              })}
            </ScrollView>
          </View>
        ))}
        <Mono style={s.meta}>Zones split at the depot. A zone with no driver goes to whoever has the fewest stops. Drivers already on a route for {day === depotToday() ? "today" : dayLabel(day)} get the new stops added and re-optimised.</Mono>
        <Btn testID="auto-dispatch-button" label={`AUTO DISPATCH ${p.total} ORDERS`} icon="flash" loading={busy === "run"} disabled={p.total === 0} onPress={run} />
        {last ? (
          <View style={{ gap: 4 }} testID="auto-dispatch-result">
            {last.routes.map((r) => (
              <Mono key={r.route_id} style={s.meta} testID={`auto-dispatch-result-${r.route_id}`}>
                {r.route_name} → {r.driver_name.split(" ")[0].toUpperCase()} · {r.stops} STOPS · {r.zones.join("+").toUpperCase()}{r.fallback.length ? " (NO DRIVER — FEWEST STOPS)" : ""}{r.created ? "" : " · ADDED TO EXISTING ROUTE"}
              </Mono>
            ))}
          </View>
        ) : null}
      </View>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  empty: { padding: spacing.lg, fontSize: 12, color: c.muted },
  grid: { flexDirection: "row", borderBottomWidth: 2, borderColor: c.border },
  cell: { flex: 1, padding: spacing.sm, alignItems: "center", gap: 2, borderRightWidth: 2, borderColor: c.border },
  count: { fontSize: 22 },
  zone: { fontSize: 11, fontWeight: "700", letterSpacing: 1 },
  meta: { fontSize: 11, color: c.muted },
  block: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5, color: c.muted },
  driverRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  driverName: { width: 72, fontSize: 12, fontWeight: "700" },
  chip: { width: 44, height: 44, borderWidth: 2, borderColor: c.border, alignItems: "center", justifyContent: "center" },
  chipText: { fontSize: 13, fontWeight: "700" },
}));
