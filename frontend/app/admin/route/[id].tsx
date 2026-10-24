import React, { useEffect, useState } from "react";
import { Modal, Platform, Pressable, View } from "react-native";
import * as Print from "expo-print";
import QRCode from "react-native-qrcode-svg";
import WindowPicker from "@/src/components/WindowPicker";
import { useLocalSearchParams } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import DraggableFlatList, { RenderItemParams, ScaleDecorator } from "react-native-draggable-flatlist";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, API, dayLabel, depotToday, fmtDepotTime, RouteDetail, Stop, windowLabel } from "@/src/api";
import RouteMap from "@/src/components/RouteMap";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, statusTone, useToast } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

export default function AdminRoute() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const s = useStyles();
  const qc = useQueryClient();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const [stops, setStops] = useState<Stop[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const q = useQuery({ queryKey: ["route", id], queryFn: () => api<RouteDetail>(`/routes/${id}`), refetchInterval: 8000 });
  const route = q.data;
  useEffect(() => { if (route) setStops(route.stops); }, [route]);

  const locked = (st: Stop) => st.status === "delivered" || st.status === "failed";
  const [sel, setSel] = useState<Stop | null>(null);

  const printLabels = async () => {
    const url = `${API}/routes/${id}/labels.html`;
    setBusy("print");
    try {
      if (Platform.OS === "web") {
        window.open(`${url}?autoprint=1`, "_blank");
      } else {
        const html = await (await fetch(url)).text();
        await Print.printAsync({ html });
      }
    } catch (e: any) {
      toast.show(e.message ?? "Could not print labels", "red");
    } finally {
      setBusy(null);
    }
  };

  const setWindow = async (date: string | null, start: string | null, end: string | null) => {
    if (!sel) return;
    setBusy("win");
    try {
      const st = await api<Stop & { moved_off_route: string | null }>(`/stops/${sel.id}/window`, { method: "PUT", body: { date, start, end } });
      await q.refetch();
      if (st.moved_off_route) {
        setSel(null);
        toast.show(`${st.tracking_code} BOOKED FOR ${windowLabel(st)} — MOVED OFF THIS ROUTE`, "green");
      } else {
        setSel({ ...sel, ...st });
        toast.show(start ? `WINDOW ${windowLabel(st)} SET · OPTIMIZE TO APPLY` : "WINDOW CLEARED", "green");
      }
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  const save = async (next: Stop[], label = "SEQUENCE SAVED") => {
    const lockedIds = next.filter(locked).map((x) => x.id);
    const ordered = [...lockedIds, ...next.filter((x) => !locked(x)).map((x) => x.id)];
    setBusy("seq");
    try {
      const r = await api<RouteDetail>(`/routes/${id}/sequence`, { method: "PUT", body: { stop_ids: ordered } });
      qc.setQueryData(["route", id], r);
      qc.invalidateQueries({ queryKey: ["routes"] });
      toast.show(`MANUAL OVERRIDE · ${label}`, "green");
    } catch (e: any) {
      toast.show(e.message, "red");
      q.refetch();
    } finally {
      setBusy(null);
    }
  };

  const move = (idx: number, dir: -1 | 1) => {
    const j = idx + dir;
    if (j < 0 || j >= stops.length || locked(stops[j]) || locked(stops[idx])) return;
    const next = [...stops];
    [next[idx], next[j]] = [next[j], next[idx]];
    setStops(next);
    save(next);
  };

  const run = async (key: string, path: string, msg: string) => {
    setBusy(key);
    try {
      const r = await api<RouteDetail>(path, { method: "POST" });
      qc.setQueryData(["route", id], r);
      qc.invalidateQueries({ queryKey: ["routes"] });
      toast.show(msg, "green");
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  const renderItem = ({ item, drag, isActive, getIndex }: RenderItemParams<Stop>) => {
    const idx = getIndex() ?? 0;
    const lock = locked(item);
    return (
      <ScaleDecorator>
        <Pressable
          testID={`seq-row-${item.tracking_code}`}
          onLongPress={lock ? undefined : drag}
          onPress={() => setSel(item)}
          delayLongPress={150}
          style={[s.row, isActive && { backgroundColor: colors.brandTertiary }, lock && { opacity: 0.5 }]}
        >
          <Pressable testID={`drag-handle-${item.tracking_code}`} onPressIn={lock ? undefined : drag} style={s.handle} hitSlop={6}>
            <Ionicons name={lock ? "lock-closed" : "reorder-three"} size={26} color={colors.onSurface} />
          </Pressable>
          <Display style={s.seq}>{String(idx + 1).padStart(2, "0")}</Display>
          <View style={{ flex: 1, gap: 2 }}>
            <Display style={s.name} numberOfLines={1}>{item.customer_name}</Display>
            <Mono style={s.meta} numberOfLines={1}>{item.address}</Mono>
            <View style={{ flexDirection: "row", gap: spacing.xs, flexWrap: "wrap" }}>
              <Pill label={item.status} tone={statusTone(item.status)} />
              {windowLabel(item) ? <Pill label={`⏱ ${windowLabel(item)}`} tone="dark" /> : null}
              {!lock && item.planned_eta ? <Pill label={`ETA ${fmtDepotTime(item.planned_eta)}`} tone="light" /> : null}
              {item.late_min ? <Pill label={`LATE ${item.late_min}M`} tone="red" /> : null}
            </View>
          </View>
          <View>
            <Pressable testID={`move-up-${item.tracking_code}`} onPress={() => move(idx, -1)} style={s.arrow}><Ionicons name="chevron-up" size={20} color={colors.onSurface} /></Pressable>
            <Pressable testID={`move-down-${item.tracking_code}`} onPress={() => move(idx, 1)} style={s.arrow}><Ionicons name="chevron-down" size={20} color={colors.onSurface} /></Pressable>
          </View>
        </Pressable>
      </ScaleDecorator>
    );
  };

  return (
    <View style={s.root} testID="admin-route-screen">
      <Header title={route?.name ?? "ROUTE"} subtitle={route ? `${route.service_date !== depotToday() ? `${dayLabel(route.service_date)} · ` : ""}${route.driver?.name ?? "UNASSIGNED"} · ${route.status.toUpperCase()} · ${(route.planned_distance_m / 1000).toFixed(1)} KM PLAN` : undefined} />
      {q.isLoading ? <Loading /> : q.error || !route ? <ErrorBlock message={(q.error as Error)?.message ?? "Not found"} onRetry={q.refetch} /> : (
        <DraggableFlatList
          data={stops}
          keyExtractor={(x) => x.id}
          renderItem={renderItem}
          onDragEnd={({ data }) => { setStops(data); save(data); }}
          containerStyle={{ flex: 1 }}
          contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }}
          ListHeaderComponent={
            <View>
              <RouteMap
                height={220}
                driver={route.status === "active" ? route.driver : null}
                path={[route.depot, ...stops.map((x) => ({ lat: x.lat, lng: x.lng }))]}
                markers={stops.map((x, i) => ({ id: x.id, lat: x.lat, lng: x.lng, label: String(i + 1), tone: x.status === "delivered" ? "green" : x.id === route.next_stop_id ? "red" : "dark" }))}
              />
              <View style={s.actions}>
                <Btn testID="route-optimize-button" label="OPTIMIZE" icon="git-network" variant="dark" loading={busy === "opt"} onPress={() => run("opt", `/routes/${id}/optimize`, "ROUTE OPTIMIZED")} style={{ flex: 1 }} />
                <Btn testID="print-labels-button" label="LABELS" icon="print" variant="outline" loading={busy === "print"} onPress={printLabels} style={{ flex: 1 }} />
                {route.status === "planned" && route.service_date <= depotToday() ? (
                  <Btn testID="route-start-button" label="DISPATCH" icon="play" loading={busy === "start"} onPress={() => run("start", `/routes/${id}/start`, "ROUTE DISPATCHED")} style={{ flex: 1 }} />
                ) : null}
              </View>
              <Mono style={s.section}>DRAG ≡ OR USE ARROWS TO RE-SEQUENCE · TAP A STOP FOR LABEL & WINDOW {busy === "seq" ? "· SAVING…" : ""}</Mono>
              {route.late_stops > 0 ? (
                <View style={s.lateBanner} testID="route-late-banner">
                  <Mono style={s.lateText}>{route.late_stops} STOP{route.late_stops > 1 ? "S" : ""} WILL MISS THEIR WINDOW — TAP OPTIMIZE</Mono>
                </View>
              ) : null}
            </View>
          }
        />
      )}
      {sel ? (
        <Modal transparent animationType="slide" visible onRequestClose={() => setSel(null)}>
          <Pressable style={s.backdrop} onPress={() => setSel(null)} testID="stop-options-backdrop" />
          <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="stop-options-sheet">
            <View style={s.sheetHead}>
              <View style={{ flex: 1 }}>
                <Display style={{ fontSize: 20 }}>{sel.customer_name}</Display>
                <Mono style={s.meta}>{sel.address}</Mono>
              </View>
              <Pressable testID="stop-options-close" onPress={() => setSel(null)} style={s.handle}>
                <Ionicons name="close" size={26} color={colors.onSurface} />
              </Pressable>
            </View>
            <View style={s.label} testID="parcel-label">
              <QRCode value={`MULTIDROP:${sel.tracking_code}`} size={150} color={colors.onSurface} backgroundColor={colors.surface} />
              <View style={{ flex: 1, gap: spacing.xs }}>
                <Mono style={s.meta}>PARCEL LABEL</Mono>
                <Display style={{ fontSize: 26 }}>{sel.tracking_code}</Display>
                <Mono style={s.meta}>{sel.postal_code} · {sel.zone.toUpperCase()}</Mono>
                <Mono style={s.meta}>Driver scans this at the door as proof of delivery.</Mono>
              </View>
            </View>
            <Mono style={[s.meta, { paddingHorizontal: spacing.lg, fontWeight: "700" }]}>DELIVERY WINDOW (SF TIME)</Mono>
            <WindowPicker testIDPrefix="admin-window" date={sel.window_date} start={sel.window_start} end={sel.window_end} disabled={sel.status !== "pending" || busy === "win"} onSelect={setWindow} />
          </View>
          {toast.node}
        </Modal>
      ) : null}
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  actions: { flexDirection: "row", gap: spacing.sm, padding: spacing.lg, borderBottomWidth: 2, borderColor: c.border },
  section: { padding: spacing.lg, fontSize: 11, fontWeight: "700", letterSpacing: 1.5, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surface, minHeight: 84 },
  handle: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  seq: { fontSize: 22, width: 32 },
  name: { fontSize: 16 },
  meta: { fontSize: 11, color: c.muted },
  arrow: { width: 44, height: 36, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.border, marginVertical: 1 },
  lateBanner: { backgroundColor: c.brandPrimary, padding: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  lateText: { color: c.onBrandPrimary, fontSize: 12, fontWeight: "700" },
  backdrop: { flex: 1, backgroundColor: c.surfaceInverse, opacity: 0.5 },
  sheet: { backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, gap: spacing.md, paddingTop: spacing.lg },
  sheetHead: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.lg, gap: spacing.md },
  label: { flexDirection: "row", gap: spacing.lg, alignItems: "center", marginHorizontal: spacing.lg, padding: spacing.md, borderWidth: 2, borderColor: c.border },
}));
