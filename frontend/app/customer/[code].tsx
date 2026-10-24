import React, { useEffect, useRef, useState } from "react";
import { View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeInDown } from "react-native-reanimated";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, fmtDepotTime, Tracking, windowLabel, WS_BASE } from "@/src/api";
import WindowPicker from "@/src/components/WindowPicker";
import RouteMap from "@/src/components/RouteMap";
import { Display, ErrorBlock, Header, Loading, Mono, Pill, statusTone, useToast } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

export default function TrackOrder() {
  const { code } = useLocalSearchParams<{ code: string }>();
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const [live, setLive] = useState<Tracking | null>(null);
  const [wsUp, setWsUp] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  // Polling fallback if the socket drops
  const q = useQuery({ queryKey: ["track", code], queryFn: () => api<Tracking>(`/track/${code}`), refetchInterval: wsUp ? false : 5000 });

  useEffect(() => {
    let closed = false;
    let retry: ReturnType<typeof setTimeout>;
    const connect = () => {
      const ws = new WebSocket(`${WS_BASE}/track/${code}`);
      wsRef.current = ws;
      ws.onopen = () => setWsUp(true);
      ws.onmessage = (e) => {
        try {
          setLive(JSON.parse(e.data));
        } catch {}
      };
      ws.onclose = () => {
        setWsUp(false);
        if (!closed) retry = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    };
    connect();
    const ping = setInterval(() => wsRef.current?.readyState === 1 && wsRef.current.send("ping"), 20000);
    return () => {
      closed = true;
      clearTimeout(retry);
      clearInterval(ping);
      wsRef.current?.close();
    };
  }, [code]);

  const t = live ?? q.data;
  const toast = useToast();
  const [savingWin, setSavingWin] = useState(false);
  const requestWindow = async (start: string | null, end: string | null) => {
    if (!t) return;
    setSavingWin(true);
    try {
      const st = await api<Tracking["stop"]>(`/stops/${t.stop.id}/window`, { method: "PUT", body: { start, end } });
      setLive({ ...t, stop: st });
      q.refetch();
      toast.show(start ? `WINDOW ${start}–${end} REQUESTED` : "WINDOW CLEARED", "green");
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setSavingWin(false);
    }
  };
  const eta = t?.eta_time ? fmtDepotTime(t.eta_time) : null;
  const delivered = t?.stop.status === "delivered";

  return (
    <View style={s.root} testID="customer-tracking-screen">
      <Header title={`ORDER ${code}`} subtitle={wsUp ? "● LIVE · WEBSOCKET" : "○ POLLING"} />
      {!t && q.isLoading ? <Loading /> : !t ? <ErrorBlock message={(q.error as Error)?.message ?? "Not found"} onRetry={q.refetch} /> : (
        <View style={{ flex: 1 }}>
          <View style={{ flex: 1 }}>
            <RouteMap
              height="100%"
              driver={t.route_status === "active" && !delivered ? t.driver : null}
              path={delivered ? [] : t.path}
              markers={[
                ...t.path.slice(1, -1).map((p, i) => ({ ...p, id: `b${i}`, label: String(i + 1), tone: "light" as const })),
                { lat: t.stop.lat, lng: t.stop.lng, id: "me", label: "YOU", tone: delivered ? ("green" as const) : ("red" as const) },
              ]}
            />
            {t.alert ? (
              <Animated.View key={t.alert} entering={FadeInDown} style={[s.banner, (t.stops_away ?? 9) <= 2 || delivered ? { backgroundColor: colors.brandPrimary } : null]} testID="tracking-alert-banner">
                <Ionicons name={delivered ? "checkmark-circle" : "notifications"} size={20} color={colors.onBrandPrimary} />
                <Mono style={s.bannerText}>{t.alert.toUpperCase()}</Mono>
              </Animated.View>
            ) : null}
          </View>
          <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="tracking-sheet">
            <View style={s.sheetTop}>
              <Mono style={s.label}>{delivered ? "DELIVERED AT" : "ESTIMATED ARRIVAL"}</Mono>
              <Pill label={t.stop.status} tone={statusTone(t.stop.status)} />
            </View>
            <Display style={s.eta} testID="tracking-eta">
              {delivered ? fmtDepotTime(t.stop.delivered_at) : t.stop.status === "arrived" ? "HERE NOW" : eta ?? "—"}
            </Display>
            {!delivered && t.eta_minutes != null && t.stop.status !== "arrived" ? <Mono style={s.meta} testID="tracking-eta-minutes">~{t.eta_minutes} MIN</Mono> : null}
            <View style={s.stats}>
              <View style={s.stat}>
                <Mono style={s.label}>STOPS AWAY</Mono>
                <Display style={s.statVal} testID="tracking-stops-away">{t.stops_away ?? "—"}</Display>
              </View>
              <View style={[s.stat, { borderLeftWidth: 2, borderColor: colors.border }]}>
                <Mono style={s.label}>DRIVER</Mono>
                <Display style={[s.statVal, { fontSize: 18 }]} numberOfLines={1}>{t.driver?.name ?? "TBD"}</Display>
                <Mono style={s.meta}>{t.driver?.vehicle ?? ""}</Mono>
              </View>
            </View>
            <Mono style={s.meta} numberOfLines={1}>TO: {t.stop.address}</Mono>
            {!delivered && t.stop.status !== "failed" ? (
              <View style={{ marginHorizontal: -spacing.lg, gap: spacing.xs }} testID="customer-window-section">
                <Mono style={[s.label, { paddingHorizontal: spacing.lg }]}>
                  {windowLabel(t.stop) ? `YOUR WINDOW ${windowLabel(t.stop)} (SF TIME)` : "REQUEST A DELIVERY WINDOW"}
                  {t.late_min ? ` · MAY RUN ${t.late_min}M LATE` : ""}
                </Mono>
                <WindowPicker testIDPrefix="customer-window" start={t.stop.window_start} end={t.stop.window_end} disabled={savingWin} onSelect={requestWindow} />
              </View>
            ) : null}
          </View>
        </View>
      )}
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  banner: { position: "absolute", left: spacing.lg, right: spacing.lg, bottom: spacing.lg, flexDirection: "row", alignItems: "center", gap: spacing.sm, padding: spacing.md, backgroundColor: c.surfaceInverse, borderWidth: 2, borderColor: c.border },
  bannerText: { color: c.onBrandPrimary, fontWeight: "700", fontSize: 13, flex: 1 },
  sheet: { borderTopWidth: 2, borderColor: c.border, backgroundColor: c.surface, padding: spacing.lg, gap: spacing.sm },
  sheetTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  eta: { fontSize: 56, letterSpacing: -2, lineHeight: 60 },
  meta: { fontSize: 12, color: c.muted },
  stats: { flexDirection: "row", borderWidth: 2, borderColor: c.border },
  stat: { flex: 1, padding: spacing.md, gap: 2 },
  statVal: { fontSize: 28 },
}));
