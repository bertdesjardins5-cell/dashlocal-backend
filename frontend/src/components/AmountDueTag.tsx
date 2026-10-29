import React, { useState } from "react";
import { Pressable, TextInput, View } from "react-native";
import { api } from "@/src/api";
import { money } from "@/src/components/BillCard";
import { Btn, Mono, Pill } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Notify = (m: string, tone?: "dark" | "red" | "green") => void;

// Store sets what the customer pays for this order in the app (before HST). Empty = nothing to pay.
export default function AmountDueTag({ merchantId, stopId, value, paid, onSaved, notify }: {
  merchantId: string; stopId: string; value?: number | null; paid?: { total: number } | null; onSaved: () => void; notify: Notify;
}) {
  const s = useStyles();
  const { colors } = useTheme();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  if (paid) return <View style={{ marginTop: spacing.xs }}><Pill label={`customer paid ${money(paid.total)}`} tone="green" /></View>;

  const save = async () => {
    const t = text.replace(/[$,\s]/g, "");
    const amount = t ? Number(t) : null;
    if (amount !== null && !isFinite(amount)) return notify("ENTER A DOLLAR AMOUNT", "red");
    setBusy(true);
    try {
      const r = await api<{ amount_due: number | null; payment: { total: number } | null }>(`/merchants/${merchantId}/stops/${stopId}/amount-due`, { method: "PUT", body: { amount } });
      notify(r.payment ? `CUSTOMER PAYS ${money(r.payment.total)} INCL. HST` : "NO PAYMENT NEEDED", "green");
      setEditing(false);
      onSaved();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  if (!editing) {
    return (
      <Pressable testID={`amount-edit-${stopId}`} onPress={() => { setText(value ? String(value) : ""); setEditing(true); }} style={s.btn}>
        <Mono style={s.btnText}>{value ? `CUSTOMER PAYS ${money(value)} + HST ✎` : "+ CUSTOMER PAYS IN APP"}</Mono>
      </Pressable>
    );
  }
  return (
    <View style={s.row} testID={`amount-editor-${stopId}`}>
      <TextInput testID={`amount-input-${stopId}`} value={text} onChangeText={(v) => setText(v.replace(/[^0-9.]/g, ""))} keyboardType="decimal-pad" autoFocus
        placeholder="Amount before HST, e.g. 42.50" placeholderTextColor={colors.muted} style={s.input} onSubmitEditing={save} />
      <Btn testID={`amount-save-${stopId}`} label="SAVE" loading={busy} onPress={save} />
      <Pressable testID={`amount-cancel-${stopId}`} onPress={() => setEditing(false)} style={s.btn} accessibilityLabel="Cancel">
        <Mono style={s.btnText}>✕</Mono>
      </Pressable>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginTop: spacing.xs },
  input: { flex: 1, minWidth: 0, minHeight: 44, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.sm, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface, backgroundColor: c.surface },
  btn: { minHeight: 44, minWidth: 44, alignSelf: "flex-start", justifyContent: "center", alignItems: "center", paddingHorizontal: spacing.sm, borderWidth: 2, borderColor: c.border, marginTop: spacing.xs },
  btnText: { fontSize: 11, fontWeight: "700", color: c.onSurface },
}));
