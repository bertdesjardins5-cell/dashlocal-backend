import React, { useState } from "react";
import { Modal, Pressable, TextInput, View } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { Btn, Display, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

export type ImportRow = {
  id: string; line: number; status: string; error: string | null;
  data: { customer_name: string; phone: string; address: string; notes: string; window_date: string; window_start: string; window_end: string };
};

const FIELDS: { k: keyof ImportRow["data"]; label: string; ph: string }[] = [
  { k: "customer_name", label: "CUSTOMER NAME", ph: "Jane Doe" },
  { k: "phone", label: "PHONE", ph: "+1 415 555 0123" },
  { k: "address", label: "STREET ADDRESS (SAN FRANCISCO)", ph: "1 Market St" },
  { k: "notes", label: "NOTES (OPTIONAL)", ph: "Leave at reception" },
  { k: "window_date", label: "DELIVERY DATE (OPTIONAL, YYYY-MM-DD)", ph: "2026-06-30" },
  { k: "window_start", label: "WINDOW FROM (OPTIONAL, HH:MM)", ph: "14:00" },
  { k: "window_end", label: "WINDOW TO (OPTIONAL, HH:MM)", ph: "16:00" },
];

// Merchant sheet: correct a flagged CSV row and try it again, or drop it.
export default function FixRowSheet({ row, onClose, onDone, notify }: {
  row: ImportRow; onClose: () => void; onDone: () => void; notify: (m: string, tone?: "dark" | "red" | "green") => void;
}) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [data, setData] = useState(row.data);
  const [error, setError] = useState(row.error);
  const [busy, setBusy] = useState<"retry" | "drop" | null>(null);

  const retry = async () => {
    setBusy("retry");
    try {
      const r = await api<ImportRow & { tracking_code: string | null }>(`/import-rows/${row.id}`, { method: "PUT", body: data });
      if (r.status === "created") {
        notify(`ORDER ${r.tracking_code} CREATED`, "green");
        onDone();
      } else {
        setError(r.error);
      }
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  const drop = async () => {
    setBusy("drop");
    try {
      await api(`/import-rows/${row.id}`, { method: "DELETE" });
      notify(`ROW ${row.line} REMOVED`);
      onDone();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal transparent animationType="slide" visible onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} testID="fix-row-backdrop" />
      <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="fix-row-sheet">
        <KeyboardAwareScrollView style={{ flexShrink: 1 }} contentContainerStyle={{ gap: spacing.sm }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
          <View style={{ flexDirection: "row", alignItems: "center" }}>
            <Display style={{ fontSize: 22, flex: 1 }} numberOfLines={2}>FIX ROW {row.line}</Display>
            <Pressable testID="fix-row-close" onPress={onClose} style={s.close}><Ionicons name="close" size={24} color={colors.onSurface} /></Pressable>
          </View>
          {error ? (
            <View style={s.err} testID="fix-row-error"><Mono style={s.errText}>{error}</Mono></View>
          ) : null}
          {FIELDS.map((f) => (
            <View key={f.k} style={{ gap: 4 }}>
              <Mono style={s.label}>{f.label}</Mono>
              <TextInput
                testID={`fix-row-${f.k}`}
                value={data[f.k]}
                onChangeText={(v) => setData((d) => ({ ...d, [f.k]: v }))}
                placeholder={f.ph}
                placeholderTextColor={colors.muted}
                keyboardType={f.k === "phone" ? "phone-pad" : "default"}
                style={s.input}
              />
            </View>
          ))}
        </KeyboardAwareScrollView>
        <View style={{ flexDirection: "row", gap: spacing.sm, paddingTop: spacing.md }}>
          <Btn testID="fix-row-drop-button" label="REMOVE" icon="trash" variant="outline" loading={busy === "drop"} onPress={drop} style={{ flex: 1 }} />
          <Btn testID="fix-row-retry-button" label="SAVE & RETRY" icon="refresh" loading={busy === "retry"} onPress={retry} style={{ flex: 2 }} />
        </View>
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  backdrop: { flex: 1, backgroundColor: c.surfaceInverse, opacity: 0.5 },
  sheet: { backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.lg, maxHeight: "88%" },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.muted },
  input: { height: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 15, color: c.onSurface },
  err: { backgroundColor: c.brandPrimary, padding: spacing.md },
  errText: { color: c.onBrandPrimary, fontSize: 12, fontWeight: "700" },
}));
