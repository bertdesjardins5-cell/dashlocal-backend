import React, { useState } from "react";
import { Pressable, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { Btn, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type SavedNote = { id: string; phone: string; note: string; updated_at: string };

// Store's saved delivery notes for regular customers (by phone). Filled into every new order for that customer.
export default function SavedNotesCard({ merchantId, notify }: { merchantId: string; notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["saved-notes", merchantId], queryFn: () => api<SavedNote[]>(`/merchants/${merchantId}/saved-notes`) });
  const [phone, setPhone] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const save = async () => {
    setBusy("save");
    try {
      await api(`/merchants/${merchantId}/saved-notes`, { method: "PUT", body: { phone, note } });
      setPhone("");
      setNote("");
      await q.refetch();
      notify("NOTE SAVED · ADDED TO THEIR NEXT ORDERS", "green");
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const remove = async (id: string) => {
    setBusy(id);
    try {
      await api(`/merchants/${merchantId}/saved-notes/${id}`, { method: "DELETE" });
      await q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  return (
    <View style={s.block} testID="saved-notes-card">
      <Mono style={s.title}>SAVED DELIVERY NOTES</Mono>
      <Mono style={s.meta}>Regular customers&apos; gate codes and drop-off tips. Filled into every new order with that phone number. If none is saved here, the customer&apos;s own last note is reused.</Mono>
      {(q.data ?? []).map((n) => (
        <View key={n.id} style={s.item} testID={`saved-note-${n.id}`}>
          <View style={{ flex: 1, gap: 2 }}>
            <Mono style={s.phone}>{n.phone}</Mono>
            <Mono style={s.note}>{n.note}</Mono>
          </View>
          <Pressable testID={`saved-note-delete-${n.id}`} disabled={busy === n.id} onPress={() => remove(n.id)} style={s.del} hitSlop={4}>
            <Ionicons name="trash" size={18} color={colors.onSurface} />
          </Pressable>
        </View>
      ))}
      <TextInput testID="saved-note-phone" value={phone} onChangeText={setPhone} placeholder="Customer phone" placeholderTextColor={colors.muted} keyboardType="phone-pad" style={s.input} />
      <TextInput testID="saved-note-text" value={note} onChangeText={setNote} placeholder="Gate code 1234 · leave at back door" placeholderTextColor={colors.muted} maxLength={200} style={s.input} />
      <Btn testID="saved-note-save" label="SAVE NOTE" icon="bookmark" variant="dark" loading={busy === "save"} disabled={!phone.trim() || !note.trim()} onPress={save} />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  title: { fontSize: 13, fontWeight: "700" },
  meta: { fontSize: 11, color: c.muted },
  item: { flexDirection: "row", alignItems: "center", gap: spacing.sm, borderWidth: 2, borderColor: c.border, padding: spacing.sm },
  phone: { fontSize: 12, fontWeight: "700" },
  note: { fontSize: 13 },
  del: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  input: { height: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface, backgroundColor: c.surface },
}));
