import React, { useState } from "react";
import { Modal, Pressable, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, dayLabel, DEPOT_TZ } from "@/src/api";
import { Btn, Display, Mono, Pill } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Redemption = { order_value: number; discount: number; order_ref: string | null; redeemed_at: string };
type CodeRow = { code: string; tracking_code: string; customer_name: string; rating: number; issued_at: string | null; sms_status: string | null; redemption: Redemption | null };
type Codes = { discount_pct: number; issued: number; redeemed: number; redemption_rate: number; revenue: number; discount_given: number; net_revenue: number; avg_order: number; codes: CodeRow[] };

const day = (iso: string | null) => (iso ? dayLabel(new Date(iso).toLocaleDateString("en-CA", { timeZone: DEPOT_TZ })) : "—");
const money = (n: number) => `$${n.toFixed(2)}`;

// Admin: apology discount codes — issued vs used, and the spend they won back.
export default function ApologyCodes({ notify }: { notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const q = useQuery({ queryKey: ["apology-codes"], queryFn: () => api<Codes>("/codes/apology"), refetchInterval: 30000 });
  const [sel, setSel] = useState<CodeRow | null>(null);
  const [value, setValue] = useState("");
  const [ref, setRef] = useState("");
  const [saving, setSaving] = useState(false);
  const d = q.data;
  if (!d) return null;

  const redeem = async () => {
    if (!sel) return;
    const v = parseFloat(value.replace(/[^0-9.]/g, ""));
    if (!v || v <= 0) return notify("ENTER THE ORDER VALUE", "red");
    setSaving(true);
    try {
      await api("/codes/redeem", { method: "POST", body: { code: sel.code, order_value: v, order_ref: ref || null } });
      notify(`${sel.code} RECORDED · ${money(v)} ORDER`, "green");
      setSel(null);
      q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setSaving(false);
    }
  };

  const cells = [
    { k: "USED", v: `${d.redeemed}/${d.issued}`, id: "codes-used" },
    { k: "RATE", v: `${d.redemption_rate}%`, id: "codes-rate" },
    { k: "WON BACK", v: `$${Math.round(d.net_revenue)}`, id: "codes-net" },
    { k: "AVG ORDER", v: `$${Math.round(d.avg_order)}`, id: "codes-avg" },
  ];
  return (
    <View testID="apology-codes">
      <View style={s.totals}>
        {cells.map((c, i) => (
          <View key={c.k} style={[s.cell, i > 0 && { borderLeftWidth: 2, borderColor: colors.border }]} testID={c.id}>
            <Display style={s.cellVal} numberOfLines={1} adjustsFontSizeToFit>{c.v}</Display>
            <Mono style={s.label}>{c.k}</Mono>
          </View>
        ))}
      </View>
      <Mono style={s.note}>{d.discount_pct}% OFF · {money(d.revenue)} SPENT · {money(d.discount_given)} DISCOUNT GIVEN</Mono>
      {d.codes.map((c) => (
        <View key={c.code} style={s.row} testID={`code-row-${c.code}`}>
          <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
            <Display style={{ fontSize: 15, flexShrink: 1 }}>{c.code}</Display>
            <Mono style={s.meta} numberOfLines={2}>{c.customer_name} · {c.tracking_code} · {c.rating}★ · ISSUED {day(c.issued_at)}</Mono>
            {c.redemption ? (
              <Mono style={[s.meta, { color: colors.success, fontWeight: "700" }]}>
                USED {day(c.redemption.redeemed_at)} · {money(c.redemption.order_value)} ORDER{c.redemption.order_ref ? ` · ${c.redemption.order_ref}` : ""}
              </Mono>
            ) : null}
          </View>
          {c.redemption ? (
            <Pill label="used" tone="green" />
          ) : (
            <Pressable testID={`code-record-${c.code}`} onPress={() => { setValue(""); setRef(""); setSel(c); }} style={s.recordBtn}>
              <Mono style={s.recordText}>RECORD USE</Mono>
            </Pressable>
          )}
        </View>
      ))}
      {sel ? (
        <Modal transparent animationType="slide" visible onRequestClose={() => setSel(null)}>
          <Pressable style={s.backdrop} onPress={() => setSel(null)} testID="redeem-backdrop" />
          <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="redeem-sheet">
            <KeyboardAwareScrollView contentContainerStyle={{ gap: spacing.md }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
              <View style={{ flexDirection: "row", alignItems: "center" }}>
                <Display style={{ fontSize: 22, flex: 1 }} numberOfLines={2}>{sel.code}</Display>
                <Pressable testID="redeem-close" onPress={() => setSel(null)} style={s.close}><Ionicons name="close" size={24} color={colors.onSurface} /></Pressable>
              </View>
              <Mono style={s.meta}>Record that {sel.customer_name} used this code on a new order.</Mono>
              <Mono style={s.label}>ORDER VALUE (BEFORE DISCOUNT)</Mono>
              <TextInput testID="redeem-value-input" value={value} onChangeText={setValue} keyboardType="decimal-pad" placeholder="45.00" placeholderTextColor={colors.muted} style={s.input} />
              <Mono style={s.label}>ORDER REFERENCE (OPTIONAL)</Mono>
              <TextInput testID="redeem-ref-input" value={ref} onChangeText={setRef} autoCapitalize="characters" placeholder="ORD-12345" placeholderTextColor={colors.muted} style={s.input} />
              <Btn testID="redeem-submit-button" label="RECORD USE" icon="pricetag" loading={saving} onPress={redeem} />
            </KeyboardAwareScrollView>
          </View>
        </Modal>
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  totals: { flexDirection: "row", borderBottomWidth: 2, borderColor: c.border },
  cell: { flex: 1, padding: spacing.md, alignItems: "center", gap: 2 },
  cellVal: { fontSize: 18 },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.muted },
  note: { fontSize: 11, flexWrap: "wrap", paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderBottomWidth: 1, borderColor: c.divider },
  meta: { fontSize: 11, color: c.muted },
  recordBtn: { minHeight: 44, flexShrink: 0, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  recordText: { fontSize: 11, fontWeight: "700" },
  backdrop: { flex: 1, backgroundColor: c.surfaceInverse, opacity: 0.5 },
  sheet: { backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.lg, maxHeight: "80%" },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  input: { height: 52, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 18, color: c.onSurface },
}));
