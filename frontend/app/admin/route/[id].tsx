import React, { useEffect, useState } from "react";
import { Pressable, View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import DraggableFlatList, { RenderItemParams, ScaleDecorator } from "react-native-draggable-flatlist";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, RouteDetail, Stop } from "@/src/api";
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
            <Pill label={item.status} tone={statusTone(item.status)} />
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
      <Header title={route?.name ?? "ROUTE"} subtitle={route ? `${route.driver?.name ?? "UNASSIGNED"} · ${route.status.toUpperCase()} · ${(route.planned_distance_m / 1000).toFixed(1)} KM PLAN` : undefined} />
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
                <Btn testID="route-optimize-button" label="RE-OPTIMIZE" icon="git-network" variant="dark" loading={busy === "opt"} onPress={() => run("opt", `/routes/${id}/optimize`, "ROUTE OPTIMIZED")} style={{ flex: 1 }} />
                {route.status === "planned" ? (
                  <Btn testID="route-start-button" label="DISPATCH" icon="play" loading={busy === "start"} onPress={() => run("start", `/routes/${id}/start`, "ROUTE DISPATCHED")} style={{ flex: 1 }} />
                ) : null}
              </View>
              <Mono style={s.section}>DRAG ≡ OR USE ARROWS TO RE-SEQUENCE {busy === "seq" ? "· SAVING…" : ""}</Mono>
            </View>
          }
        />
      )}
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
}));
