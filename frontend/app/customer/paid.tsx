import React, { useEffect, useState } from "react";
import { Linking, Platform, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { ZoomIn } from "react-native-reanimated";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, fmtDepotTime, OrderPrivate } from "@/src/api";
import { loadUnlock } from "@/src/orderUnlock";
import { money } from "@/src/components/BillCard";
import { Btn, Display, ErrorBlock, Header, Loading, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

// Payment confirmation after a customer pays for their order (read back from the server, never assumed).
export default function PaymentConfirmed() {
  const { code } = useLocalSearchParams<{ code: string }>();
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const [p, setP] = useState<OrderPrivate["payment"] | undefined>(undefined);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const u = code ? await loadUnlock(code) : null;
      if (!u) return setErr("Open your order and enter the last 4 digits of your phone to see this payment");
      try {
        setP((await api<OrderPrivate>(`/track/${code}/private`, { headers: { "X-Order-Token": u.token } })).payment ?? null);
      } catch (e: any) {
        setErr(e.message);
      }
    })();
  }, [code]);

  const back = () => (router.canGoBack() ? router.back() : router.replace(`/customer/${code}`));
  return (
    <View style={s.root} testID="payment-confirmation-screen">
      <Header title="PAYMENT" subtitle={`ORDER ${code ?? ""}`} />
      {err ? <ErrorBlock message={err} onRetry={back} /> : p === undefined ? <Loading /> : !p?.paid ? (
        <ErrorBlock message="We couldn't find a payment on this order yet" onRetry={back} />
      ) : (
        <ScrollView contentContainerStyle={[s.body, { paddingBottom: insets.bottom + spacing.xl }]}>
          <Animated.View entering={ZoomIn} style={s.icon}><Ionicons name="checkmark" size={56} color={colors.onSuccess} /></Animated.View>
          <Display style={s.big} testID="payment-confirmed-title">PAYMENT RECEIVED</Display>
          <Display style={s.total} testID="payment-confirmed-total">{money(p.total)}</Display>
          <Mono style={s.meta}>CAD · INCLUDES {money(p.hst)} HST ({p.hst_pct}%)</Mono>
          <View style={s.table}>
            {[
              ["ORDER", code],
              ["ORDER AMOUNT", money(p.amount)],
              [`HST ${p.hst_pct}%`, money(p.hst)],
              ...(p.refunded ? [["REFUNDED", `−${money(p.refunded)}`]] : []),
              ["CARD", p.card],
              ["PAID", p.paid_at ? fmtDepotTime(p.paid_at) : "—"],
              ["RECEIPT SENT TO", p.email],
              ["PAYMENT ID", p.payment_id],
            ].map(([k, v]) => (
              <View key={k} style={s.row}><Mono style={s.k}>{k}</Mono><Mono style={s.v} numberOfLines={1} selectable>{v ?? "—"}</Mono></View>
            ))}
          </View>
          {p.receipt_url ? (
            <Btn testID="payment-receipt" label="VIEW SQUARE RECEIPT" icon="receipt" variant="outline"
              onPress={() => (Platform.OS === "web" ? window.open(p.receipt_url!, "_blank") : Linking.openURL(p.receipt_url!))} />
          ) : null}
          <Btn testID="payment-back-to-order" label="BACK TO MY ORDER" icon="navigate" onPress={back} />
        </ScrollView>
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  body: { padding: spacing.lg, gap: spacing.md, alignItems: "stretch" },
  icon: { alignSelf: "center", width: 96, height: 96, alignItems: "center", justifyContent: "center", backgroundColor: c.success, borderWidth: 2, borderColor: c.border, marginTop: spacing.lg },
  big: { fontSize: 24, textAlign: "center" },
  total: { fontSize: 48, textAlign: "center", letterSpacing: -1 },
  meta: { fontSize: 12, color: c.muted, textAlign: "center" },
  table: { borderWidth: 2, borderColor: c.border },
  row: { flexDirection: "row", justifyContent: "space-between", gap: spacing.md, padding: spacing.md, borderBottomWidth: 1, borderColor: c.surfaceSecondary },
  k: { fontSize: 11, fontWeight: "700", letterSpacing: 1 },
  v: { fontSize: 12, flexShrink: 1, textAlign: "right" },
}));
