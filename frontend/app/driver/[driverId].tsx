import React, { useCallback, useEffect, useRef, useState } from "react";
import { Linking, Platform, Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import * as Location from "expo-location";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeIn } from "react-native-reanimated";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, RouteDetail } from "@/src/api";
import RouteMap from "@/src/components/RouteMap";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, statusTone, useToast } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Mode = "off" | "gps" | "sim";

export default function Manifest() {
  const { driverId } = useLocalSearchParams<{ driverId: string }>();
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>("off");
  const [perm, setPerm] = useState<"ask" | "settings" | null>(null);
  const [starting, setStarting] = useState(false);
  const watchRef = useRef<Location.LocationSubscription | null>(null);

  const q = useQuery({
    queryKey: ["driver-route", driverId],
    queryFn: () => api<{ route: RouteDetail | null }>(`/drivers/${driverId}/route`),
    refetchInterval: mode === "off" ? false : 3000,
  });
  const route = q.data?.route;
  const next = route?.stops.find((st) => st.id === route.next_stop_id);
  const remaining = route?.stops.filter((st) => st.id !== route.next_stop_id) ?? [];

  const onGeofence = useCallback((r: { geofence_arrived_stop_id: string | null }) => {
    if (r.geofence_arrived_stop_id) {
      toast.show("GEOFENCE: ARRIVED AT STOP (50M)", "green");
      q.refetch();
    }
  }, [q, toast]);

  // Simulation loop (GPS stand-in for demo / web preview)
  useEffect(() => {
    if (mode !== "sim") return;
    const id = setInterval(async () => {
      try {
        const r = await api(`/drivers/${driverId}/simulate-step`, { method: "POST" });
        onGeofence(r);
      } catch (e: any) {
        toast.show(e.message, "red");
        setMode("off");
      }
    }, 2000);
    return () => clearInterval(id);
  }, [mode, driverId, onGeofence, toast]);

  useEffect(() => () => watchRef.current?.remove(), []);

  const stopGps = () => {
    watchRef.current?.remove();
    watchRef.current = null;
  };

  const startGps = async () => {
    const cur = await Location.getForegroundPermissionsAsync();
    let granted = cur.granted;
    if (!granted) {
      if (!cur.canAskAgain) return setPerm("settings");
      const req = await Location.requestForegroundPermissionsAsync();
      granted = req.granted;
      if (!granted) return setPerm(req.canAskAgain ? "ask" : "settings");
    }
    setPerm(null);
    watchRef.current = await Location.watchPositionAsync(
      { accuracy: Location.Accuracy.High, distanceInterval: 10, timeInterval: 4000 },
      async (loc) => {
        try {
          const r = await api(`/drivers/${driverId}/location`, { method: "POST", body: { lat: loc.coords.latitude, lng: loc.coords.longitude } });
          onGeofence(r);
        } catch {}
      },
    );
    setMode("gps");
  };

  const selectMode = (m: Mode) => {
    stopGps();
    if (m === "gps") {
      setMode("off");
      setPerm("ask");
      return;
    }
    setMode(m);
  };

  const startRoute = async () => {
    if (!route) return;
    setStarting(true);
    try {
      await api(`/routes/${route.id}/start`, { method: "POST" });
      await q.refetch();
      toast.show("ROUTE STARTED", "green");
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setStarting(false);
    }
  };

  const navigate = () => {
    if (!next) return;
    const url = Platform.select({
      ios: `maps:0,0?daddr=${next.lat},${next.lng}`,
      default: `https://www.google.com/maps/dir/?api=1&destination=${next.lat},${next.lng}`,
    });
    Linking.openURL(url!);
  };

  return (
    <View style={s.root} testID="driver-manifest-screen">
      <Header title={route?.name ?? "MANIFEST"} subtitle={route ? `${route.completed}/${route.total} DROPS · ${route.driver?.name}` : undefined} />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : !route ? (
        <View style={s.empty} testID="manifest-empty">
          <Display style={s.emptyTitle}>NO ROUTE{"\n"}ASSIGNED</Display>
          <Mono style={s.meta}>Ask dispatch to batch a zone to you.</Mono>
        </View>
      ) : (
        <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} refreshControl={<RefreshControl refreshing={q.isRefetching && mode === "off"} onRefresh={() => { q.refetch(); }} />}>
          <RouteMap
            height={200}
            driver={route.status === "active" && route.driver ? route.driver : null}
            geofence={next ?? null}
            path={route.driver && next ? [route.driver, next] : []}
            markers={route.stops.map((st) => ({ id: st.id, lat: st.lat, lng: st.lng, label: String(st.seq), tone: st.id === route.next_stop_id ? "red" : st.status === "delivered" ? "green" : st.status === "failed" ? "dark" : "light" }))}
          />

          {route.status === "planned" ? (
            <View style={s.block}>
              <Mono style={s.meta}>ROUTE IS PLANNED · {route.total} DROPS · {(route.planned_distance_m / 1000).toFixed(1)} KM</Mono>
              <Btn testID="start-route-button" label="START ROUTE" icon="play" onPress={startRoute} loading={starting} />
            </View>
          ) : route.status === "active" ? (
            <View style={s.modeRow}>
              {(["off", "gps", "sim"] as Mode[]).map((m) => (
                <Pressable key={m} testID={`tracking-mode-${m}`} onPress={() => selectMode(m)} style={[s.modeBtn, mode === m && { backgroundColor: colors.surfaceInverse }]}>
                  <Mono style={[s.modeText, mode === m && { color: colors.onSurfaceInverse }]}>{m === "off" ? "PAUSED" : m === "gps" ? "LIVE GPS" : "SIMULATE"}</Mono>
                </Pressable>
              ))}
            </View>
          ) : null}

          {perm ? (
            <View style={s.permBox} testID="location-permission-box">
              <Display style={{ fontSize: 18 }}>SHARE YOUR LOCATION</Display>
              <Mono style={s.meta}>Customers see you approach live, and stops auto-mark ARRIVED within 50 m — no tapping.</Mono>
              {perm === "settings" ? (
                <Btn testID="location-open-settings-button" label="OPEN SETTINGS" variant="dark" icon="settings" onPress={() => Linking.openSettings()} />
              ) : (
                <Btn testID="location-allow-button" label="ALLOW LOCATION" variant="dark" icon="navigate" onPress={startGps} />
              )}
              <Pressable testID="location-not-now-button" onPress={() => setPerm(null)} style={{ minHeight: 44, justifyContent: "center" }}>
                <Mono style={[s.meta, { textAlign: "center" }]}>NOT NOW</Mono>
              </Pressable>
            </View>
          ) : null}

          {next ? (
            <Animated.View entering={FadeIn}>
              <Pressable testID="next-stop-card" onPress={() => router.push(`/driver/stop/${next.id}`)} style={s.hero}>
                <View style={s.heroTop}>
                  <Mono style={s.heroKicker}>NEXT STOP · #{next.seq}</Mono>
                  {next.status === "arrived" ? <Pill label="ARRIVED" tone="yellow" /> : null}
                </View>
                <Display style={s.heroName}>{next.customer_name}</Display>
                <Mono style={s.heroAddr}>{next.address}</Mono>
                <Mono style={s.heroAddr}>{next.postal_code} · {next.tracking_code}</Mono>
                {next.notes ? <Mono style={s.heroNote}>NOTE: {next.notes}</Mono> : null}
                <View style={s.heroActions}>
                  <Pressable testID="next-stop-navigate-button" onPress={navigate} style={s.heroBtn}>
                    <Ionicons name="navigate" size={20} color={colors.onBrandPrimary} />
                    <Mono style={s.heroBtnText}>NAVIGATE</Mono>
                  </Pressable>
                  <View style={[s.heroBtn, { borderLeftWidth: 2, borderColor: colors.onBrandPrimary }]}>
                    <Mono style={s.heroBtnText}>OPEN DROP</Mono>
                    <Ionicons name="arrow-forward" size={20} color={colors.onBrandPrimary} />
                  </View>
                </View>
              </Pressable>
            </Animated.View>
          ) : (
            <View style={s.empty} testID="route-complete">
              <Display style={s.emptyTitle}>ROUTE{"\n"}COMPLETE</Display>
              <Mono style={s.meta}>{route.completed} drops closed.</Mono>
            </View>
          )}

          <Mono style={s.section}>MANIFEST · {route.total} STOPS</Mono>
          {remaining.map((st) => (
            <Pressable key={st.id} testID={`manifest-stop-${st.seq}`} onPress={() => router.push(`/driver/stop/${st.id}`)} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }, (st.status === "delivered" || st.status === "failed") && { opacity: 0.5 }]}>
              <Display style={s.seq}>{String(st.seq).padStart(2, "0")}</Display>
              <View style={{ flex: 1, gap: 2 }}>
                <Display style={s.rowName}>{st.customer_name}</Display>
                <Mono style={s.meta} numberOfLines={1}>{st.address}</Mono>
              </View>
              <Pill label={st.status} tone={statusTone(st.status)} />
            </Pressable>
          ))}
        </ScrollView>
      )}
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  empty: { padding: spacing.xl, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  emptyTitle: { fontSize: 40, lineHeight: 42, letterSpacing: -1 },
  meta: { fontSize: 12, color: c.muted },
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  modeRow: { flexDirection: "row", borderBottomWidth: 2, borderColor: c.border },
  modeBtn: { flex: 1, minHeight: 48, alignItems: "center", justifyContent: "center", borderRightWidth: 2, borderColor: c.border },
  modeText: { fontSize: 12, fontWeight: "700", letterSpacing: 1 },
  permBox: { margin: spacing.lg, padding: spacing.lg, gap: spacing.md, borderWidth: 2, borderColor: c.border, backgroundColor: c.warning },
  hero: { backgroundColor: c.brandPrimary, padding: spacing.xl, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  heroTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  heroKicker: { color: c.onBrandPrimary, fontSize: 13, fontWeight: "700", letterSpacing: 2 },
  heroName: { color: c.onBrandPrimary, fontSize: 34, letterSpacing: -1, lineHeight: 38 },
  heroAddr: { color: c.onBrandPrimary, fontSize: 15, fontWeight: "700" },
  heroNote: { color: c.onBrandPrimary, fontSize: 13, backgroundColor: c.brandSecondary, padding: spacing.sm, marginTop: spacing.xs },
  heroActions: { flexDirection: "row", borderWidth: 2, borderColor: c.onBrandPrimary, marginTop: spacing.md },
  heroBtn: { flex: 1, minHeight: 52, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm },
  heroBtnText: { color: c.onBrandPrimary, fontWeight: "700", fontSize: 14 },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.lg, paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderBottomWidth: 2, borderColor: c.border, minHeight: 72 },
  seq: { fontSize: 24, width: 36 },
  rowName: { fontSize: 17 },
}));
