import React, { useState } from "react";
import { FlatList, Modal, Pressable, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, fmtDepotTime } from "@/src/api";
import { money } from "@/src/components/BillCard";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, statusTone, useToast } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Refund = { id: string; amount: number; status: string; reason: string; at: string };
type Row = { kind: "order" | "tip"; payment_id: string; total: number; tip: number; refunded: number; refundable: number; card: string | null;
  paid_at: string | null; email: string | null; refunds?: Refund[]; stop_id: string; tracking_code: string; customer_name: string | null;
  status: string; store: string | null; driver: string | null };
type Resp = { payments: Row[]; tips_by_driver: { driver: string; total: number; count: number }[]; sandbox: boolean };

// Admin: every customer card payment and driver tip, with one-tap full or partial refunds back to the card.
export default function CustomerPaymentsScreen() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const toast = useToast();
  const q = useQuery({ queryKey: ["customer-payments"], queryFn: () => api<Resp>("/admin/customer-payments") });
  const [sel, setSel] = useState<Row | null>(null);
  const d = q.data;

  const head = d ? (
    <View style={{ gap: spacing.md }}>
      {d.sandbox ? <Pill label="SQUARE TEST MODE · REFUNDS ARE NOT REAL" tone="yellow" /> : null}
      <View style={s.card} testID="tips-by-driver">
        <Display style={s.title}>TIPS BY DRIVER</Display>
        {d.tips_by_driver.length === 0 ? <Mono style={s.meta}>No tips yet.</Mono> : d.tips_by_driver.map((t) => (
          <View key={t.driver} style={s.line}>
            <Mono style={s.v}>{t.driver} · {t.count} TIP{t.count === 1 ? "" : "S"}</Mono>
            <Mono style={s.v}>{money(t.total)}</Mono>
          </View>
        ))}
        <Mono style={s.meta}>Tips land in your Square account. Pay them out to drivers as you usually do.</Mono>
      </View>
      <Mono style={s.section}>CUSTOMER PAYMENTS</Mono>
    </View>
  ) : undefined;

  return (
    <View style={s.root} testID="customer-payments-screen">
      <Header title="CUSTOMER PAYMENTS" subtitle="Order payments, tips & refunds" />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : d && (
        <FlatList
          data={d.payments}
          keyExtractor={(r) => r.payment_id}
          ListHeaderComponent={head}
          ListEmptyComponent={<Mono style={s.meta}>No customer card payments yet.</Mono>}
          contentContainerStyle={{ padding: spacing.md, gap: spacing.sm, paddingBottom: insets.bottom + spacing.xl }}
          refreshing={q.isRefetching}
          onRefresh={q.refetch}
          renderItem={({ item: r }) => (
            <Pressable testID={`payment-row-${r.payment_id}`} onPress={() => setSel(r)} style={({ pressed }) => [s.card, pressed && s.pressed]}>
              <View style={s.line}>
                <Display style={s.title}>{r.tracking_code} · {r.kind === "tip" ? "TIP" : "ORDER"}</Display>
                <Pill label={r.status} tone={statusTone(r.status)} />
              </View>
              <Mono style={s.meta}>{r.customer_name ?? "—"} · {r.store ?? "—"}{r.driver ? ` · DRIVER ${r.driver}` : ""}</Mono>
              <View style={s.line}>
                <Mono style={s.v}>{money(r.total)}{r.tip ? ` (TIP ${money(r.tip)})` : ""}</Mono>
                {r.refunded ? <Pill label={r.refundable <= 0 ? "REFUNDED" : `REFUNDED ${money(r.refunded)}`} tone="red" /> : null}
              </View>
              <Mono style={s.meta}>{r.card ?? ""} · {r.paid_at ? fmtDepotTime(r.paid_at) : ""}</Mono>
            </Pressable>
          )}
        />
      )}
      {sel ? <RefundSheet row={sel} onClose={() => setSel(null)} onDone={(m) => { setSel(null); toast.show(m, "green"); q.refetch(); }} onError={(m) => toast.show(m, "red")} /> : null}
      {toast.node}
    </View>
  );
}

function RefundSheet({ row, onClose, onDone, onError }: { row: Row; onClose: () => void; onDone: (m: string) => void; onError: (m: string) => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [amount, setAmount] = useState(row.refundable.toFixed(2));
  const [reason, setReason] = useState(row.status === "failed" ? "Delivery failed" : "");
  const [busy, setBusy] = useState(false);

  const go = async () => {
    const v = Number(amount.replace(",", "."));
    if (!isFinite(v) || v <= 0) return onError("ENTER A REFUND AMOUNT");
    if (v > row.refundable + 0.001) return onError(`YOU CAN REFUND AT MOST ${money(row.refundable)}`);
    setBusy(true);
    try {
      await api(`/admin/stops/${row.stop_id}/refund`, { method: "POST", body: { payment_id: row.payment_id, amount: v, reason } });
      onDone(`${money(v)} REFUNDED TO ${row.card ?? "THE CARD"}`);
    } catch (e: any) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal transparent animationType="slide" visible onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} testID="refund-backdrop" />
      <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="refund-sheet">
        <View style={s.line}>
          <Display style={{ fontSize: 20, flex: 1 }}>REFUND · {row.tracking_code}</Display>
          <Pressable testID="refund-close" onPress={onClose} style={s.close}><Ionicons name="close" size={24} color={colors.onSurface} /></Pressable>
        </View>
        <KeyboardAwareScrollView contentContainerStyle={{ gap: spacing.sm }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
          <Mono style={s.meta}>PAID {money(row.total)} · REFUNDED {money(row.refunded)} · LEFT {money(row.refundable)}</Mono>
          {(row.refunds ?? []).map((f) => <Mono key={f.id} style={s.meta}>• {money(f.amount)} {f.status} · {f.reason} · {fmtDepotTime(f.at)}</Mono>)}
          {row.refundable > 0 ? (
            <>
              <Mono style={s.label}>AMOUNT (CAD)</Mono>
              <View style={{ flexDirection: "row", gap: spacing.sm }}>
                <TextInput testID="refund-amount" value={amount} onChangeText={(v) => setAmount(v.replace(/[^0-9.,]/g, ""))} keyboardType="decimal-pad" style={[s.input, { flex: 1 }]} />
                <Btn testID="refund-full" label="FULL" variant="outline" onPress={() => setAmount(row.refundable.toFixed(2))} />
              </View>
              <Mono style={s.label}>REASON (SHOWN TO THE CUSTOMER)</Mono>
              <TextInput testID="refund-reason" value={reason} onChangeText={setReason} maxLength={180} placeholder="e.g. Order cancelled"
                placeholderTextColor={colors.muted} style={s.input} />
              <Btn testID="refund-submit" label="REFUND TO CARD" icon="return-down-back" variant="dark" loading={busy} onPress={go} />
              <Mono style={s.meta}>The money goes back to the customer&apos;s card in 5–10 business days and they get an email.</Mono>
            </>
          ) : <Pill label="FULLY REFUNDED" tone="red" />}
        </KeyboardAwareScrollView>
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  card: { borderWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary, padding: spacing.md, gap: spacing.xs },
  pressed: { backgroundColor: c.surfaceTertiary },
  title: { fontSize: 16, color: c.onSurface, flexShrink: 1 },
  section: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5, color: c.onSurfaceSecondary },
  meta: { fontSize: 12, color: c.onSurfaceSecondary },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: spacing.xs },
  v: { fontSize: 14, color: c.onSurface },
  line: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: spacing.sm },
  backdrop: { flex: 1, backgroundColor: c.surfaceInverse, opacity: 0.5 },
  sheet: { backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.lg, maxHeight: "90%" },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  input: { minHeight: 52, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 16, color: c.onSurface, backgroundColor: c.surface },
}));
