import React, { useState } from "react";
import { Pressable, TextInput, View } from "react-native";
import { api } from "@/src/api";
import { Btn, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Notify = (m: string, tone?: "dark" | "red" | "green") => void;

// Law offices: tag a delivery with the client file / matter number (printed on the signed receipt).
export default function MatterTag({ merchantId, stopId, value, onSaved, notify }: {
  merchantId: string; stopId: string; value?: string | null; onSaved: () => void; notify: Notify;
}) {
  const s = useStyles();
  const { colors } = useTheme();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(value ?? "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      const r = await api<{ matter_ref: string | null }>(`/merchants/${merchantId}/stops/${stopId}/matter`, { method: "PUT", body: { matter_ref: text } });
      notify(r.matter_ref ? `MATTER ${r.matter_ref} SAVED` : "MATTER REMOVED", "green");
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
      <Pressable testID={`matter-edit-${stopId}`} onPress={() => { setText(value ?? ""); setEditing(true); }} style={s.btn}>
        <Mono style={s.btnText}>{value ? `MATTER ${value} ✎` : "+ MATTER / FILE NO."}</Mono>
      </Pressable>
    );
  }
  return (
    <View style={s.row} testID={`matter-editor-${stopId}`}>
      <TextInput testID={`matter-input-${stopId}`} value={text} onChangeText={setText} maxLength={60} autoFocus autoCapitalize="characters"
        autoCorrect={false} placeholder="e.g. 2026-0142" placeholderTextColor={colors.muted} style={s.input} onSubmitEditing={save} />
      <Btn testID={`matter-save-${stopId}`} label="SAVE" loading={busy} onPress={save} />
      <Pressable testID={`matter-cancel-${stopId}`} onPress={() => setEditing(false)} style={s.btn}>
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
