import React, { useState } from "react";
import { Pressable, RefreshControl, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api, fmtDepotTime } from "@/src/api";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, useToast } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type DriverAccount = { id: string; name: string; phone: string; vehicle: string; has_pin: boolean; last_login_at: string | null };

// Admin: set / reset the 6-digit PIN each driver signs in with.
export default function DriverPins() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const q = useQuery({ queryKey: ["driver-accounts"], queryFn: () => api<DriverAccount[]>("/admin/driver-accounts") });
  const [open, setOpen] = useState<string | null>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);

  const save = async (d: DriverAccount) => {
    setBusy(true);
    try {
      await api(`/admin/drivers/${d.id}/pin`, { method: "PUT", body: { pin } });
      toast.show(`PIN SET FOR ${d.name.toUpperCase()}`, "green");
      setOpen(null);
      setPin("");
      q.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.root} testID="driver-pins-screen">
      <Header title="DRIVER PINS" subtitle="Phone number + 6-digit PIN sign-in" />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : (
        <KeyboardAwareScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} keyboardShouldPersistTaps="handled" bottomOffset={24}
          refreshControl={<RefreshControl refreshing={false} onRefresh={() => { q.refetch(); }} />}>
          <Mono style={s.note}>Drivers sign in with the phone number on file and this PIN. Tell the driver their new PIN in person — it is never shown again.</Mono>
          {q.data?.map((d) => (
            <View key={d.id} style={s.row} testID={`driver-pin-${d.id}`}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }}>
                <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                  <Display style={s.name} numberOfLines={1}>{d.name}</Display>
                  <Mono style={s.meta}>{d.phone} · {d.vehicle}{d.last_login_at ? ` · LAST SIGN-IN ${fmtDepotTime(d.last_login_at)}` : ""}</Mono>
                </View>
                <Pill label={d.has_pin ? "PIN set" : "no PIN"} tone={d.has_pin ? "green" : "red"} />
              </View>
              {open === d.id ? (
                <View style={{ flexDirection: "row", gap: spacing.sm }}>
                  <TextInput testID={`driver-pin-input-${d.id}`} value={pin} onChangeText={(v) => setPin(v.replace(/\D/g, "").slice(0, 6))}
                    keyboardType="number-pad" maxLength={6} placeholder="6 digits" placeholderTextColor={colors.muted} style={[s.input, { flex: 1, minWidth: 0 }]} />
                  <Btn testID={`driver-pin-save-${d.id}`} label="SAVE" loading={busy} disabled={pin.length !== 6} onPress={() => save(d)} />
                </View>
              ) : null}
              <Pressable testID={`driver-pin-toggle-${d.id}`} onPress={() => { setPin(""); setOpen(open === d.id ? null : d.id); }} style={s.smallBtn}>
                <Mono style={s.smallBtnText}>{open === d.id ? "CANCEL" : d.has_pin ? "RESET PIN" : "SET PIN"}</Mono>
              </Pressable>
            </View>
          ))}
        </KeyboardAwareScrollView>
      )}
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  note: { padding: spacing.lg, fontSize: 12, color: c.muted, borderBottomWidth: 2, borderColor: c.border },
  row: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  name: { fontSize: 18 },
  meta: { fontSize: 11, color: c.muted },
  input: { height: 56, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 18, letterSpacing: 6, color: c.onSurface },
  smallBtn: { minHeight: 44, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center", alignSelf: "flex-start" },
  smallBtnText: { fontSize: 11, fontWeight: "700" },
}));
