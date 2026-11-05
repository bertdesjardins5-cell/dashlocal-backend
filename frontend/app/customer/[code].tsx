import React, { useEffect, useRef, useState } from "react";
import { View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import Animated, { FadeInDown } from "react-native-reanimated";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, fmtDepotTime, OrderPrivate, smsNote, Tracking, windowLabel, WS_BASE } from "@/src/api";
import WindowPicker from "@/src/components/WindowPicker";
import RatingPrompt from "@/src/components/RatingPrompt";
import DeliveryProof from "@/src/components/DeliveryProof";
import VerifyOrder from "@/src/components/VerifyOrder";
import CustomerNote from "@/src/components/CustomerNote";
import OrderPayment from "@/src/components/OrderPayment";
import { loadUnlock, saveUnlock, Verified } from "@/src/orderUnlock";
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
  const sheetRef = useRef<any>(null);
  const [order, setOrderState] = useState<Verified | null>(null);
  const setOrder = (o: Verified | null) => {
    saveUnlock(code, o);
    setOrderState(o);
  };
  useEffect(() => {
    loadUnlock(code).then((v) => v && setOrderState(v));
  }, [code]);
  const orderHeaders = order ? { "X-Order-Token": order.token } : undefined;

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
  const status = t?.stop.status;
  const rating = t?.stop.rating;
  const token = order?.token;
  // Refresh private details when the order changes state (e.g. proof photo appears after delivery); drop the unlock if it expired.
  useEffect(() => {
    if (!token || !status) return;
    api<OrderPrivate>(`/track/${code}/private`, { headers: { "X-Order-Token": token } })
      .then((p) => setOrder({ ...p, token }))
      .catch((e: any) => {
        if (e?.status !== 401 && e?.status !== 403) return; // network blip: keep the saved unlock
        setOrder(null);
        toast.show("YOUR UNLOCK EXPIRED · ENTER THE LAST 4 DIGITS AGAIN", "red");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, token, status, rating]);
  const [savingWin, setSavingWin] = useState(false);
  const requestWindow = async (date: string | null, start: string | null, end: string | null) => {
    if (!t) return;
    setSavingWin(true);
    try {
      const st = await api<Tracking["stop"] & { moved_off_route: string | null; sms_status?: string }>(`/stops/${t.stop.id}/window`, { method: "PUT", body: { date, start, end }, headers: orderHeaders });
      const fresh = await q.refetch();
      if (fresh.data) setLive(fresh.data);
      toast.show(`${start ? `WINDOW ${windowLabel(st)} BOOKED` : "WINDOW CLEARED"}${smsNote(st.sms_status)}`, "green");
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
      <Header title={`ORDER ${code}`} subtitle={wsUp ? "● LIVE" : "○ UPDATING EVERY FEW SECONDS"} />
      {!t && q.isLoading ? <Loading /> : !t ? <ErrorBlock message={(q.error as Error)?.message ?? "Not found"} onRetry={q.refetch} /> : (
        <View style={{ flex: 1 }}>
          <View style={{ flex: 1, minHeight: 180 }}>
            <RouteMap
              height="100%"
              driver={t.route_status === "active" && !delivered ? t.driver : null}
              path={!delivered && order && t.route_status === "active" && t.driver ? [t.driver, order] : []}
              markers={order ? [{ lat: order.lat, lng: order.lng, id: "me", label: "YOU", tone: delivered ? ("green" as const) : ("red" as const) }] : [{ ...t.depot, id: "depot", label: "HUB", tone: "light" as const }]}
            />
            {t.alert ? (
              <Animated.View key={t.alert} entering={FadeInDown} style={[s.banner, (t.stops_away ?? 9) <= 2 || delivered ? { backgroundColor: colors.brandPrimary } : null]} testID="tracking-alert-banner">
                <Ionicons name={delivered ? "checkmark-circle" : "notifications"} size={20} color={colors.onBrandPrimary} />
                <View style={{ flex: 1, gap: 2 }}>
                  <Mono style={s.bannerText}>{t.alert.toUpperCase()}</Mono>
                  {!delivered && t.stop.nearby_sms_status === "sent" ? <Mono style={s.bannerSub} testID="tracking-nearby-sms">WE&apos;VE TEXTED YOU TOO</Mono> : null}
                </View>
              </Animated.View>
            ) : null}
          </View>
          <View style={s.sheetWrap}>
          <KeyboardAwareScrollView ref={sheetRef} contentContainerStyle={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} keyboardShouldPersistTaps="handled" bottomOffset={24} testID="tracking-sheet">
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
            {!order ? (
              <VerifyOrder code={code} onVerified={(o) => { setOrder(o); setTimeout(() => sheetRef.current?.scrollTo({ y: 0, animated: true }), 50); }} />
            ) : (
              <>
                <Mono style={s.meta} numberOfLines={1} testID="tracking-address">TO: {order.address}</Mono>
                {order.payment ? (
                  <OrderPayment code={code} payment={order.payment} orderToken={order.token} onError={(m) => toast.show(m, "red")}
                    onPaid={() => api<OrderPrivate>(`/track/${code}/private`, { headers: { "X-Order-Token": order.token } }).then((p) => setOrder({ ...p, token: order.token })).catch(() => {})} />
                ) : null}
                {!delivered && t.stop.status !== "failed" ? (
                  <CustomerNote
                    key={order.customer_note ?? ""}
                    stopId={t.stop.id}
                    note={order.customer_note}
                    source={order.customer_note_source ?? null}
                    read={!!t.stop.customer_note_read}
                    locked={t.stop.status !== "pending"}
                    orderToken={order.token}
                    onSaved={(n) => {
                      setOrder({ ...order, customer_note: n, customer_note_source: n ? "customer" : null });
                      toast.show(n ? "NOTE SENT TO YOUR DRIVER" : "NOTE CLEARED", "green");
                    }}
                    onError={(m) => toast.show(m, "red")}
                  />
                ) : null}
                {!delivered && t.stop.status !== "failed" ? (
                  <View style={{ marginHorizontal: -spacing.lg, gap: spacing.xs }} testID="customer-window-section">
                    <Mono style={[s.label, { paddingHorizontal: spacing.lg }]}>
                      {windowLabel(t.stop) ? `YOUR WINDOW ${windowLabel(t.stop)} (EASTERN TIME)` : "BOOK A DELIVERY DAY & WINDOW"}
                      {t.late_min ? ` · MAY RUN ${t.late_min}M LATE` : ""}
                    </Mono>
                    <WindowPicker testIDPrefix="customer-window" date={t.stop.window_date} start={t.stop.window_start} end={t.stop.window_end} disabled={savingWin || t.stop.status === "arrived"} onSelect={requestWindow} />
                  </View>
                ) : null}
                {delivered ? (
                  <DeliveryProof
                    stop={{ id: t.stop.id, tracking_code: t.stop.tracking_code, delivered_at: t.stop.delivered_at, pod_qr_verified: t.stop.pod_qr_verified, pod_signature: order.pod_signature, photo_url: order.pod_photo_url }}
                    orderToken={order.token}
                    onShared={() => toast.show("PROOF LINK READY · VALID 30 DAYS", "green")}
                    onError={(m) => toast.show(m, "red")}
                  />
                ) : null}
                {delivered ? (
                  <RatingPrompt
                    stop={{ id: t.stop.id, rating: t.stop.rating, apology_code: order.apology_code }}
                    orderToken={order.token}
                    onRated={(st) => {
                      setLive({ ...t, stop: { ...t.stop, rating: st.rating } });
                      setOrder({ ...order, apology_code: st.apology_code ?? null });
                      toast.show("THANKS FOR YOUR FEEDBACK", "green");
                    }}
                    onError={(m) => toast.show(m, "red")}
                  />
                ) : null}
              </>
            )}
          </KeyboardAwareScrollView>
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
  bannerText: { color: c.onBrandPrimary, fontWeight: "700", fontSize: 13 },
  bannerSub: { color: c.onBrandPrimary, fontSize: 11 },
  sheetWrap: { maxHeight: "65%", borderTopWidth: 2, borderColor: c.border, backgroundColor: c.surface },
  sheet: { padding: spacing.lg, gap: spacing.sm },
  sheetTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  eta: { fontSize: 56, letterSpacing: -2, lineHeight: 60 },
  meta: { fontSize: 12, color: c.muted },
  stats: { flexDirection: "row", borderWidth: 2, borderColor: c.border },
  stat: { flex: 1, padding: spacing.md, gap: 2 },
  statVal: { fontSize: 28 },
}));
