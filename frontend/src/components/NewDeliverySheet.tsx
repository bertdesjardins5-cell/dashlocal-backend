import React, { useState } from "react";
import { Modal, Pressable, TextInput, View } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import Checkbox from "expo-checkbox";
import { api, Stop } from "@/src/api";
import { Btn, Display, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Form = { customer_name: string; phone: string; address: string; notes: string; delivery_note: string; window_date: string;
  window_start: string; window_end: string; matter_ref: string; amount_due: string };
type Field = { k: keyof Form; label: string; ph: string; kb?: "phone-pad" | "decimal-pad" | "numbers-and-punctuation"; law?: boolean; noRun?: boolean };

const FIELDS: Field[] = [
  { k: "customer_name", label: "CUSTOMER NAME *", ph: "Jane Doe" },
  { k: "phone", label: "CUSTOMER PHONE *", ph: "519 555 0123", kb: "phone-pad" },
  { k: "address", label: "STREET ADDRESS (KITCHENER-WATERLOO) *", ph: "200 King St W" },
  { k: "notes", label: "ORDER NOTES (OPTIONAL)", ph: "Leave at reception" },
  { k: "delivery_note", label: "GATE CODE / DOOR NOTE (OPTIONAL, SAVED FOR THIS CUSTOMER)", ph: "Gate code 2468" },
  { k: "window_date", label: "DELIVERY DATE (OPTIONAL, YYYY-MM-DD · BLANK = NEXT AVAILABLE)", ph: "2026-11-03", kb: "numbers-and-punctuation", noRun: true },
  { k: "window_start", label: "WINDOW FROM (OPTIONAL, HH:MM)", ph: "14:00", kb: "numbers-and-punctuation", noRun: true },
  { k: "window_end", label: "WINDOW TO (OPTIONAL, HH:MM)", ph: "16:00", kb: "numbers-and-punctuation", noRun: true },
  { k: "matter_ref", label: "MATTER / FILE NO. (OPTIONAL)", ph: "2026-0142", law: true },
  { k: "amount_due", label: "CUSTOMER PAYS IN APP, BEFORE HST (OPTIONAL, $)", ph: "45.00", kb: "decimal-pad" },
];
const EMPTY: Form = { customer_name: "", phone: "", address: "", notes: "", delivery_note: "", window_date: "", window_start: "", window_end: "", matter_ref: "", amount_due: "" };

// Merchant Portal: enter one delivery by hand. Same checks as a CSV row; problems show right here in the form.
export default function NewDeliverySheet({ merchantId, run, runLabel, lawOffice, consent, onClose, onCreated }: {
  merchantId: string; run: string | null; runLabel?: string; lawOffice?: boolean; consent: boolean; onClose: () => void; onCreated: (s: Stop) => void;
}) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [f, setF] = useState<Form>(EMPTY);
  const [priority, setPriority] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const missing = (["customer_name", "phone", "address"] as const).filter((k) => !f[k].trim());
    if (missing.length) return setError(`Fill in ${missing.map((k) => k.replace("customer_", "")).join(", ")}`);
    setBusy(true);
    setError(null);
    try {
      const stop = await api<Stop>(`/merchants/${merchantId}/orders`, { method: "POST", body: { ...f, priority, consent, run } });
      onCreated(stop);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal transparent animationType="slide" visible onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} testID="new-delivery-backdrop" />
      <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="new-delivery-sheet">
        <View style={{ flexDirection: "row", alignItems: "center" }}>
          <Display style={{ fontSize: 22, flex: 1 }}>NEW DELIVERY</Display>
          <Pressable testID="new-delivery-close" onPress={onClose} style={s.close}><Ionicons name="close" size={24} color={colors.onSurface} /></Pressable>
        </View>
        <KeyboardAwareScrollView style={{ flexShrink: 1 }} contentContainerStyle={{ gap: spacing.sm, paddingTop: spacing.sm }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
          {runLabel ? <Mono style={s.hint} testID="new-delivery-run">PICKUP RUN: {runLabel.toUpperCase()} · THE RUN SETS THE DAY AND WINDOW</Mono> : null}
          {FIELDS.filter((x) => (!x.law || lawOffice) && (!x.noRun || !run)).map((x) => (
            <View key={x.k} style={{ gap: 4 }}>
              <Mono style={s.label}>{x.label}</Mono>
              <TextInput testID={`new-delivery-${x.k}`} value={f[x.k]} onChangeText={(v) => setF((p) => ({ ...p, [x.k]: v }))}
                placeholder={x.ph} placeholderTextColor={colors.muted} keyboardType={x.kb ?? "default"} style={s.input}
                autoCapitalize={x.k === "customer_name" || x.k === "address" ? "words" : "sentences"} />
            </View>
          ))}
          <Pressable testID="new-delivery-priority" onPress={() => setPriority(!priority)} style={s.check} accessibilityRole="checkbox" accessibilityState={{ checked: priority }}>
            <Checkbox value={priority} onValueChange={setPriority} color={priority ? colors.brandPrimary : undefined} />
            <Mono style={{ flex: 1, fontSize: 12 }}>PRIORITY DELIVERY (SURCHARGE APPLIES)</Mono>
          </Pressable>
          <Mono style={s.hint}>NO HEALTH INFO IN NOTES: NO MEDICATION NAMES, DOSAGES OR DIAGNOSES.</Mono>
        </KeyboardAwareScrollView>
        {error ? <View style={s.err} testID="new-delivery-error"><Mono style={s.errText}>{error}</Mono></View> : null}
        <Btn testID="new-delivery-save" label="ADD DELIVERY" icon="add-circle" loading={busy} onPress={save} style={{ marginTop: spacing.md }} />
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  backdrop: { flex: 1, backgroundColor: c.surfaceInverse, opacity: 0.5 },
  sheet: { backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.lg, maxHeight: "92%" },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.muted },
  hint: { fontSize: 11, color: c.muted },
  input: { height: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 15, color: c.onSurface },
  check: { flexDirection: "row", gap: spacing.sm, alignItems: "center", minHeight: 44 },
  err: { backgroundColor: c.brandPrimary, padding: spacing.md, marginTop: spacing.sm },
  errText: { color: c.onBrandPrimary, fontSize: 12, fontWeight: "700" },
}));
