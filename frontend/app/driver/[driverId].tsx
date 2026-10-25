import React, { useCallback, useEffect, useRef, useState } from "react";
import { Linking, Platform, Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import BadgeCelebration from "@/src/components/BadgeCelebration";
import type { Badge } from "@/src/components/Badges";
import { useQuery } from "@tanstack/react-query";
import * as Location from "expo-location";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeIn } from "react-native-reanimated";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, dayLabel, depotToday, fmtDepotTime, RouteDetail, windowLabel } from "@/src/api";
import { clearSession } from "@/src/session";
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

  // Weekly badge celebrations: poll for badges the driver hasn't seen yet, and re-check on focus (e.g. after a drop).
  const badgesQ = useQuery({
    queryKey: ["new-badges", driverId],
    queryFn: () => api<{ badges: Badge[] }>(`/drivers/${driverId}/badges/new`),
    refetchInterval: 15000,
  });
  const [dismissed, setDismissed] = useState<string[]>([]);
  const celebrate = (badgesQ.data?.badges ?? []).filter((b) => !dismissed.includes(b.key));
  const refetchBadges = badgesQ.refetch;
  useFocusEffect(useCallback(() => { refetchBadges(); }, [refetchBadges]));
  const dismissCelebration = async () => {
    const keys = celebrate.map((b) => b.key);
    setDismissed((d) => [...d, ...keys]); // hide immediately, even if offline
    try {
      await api(`/drivers/${driverId}/badges/seen`, { method: "POST", body: { keys } });
    } catch {}
  };

  // Coaching notes from dispatch (unread first)
  const coachQ = useQuery({
    queryKey: ["coaching", driverId],
    queryFn: () => api<{ id: string; note: string; reason: string | null; created_at: string }[]>(`/drivers/${driverId}/coaching?unread=true`),
    refetchInterval: 20000,
  });
  const coachNote = coachQ.data?.[0];
  const ackCoaching = async () => {
    if (!coachNote) return;
    try {
      await api(`/coaching/${coachNote.id}/read`, { method: "POST" });
    } catch (e: any) {
      toast.show(`COULD NOT MARK AS READ: ${e.message}`, "red");
    }
    coachQ.refetch();
  };

  const q = useQuery({
    queryKey: ["driver-route", driverId],
    queryFn: () => api<{ route: RouteDetail | null }>(`/drivers/${driverId}/route`),
    refetchInterval: mode === "off" ? 8000 : 3000,
  });
  const route = q.data?.route;
  const ackUpdate = async () => {
    if (!route) return;
    try {
      await api(`/routes/${route.id}/update-seen`, { method: "POST" });
    } catch (e: any) {
      toast.show(`COULD NOT CONFIRM: ${e.message}`, "red");
    }
    q.refetch();
  };
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
      <Header title={route?.name ?? "MANIFEST"} subtitle={route ? `${route.service_date !== depotToday() ? `${dayLabel(route.service_date)} · ` : ""}${route.completed}/${route.total} DROPS · ${route.driver?.name}` : undefined} right={
        <View style={{ flexDirection: "row", gap: spacing.sm }}>
          <Pressable testID="driver-summary-button" onPress={() => router.push(`/driver/summary/${driverId}`)} style={s.iconBtn}>
            <Ionicons name="stats-chart" size={20} color={colors.onSurfaceInverse} />
          </Pressable>
          <Pressable testID="driver-sign-out-button" onPress={() => clearSession()} style={s.iconBtn}>
            <Ionicons name="log-out" size={20} color={colors.onSurfaceInverse} />
          </Pressable>
        </View>
      } />
      {coachNote ? (
        <View style={s.coachBox} testID="coaching-banner">
          <Ionicons name="chatbubble-ellipses" size={20} color={colors.onWarning} />
          <View style={{ flex: 1, gap: 2 }}>
            <Mono style={s.coachKicker}>NOTE FROM DISPATCH{coachNote.reason ? ` · ${coachNote.reason}` : ""}{(coachQ.data?.length ?? 0) > 1 ? ` · +${coachQ.data!.length - 1} MORE` : ""}</Mono>
            <Mono style={s.coachText} testID="coaching-note-text">{coachNote.note}</Mono>
          </View>
          <Pressable testID="coaching-ack-button" onPress={ackCoaching} style={s.coachAck}>
            <Mono style={s.coachAckText}>GOT IT</Mono>
          </Pressable>
        </View>
      ) : null}
      {route && route.resequenced_at && route.resequence_seen === false ? (
        <View style={s.updateBox} testID="route-updated-banner">
          <Ionicons name="swap-vertical" size={20} color={colors.onBrandPrimary} />
          <View style={{ flex: 1, gap: 2 }}>
            <Mono style={s.updateKicker}>ROUTE UPDATED BY DISPATCH · {fmtDepotTime(route.resequenced_at)}</Mono>
            <Mono style={s.updateText}>Your stop order changed. Next up: {route.stops.find((x) => x.id === route.next_stop_id)?.customer_name ?? "—"}.</Mono>
          </View>
          <Pressable testID="route-updated-ack-button" onPress={ackUpdate} style={s.coachAck}>
            <Mono style={s.coachAckText}>GOT IT</Mono>
          </Pressable>
        </View>
      ) : null}
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : !route ? (
        <View style={s.empty} testID="manifest-empty">
          <Display style={s.emptyTitle}>NO ROUTE{"\n"}ASSIGNED</Display>
          <Mono style={s.meta}>Ask dispatch to batch a zone to you.</Mono>
          <Btn testID="empty-view-summary-button" label="VIEW TODAY'S SUMMARY" icon="stats-chart" variant="dark" onPress={() => router.push(`/driver/summary/${driverId}`)} />
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

          {route.status === "planned" && route.service_date > depotToday() ? (
            <View style={s.block} testID="route-scheduled-notice">
              <Mono style={s.meta}>SCHEDULED FOR {dayLabel(route.service_date)} · {route.total} DROPS · {(route.planned_distance_m / 1000).toFixed(1)} KM</Mono>
            </View>
          ) : route.status === "planned" ? (
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

          {route.service_date > depotToday() ? null : next ? (
            <Animated.View entering={FadeIn}>
              <Pressable testID="next-stop-card" onPress={() => router.push(`/driver/stop/${next.id}`)} style={s.hero}>
                <View style={s.heroTop}>
                  <Mono style={s.heroKicker}>NEXT STOP · #{next.seq}</Mono>
                  {next.status === "arrived" ? <Pill label="ARRIVED" tone="yellow" /> : null}
                  {next.priority ? <Pill label="PRIORITY" tone="red" /> : null}
                </View>
                <Display style={s.heroName}>{next.customer_name}</Display>
                <Mono style={s.heroAddr}>{next.address}</Mono>
                <Mono style={s.heroAddr}>{next.postal_code} · {next.tracking_code}</Mono>
                {next.notes ? <Mono style={s.heroNote}>NOTE: {next.notes}</Mono> : null}
                {windowLabel(next) ? (
                  <Mono style={s.heroWindow} testID="next-stop-window">
                    WINDOW {windowLabel(next)} · ETA {fmtDepotTime(next.planned_eta)}{next.late_min ? ` · LATE ${next.late_min}M` : ""}
                  </Mono>
                ) : null}
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
              <Btn testID="complete-view-summary-button" label="VIEW DAY SUMMARY" icon="stats-chart" onPress={() => router.push(`/driver/summary/${driverId}`)} />
            </View>
          )}

          <Mono style={s.section}>MANIFEST · {route.total} STOPS</Mono>
          {remaining.map((st) => (
            <Pressable key={st.id} testID={`manifest-stop-${st.seq}`} onPress={() => router.push(`/driver/stop/${st.id}`)} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }, (st.status === "delivered" || st.status === "failed") && { opacity: 0.5 }]}>
              <Display style={s.seq}>{String(st.seq).padStart(2, "0")}</Display>
              <View style={{ flex: 1, gap: 2 }}>
                <Display style={s.rowName}>{st.customer_name}</Display>
                <Mono style={s.meta} numberOfLines={1}>{st.address}</Mono>
                {windowLabel(st) && st.status === "pending" ? (
                  <Mono style={[s.meta, { color: st.late_min ? colors.brandPrimary : colors.onSurface, fontWeight: "700" }]}>
                    WINDOW {windowLabel(st)}{st.late_min ? ` · LATE ${st.late_min}M` : ""}
                  </Mono>
                ) : null}
              </View>
              {st.priority && st.status === "pending" ? <Pill label="PRIORITY" tone="red" /> : null}
              <Pill label={st.status} tone={statusTone(st.status)} />
            </Pressable>
          ))}
        </ScrollView>
      )}
      {toast.node}
      {celebrate.length ? <BadgeCelebration badges={celebrate} onDone={dismissCelebration} /> : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  iconBtn: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.onSurfaceInverse },
  coachBox: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.md, backgroundColor: c.warning, borderBottomWidth: 2, borderColor: c.border },
  coachKicker: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.onWarning },
  coachText: { fontSize: 13, color: c.onWarning },
  coachAck: { minHeight: 44, paddingHorizontal: spacing.md, justifyContent: "center", backgroundColor: c.surfaceInverse },
  coachAckText: { fontSize: 11, fontWeight: "700", color: c.onSurfaceInverse },
  updateBox: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.md, backgroundColor: c.brandPrimary, borderBottomWidth: 2, borderColor: c.border },
  updateKicker: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.onBrandPrimary },
  updateText: { fontSize: 13, color: c.onBrandPrimary },
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
  heroWindow: { color: c.onSurfaceInverse, fontSize: 13, fontWeight: "700", backgroundColor: c.surfaceInverse, padding: spacing.sm },
  heroActions: { flexDirection: "row", borderWidth: 2, borderColor: c.onBrandPrimary, marginTop: spacing.md },
  heroBtn: { flex: 1, minHeight: 52, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm },
  heroBtnText: { color: c.onBrandPrimary, fontWeight: "700", fontSize: 14 },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.lg, paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderBottomWidth: 2, borderColor: c.border, minHeight: 72 },
  seq: { fontSize: 24, width: 36 },
  rowName: { fontSize: 17 },
}));
