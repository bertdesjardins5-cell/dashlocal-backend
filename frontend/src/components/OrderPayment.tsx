import React, { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { api, OrderPayment as Payment } from "@/src/api";
import { money } from "@/src/components/BillCard";
import SquarePaySheet from "@/src/components/SquarePaySheet";
import { Btn, Display, Mono, Pill } from "@/src/ui";
import { makeStyles, spacing } from "@/src/theme";

// Customer pays the store's order amount (+ HST) by credit/debit card through Square, then sees a confirmation screen.
export default function OrderPayment({ code, payment, orderToken, onPaid, onError }: {
  code: string; payment: Payment; orderToken: string; onPaid: () => void; onError: (m: string) => void;
}) {
  const s = useStyles();
  const router = useRouter();
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true);
    try {
      setUrl((await api<{ url: string }>(`/track/${code}/pay-link`, { method: "POST", headers: { "X-Order-Token": orderToken } })).url);
    } catch (e: any) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.box} testID="order-payment">
      <View style={s.top}>
        <Mono style={s.label}>{payment.paid ? "PAYMENT" : "AMOUNT DUE"}</Mono>
        {payment.paid ? <Pill label="paid" tone="green" /> : <Pill label="unpaid" tone="red" />}
      </View>
      <View style={s.line}><Mono style={s.meta}>ORDER</Mono><Mono style={s.meta}>{money(payment.amount)}</Mono></View>
      <View style={s.line}><Mono style={s.meta}>HST {payment.hst_pct}% (ONTARIO)</Mono><Mono style={s.meta}>{money(payment.hst)}</Mono></View>
      {payment.refunded ? <View style={s.line}><Mono style={s.meta}>REFUNDED</Mono><Mono style={s.meta} testID="order-payment-refunded">−{money(payment.refunded)}</Mono></View> : null}
      <View style={s.line}><Display style={{ fontSize: 18 }}>TOTAL CAD</Display><Display style={{ fontSize: 18 }} testID="order-payment-total">{money(payment.total)}</Display></View>
      {payment.paid ? (
        <Btn testID="order-payment-view" label="VIEW PAYMENT CONFIRMATION" icon="receipt" variant="outline" onPress={() => router.push(`/customer/paid?code=${code}`)} />
      ) : (
        <Btn testID="order-payment-pay" label={`PAY ${money(payment.total)} BY CARD`} icon="card" loading={busy} onPress={start} />
      )}
      {!payment.paid ? <Mono style={s.meta}>CREDIT OR DEBIT CARD · PROCESSED SECURELY BY SQUARE</Mono> : null}
      <SquarePaySheet url={url} title={`PAY ORDER ${code}`} onClose={() => setUrl(null)} onDone={() => {
        setUrl(null);
        onPaid();
        router.push(`/customer/paid?code=${code}`);
      }} />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { borderWidth: 2, borderColor: c.border, padding: spacing.md, gap: spacing.xs },
  top: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  line: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  meta: { fontSize: 12, color: c.muted },
}));
