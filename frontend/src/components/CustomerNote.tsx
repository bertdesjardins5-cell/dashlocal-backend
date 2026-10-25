import React, { useState } from "react";
import { TextInput, View } from "react-native";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { Btn, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Props = { stopId: string; note: string | null; source: string | null; read: boolean; locked: boolean; orderToken: string; onSaved: (note: string | null) => void; onError: (m: string) => void };

// Verified customer leaves a note for the driver (gate code, "leave at back door"). Locked once the driver arrives.
export default function CustomerNote({ stopId, note, source, read, locked, orderToken, onSaved, onError }: Props) {
  const s = useStyles();
  const { colors } = useTheme();
  const [text, setText] = useState(note ?? "");
  const [saving, setSaving] = useState(false);
  const dirty = text.trim() !== (note ?? "");
  const save = async () => {
    setSaving(true);
    try {
      const r = await api<{ customer_note: string | null }>(`/stops/${stopId}/customer-note`, { method: "PUT", body: { note: text }, headers: { "X-Order-Token": orderToken } });
      setText(r.customer_note ?? "");
      onSaved(r.customer_note);
    } catch (e: any) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <View style={s.box} testID="customer-note-section">
      <Mono style={s.label}>NOTE FOR YOUR DRIVER</Mono>
      {note ? (
        <View style={s.receipt} testID={read ? "customer-note-read" : "customer-note-unread"}>
          <Ionicons name={read ? "checkmark-done" : "checkmark"} size={16} color={read ? colors.success : colors.muted} />
          <Mono style={[s.meta, read && { color: colors.success, fontWeight: "700" }]}>
            {read ? "DRIVER HAS READ YOUR NOTE" : source === "saved" ? "SAVED BY THE STORE · NOT READ YET" : source === "last" ? "FROM YOUR LAST ORDER · NOT READ YET" : "SENT · NOT READ YET"}
          </Mono>
        </View>
      ) : null}
      {locked ? (
        <Mono style={s.meta} testID="customer-note-locked">{note ? `"${note}" · ` : ""}DRIVER IS AT YOUR DOOR — NOTE LOCKED</Mono>
      ) : (
        <>
          <TextInput
            testID="customer-note-input"
            value={text}
            onChangeText={setText}
            placeholder="Gate code 1234 · leave at back door"
            placeholderTextColor={colors.muted}
            maxLength={200}
            multiline
            style={s.input}
          />
          <View style={s.row}>
            <Mono style={[s.meta, { flex: 1 }]}>{text.length}/200</Mono>
            <View style={{ minWidth: 140 }}>
              <Btn testID="customer-note-save" label={text.trim() ? "SAVE NOTE" : "CLEAR NOTE"} icon="send" variant="dark" loading={saving} disabled={!dirty} onPress={save} />
            </View>
          </View>
        </>
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { gap: spacing.xs, borderWidth: 2, borderColor: c.border, padding: spacing.md },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  meta: { fontSize: 12, color: c.muted },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  receipt: { flexDirection: "row", alignItems: "center", gap: spacing.xs },
  input: { minHeight: 64, borderWidth: 2, borderColor: c.border, padding: spacing.sm, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface, backgroundColor: c.surface, textAlignVertical: "top" },
}));
