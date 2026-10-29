import React, { useState } from "react";
import { Pressable, RefreshControl, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useLocalSearchParams } from "expo-router";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api, dayLabel, fmtDepotTime } from "@/src/api";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, useToast } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type DriverAccount = { id: string; name: string; phone: string; vehicle: string; has_pin: boolean; last_login_at: string | null; pin_request_at: string | null };

// Admin: set / reset the 6-digit PIN each driver signs in with.
export default function DriverPins() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const { focus } = useLocalSearchParams<{ focus?: string }>();
  const q = useQuery({ queryKey: ["driver-accounts"], queryFn: () => api<DriverAccount[]>("/admin/driver-accounts"), refetchInterval: 30000 });
  const dq = useQuery({ queryKey: ["dispatch-phone"], queryFn: () => api<{ phone: string | null }>("/admin/dispatch-phone") });
  const [dPhone, setDPhone] = useState<string | null>(null);
  const saveDispatch = async () => {
    setBusy(true);
    try {
      const r = await api<{ phone: string | null }>("/admin/dispatch-phone", { method: "PUT", body: { phone: dPhone } });
      toast.show(r.phone ? `FORGOT-PIN TEXTS GO TO ${r.phone}` : "FORGOT-PIN REQUESTS BY EMAIL ONLY", "green");
      setDPhone(null);
      dq.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(false);
    }
  };
  const requests = (q.data ?? []).filter((d) => d.pin_request_at).sort((a, b) => (a.id === focus ? -1 : b.id === focus ? 1 : 0));
  const [open, setOpen] = useState<string | null>(null);
  const [pin, setPin] = useState("");
  const [texting, setTexting] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [nd, setNd] = useState({ name: "", phone: "", vehicle: "", pin: "" });

  const addDriver = async () => {
    setBusy(true);
    try {
      const r = await api<{ invite: string }>("/admin/drivers", { method: "POST", body: nd });
      const inviteMsg = r.invite === "sent" ? " · WELCOME TEXT ON ITS WAY" : " · WELCOME TEXT NOT SENT — SHARE THE PIN IN PERSON";
      toast.show(`DRIVER ${nd.name.trim().toUpperCase()} ADDED${inviteMsg}`, r.invite === "sent" ? "green" : "dark");
      setNd({ name: "", phone: "", vehicle: "", pin: "" });
      setAdding(false);
      q.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

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

  const textPin = async (d: DriverAccount) => {
    setTexting(d.id);
    try {
      const r = await api<{ invite: string; pin: string | null }>(`/admin/drivers/${d.id}/pin/text`, { method: "POST" });
      toast.show(r.invite === "sent" ? `NEW PIN TEXTED TO ${d.name.toUpperCase()}` : `TEXT NOT SENT — NEW PIN IS ${r.pin}, SHARE IT IN PERSON`, r.invite === "sent" ? "green" : "dark");
      q.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setTexting(null);
    }
  };

  return (
    <View style={s.root} testID="driver-pins-screen">
      <Header title="DRIVER PINS" subtitle="Phone number + 6-digit PIN sign-in" />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : (
        <KeyboardAwareScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} keyboardShouldPersistTaps="handled" bottomOffset={24}
          refreshControl={<RefreshControl refreshing={false} onRefresh={() => { q.refetch(); }} />}>
          <Mono style={s.note}>New drivers get a welcome text with the sign-in link and their PIN. Drivers sign in with the phone number on file and this PIN.</Mono>
          {requests.length ? (
            <View style={[s.row, { backgroundColor: colors.warning }]} testID="pin-requests">
              <Mono style={[s.smallBtnText, { color: colors.onWarning }]}>FORGOT PIN · {requests.length} DRIVER{requests.length === 1 ? "" : "S"} WAITING</Mono>
              {requests.map((d) => (
                <View key={d.id} style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }} testID={`pin-request-${d.id}`}>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Display style={[s.name, { color: colors.onWarning }]} numberOfLines={1}>{d.name}</Display>
                    <Mono style={[s.meta, { color: colors.onWarning }]}>{d.phone} · ASKED {dayLabel(d.pin_request_at!.slice(0, 10)).toUpperCase()} {fmtDepotTime(d.pin_request_at!)}</Mono>
                  </View>
                  <Btn testID={`pin-request-text-${d.id}`} label="TEXT NEW PIN" icon="send" variant="dark" loading={texting === d.id} onPress={() => textPin(d)} />
                </View>
              ))}
            </View>
          ) : null}
          <View style={s.row} testID="dispatch-phone-section">
            <Mono style={s.smallBtnText}>TEXT FORGOT-PIN REQUESTS TO</Mono>
            {dPhone === null ? (
              <Pressable testID="dispatch-phone-edit" onPress={() => setDPhone(dq.data?.phone ?? "")} style={s.smallBtn}>
                <Mono style={s.smallBtnText}>{dq.data?.phone ? `${dq.data.phone} · EDIT` : "NOT SET (EMAIL ONLY) · ADD MOBILE"}</Mono>
              </Pressable>
            ) : (
              <View style={{ flexDirection: "row", gap: spacing.sm }}>
                <TextInput testID="dispatch-phone-input" value={dPhone} onChangeText={setDPhone} keyboardType="phone-pad" placeholder="Dispatch mobile, e.g. 519 555 0199"
                  placeholderTextColor={colors.muted} style={[s.field, { flex: 1, minWidth: 0 }]} />
                <Btn testID="dispatch-phone-save" label="SAVE" loading={busy} onPress={saveDispatch} />
              </View>
            )}
          </View>
          <View style={s.row} testID="add-driver-section">
            {adding ? (
              <>
                <Display style={s.name}>NEW DRIVER</Display>
                <TextInput testID="new-driver-name" value={nd.name} onChangeText={(v) => setNd({ ...nd, name: v })} placeholder="Full name"
                  placeholderTextColor={colors.muted} style={s.field} />
                <TextInput testID="new-driver-phone" value={nd.phone} onChangeText={(v) => setNd({ ...nd, phone: v })} placeholder="Mobile, e.g. 519 555 0123"
                  keyboardType="phone-pad" placeholderTextColor={colors.muted} style={s.field} />
                <TextInput testID="new-driver-vehicle" value={nd.vehicle} onChangeText={(v) => setNd({ ...nd, vehicle: v })} placeholder="Vehicle, e.g. VAN-01"
                  autoCapitalize="characters" placeholderTextColor={colors.muted} style={s.field} />
                <TextInput testID="new-driver-pin" value={nd.pin} onChangeText={(v) => setNd({ ...nd, pin: v.replace(/\D/g, "").slice(0, 6) })} placeholder="6-digit PIN"
                  keyboardType="number-pad" maxLength={6} placeholderTextColor={colors.muted} style={s.input} />
                <View style={{ flexDirection: "row", gap: spacing.sm }}>
                  <Btn testID="new-driver-save" label="ADD DRIVER" loading={busy} style={{ flex: 1 }}
                    disabled={!nd.name.trim() || !nd.phone.trim() || !nd.vehicle.trim() || nd.pin.length !== 6} onPress={addDriver} />
                  <Btn testID="new-driver-cancel" label="CANCEL" variant="outline" onPress={() => setAdding(false)} />
                </View>
              </>
            ) : (
              <Btn testID="add-driver-button" label="ADD DRIVER" icon="person-add" variant="dark" onPress={() => setAdding(true)} />
            )}
          </View>
          {q.data?.length === 0 ? <Mono style={s.note} testID="no-drivers">No drivers yet — add your first one above.</Mono> : null}
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
              <View style={{ flexDirection: "row", gap: spacing.sm, flexWrap: "wrap" }}>
                <Pressable testID={`driver-pin-toggle-${d.id}`} onPress={() => { setPin(""); setOpen(open === d.id ? null : d.id); }} style={s.smallBtn}>
                  <Mono style={s.smallBtnText}>{open === d.id ? "CANCEL" : d.has_pin ? "RESET PIN" : "SET PIN"}</Mono>
                </Pressable>
                <Pressable testID={`driver-pin-text-${d.id}`} disabled={texting === d.id} onPress={() => textPin(d)} style={[s.smallBtn, texting === d.id && { opacity: 0.5 }]}>
                  <Mono style={s.smallBtnText}>{texting === d.id ? "SENDING…" : "NEW PIN BY TEXT"}</Mono>
                </Pressable>
              </View>
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
  field: { height: 52, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 15, color: c.onSurface },
  smallBtn: { minHeight: 44, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center", alignSelf: "flex-start" },
  smallBtnText: { fontSize: 11, fontWeight: "700" },
}));
