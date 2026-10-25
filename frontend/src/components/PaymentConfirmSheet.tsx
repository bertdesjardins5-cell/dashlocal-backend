import React, { useState } from "react";
import { Modal, Pressable, TextInput, View } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, dayLabel } from "@/src/api";
import { Btn, Display, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";
import { money } from "@/src/components/BillCard";

export type PayTarget = { id: string; name: string; total: number; paid: boolean; note?: string | null };

// Admin: confirm marking a weekly bill paid (payment note required) or back to unpaid.
export default function PaymentConfirmSheet({ target, week, onClose, onDone, notify }: {
  target: PayTarget; week: string; onClose: () => void; onDone: () => void; notify: (m: string, tone?: "dark" | "red" | "green") => void;
}) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const markingPaid = !target.paid;
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    if (markingPaid && !note.trim()) return setError("Add a payment note, e.g. e-Transfer reference or cheque number");
    setBusy(true);
    setError(null);
    try {
      await api(`/admin/merchants/${target.id}/bills/${week}/paid`, { method: "PUT", body: { paid: markingPaid, note: markingPaid ? note.trim() : null } });
      notify(markingPaid ? `${target.name.toUpperCase()} · ${money(target.total)} MARKED PAID` : `${target.name.toUpperCase()} MARKED UNPAID`, "green");
      onDone();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal transparent animationType="slide" visible onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} testID="pay-confirm-backdrop" />
      <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="pay-confirm-sheet">
        <KeyboardAwareScrollView contentContainerStyle={{ gap: spacing.md }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
          <View style={{ flexDirection: "row", alignItems: "center" }}>
            <Display style={{ fontSize: 20, flex: 1 }} numberOfLines={2}>{markingPaid ? "CONFIRM PAYMENT" : "MARK AS UNPAID?"}</Display>
            <Pressable testID="pay-confirm-close" onPress={onClose} style={s.close}><Ionicons name="close" size={24} color={colors.onSurface} /></Pressable>
          </View>
          <View style={s.summary}>
            <Mono style={s.meta}>{target.name.toUpperCase()} · WEEK OF {dayLabel(week)}</Mono>
            <Display style={{ fontSize: 28 }} testID="pay-confirm-amount">{money(target.total)}</Display>
            {!markingPaid && target.note ? <Mono style={s.meta}>RECORDED NOTE: {target.note}</Mono> : null}
          </View>
          {markingPaid ? (
            <View style={{ gap: 4 }}>
              <Mono style={s.label}>PAYMENT NOTE (REQUIRED)</Mono>
              <TextInput testID="pay-confirm-note" value={note} onChangeText={(v) => { setNote(v); setError(null); }} maxLength={200}
                placeholder="e-Transfer ref, cheque #, cash…" placeholderTextColor={colors.muted} style={s.input} autoFocus />
            </View>
          ) : (
            <Mono style={s.meta}>This removes the payment record and adds {money(target.total)} back to what the store owes.</Mono>
          )}
          {error ? <View style={s.err} testID="pay-confirm-error"><Mono style={s.errText}>{error}</Mono></View> : null}
          <View style={{ flexDirection: "row", gap: spacing.sm }}>
            <Btn testID="pay-confirm-cancel" label="CANCEL" variant="outline" onPress={onClose} style={{ flex: 1 }} />
            <Btn testID="pay-confirm-submit" label={markingPaid ? "CONFIRM PAID" : "MARK UNPAID"} icon={markingPaid ? "checkmark" : "arrow-undo"}
              loading={busy} onPress={confirm} style={{ flex: 2 }} />
          </View>
        </KeyboardAwareScrollView>
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  backdrop: { flex: 1, backgroundColor: c.surfaceInverse, opacity: 0.5 },
  sheet: { backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.lg, maxHeight: "85%" },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  summary: { borderWidth: 2, borderColor: c.border, padding: spacing.md, gap: 4 },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.muted },
  meta: { fontSize: 12, color: c.muted },
  input: { height: 52, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 15, color: c.onSurface },
  err: { backgroundColor: c.brandPrimary, padding: spacing.md },
  errText: { color: c.onBrandPrimary, fontSize: 12, fontWeight: "700" },
}));
