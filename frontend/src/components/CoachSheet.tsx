import React, { useState } from "react";
import { Modal, Pressable, ScrollView, TextInput, View } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { Btn, Display, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

export type CoachTarget = { driverId: string; name: string; reasons: string[] };

const TEMPLATES: Record<string, string> = {
  LATE: "A few customers flagged late drops this week. Check windows on your manifest and message the customer early if you're running behind.",
  "HARD TO FIND PARCEL": "Some customers couldn't find their parcel. Please leave it somewhere visible and take a clear photo showing where.",
  "DRIVER NEVER RANG THE BELL": "Please ring or knock at every drop, even when leaving at the door.",
  "PARCEL LEFT IN THE RAIN": "When it's wet, leave parcels under cover or in a safe sheltered spot.",
};

// Admin sheet: send a coaching note to a driver, pre-filled from their top complaint reasons.
export default function CoachSheet({ target, onClose, notify }: { target: CoachTarget; onClose: () => void; notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [reason, setReason] = useState<string | null>(target.reasons[0] ?? null);
  const [note, setNote] = useState(reason ? TEMPLATES[reason] ?? "" : "");
  const [saving, setSaving] = useState(false);

  const pick = (r: string) => {
    setReason(r);
    setNote(TEMPLATES[r] ?? `Customers mentioned "${r.toLowerCase()}" this week — let's work on it together.`);
  };

  const send = async () => {
    setSaving(true);
    try {
      await api(`/drivers/${target.driverId}/coaching`, { method: "POST", body: { note, reason } });
      notify(`COACHING NOTE SENT TO ${target.name.split(" ")[0].toUpperCase()}`, "green");
      onClose();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal transparent animationType="slide" visible onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} testID="coach-backdrop" />
      <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="coach-sheet">
        <KeyboardAwareScrollView contentContainerStyle={{ gap: spacing.md }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
          <View style={{ flexDirection: "row", alignItems: "center" }}>
            <Display style={{ fontSize: 22, flex: 1 }} numberOfLines={2}>COACH {target.name.toUpperCase()}</Display>
            <Pressable testID="coach-close" onPress={onClose} style={s.close}><Ionicons name="close" size={24} color={colors.onSurface} /></Pressable>
          </View>
          {target.reasons.length ? (
            <>
              <Mono style={s.label}>TOP COMPLAINTS THIS WEEK</Mono>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ height: 60, flexGrow: 0, marginHorizontal: -spacing.lg }} contentContainerStyle={{ gap: spacing.sm, alignItems: "center", paddingHorizontal: spacing.lg }}>
                {target.reasons.map((r, i) => (
                  <Pressable key={r} testID={`coach-reason-${i}`} onPress={() => pick(r)} style={[s.chip, reason === r && { backgroundColor: colors.surfaceInverse }]}>
                    <Mono style={[s.chipText, reason === r && { color: colors.onSurfaceInverse }]}>{r}</Mono>
                  </Pressable>
                ))}
              </ScrollView>
            </>
          ) : null}
          <Mono style={s.label}>NOTE TO DRIVER</Mono>
          <TextInput testID="coach-note-input" value={note} onChangeText={setNote} multiline placeholder="Write a short, encouraging note" placeholderTextColor={colors.muted} style={s.input} />
          <Btn testID="coach-send-button" label="SEND NOTE" icon="send" loading={saving} disabled={!note.trim()} onPress={send} />
        </KeyboardAwareScrollView>
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  backdrop: { flex: 1, backgroundColor: c.surfaceInverse, opacity: 0.5 },
  sheet: { backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.lg, maxHeight: "85%" },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  chip: { height: 44, flexShrink: 0, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 11, fontWeight: "700" },
  input: { minHeight: 110, maxHeight: 220, borderWidth: 2, borderColor: c.border, padding: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface, textAlignVertical: "top" },
}));
