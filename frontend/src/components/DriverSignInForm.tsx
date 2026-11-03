import React, { useEffect, useState } from "react";
import { Platform, Pressable, Text, TextInput, View } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import * as SecureStore from "expo-secure-store";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { api } from "@/src/api";
import { SessionUser, setSession } from "@/src/session";
import BrandButton from "@/src/components/brand/BrandButton";
import { useBrandForm } from "@/src/components/brand/formStyles";
import { useTheme } from "@/src/theme";

// The phone number of the last driver who signed in on this device, so each shift only needs the PIN.
const PHONE_KEY = "dashlocal_driver_phone";
export const rememberedPhone = {
  get: () => (Platform.OS === "web" ? AsyncStorage.getItem(PHONE_KEY) : SecureStore.getItemAsync(PHONE_KEY)),
  set: (v: string) => (Platform.OS === "web" ? AsyncStorage.setItem(PHONE_KEY, v) : SecureStore.setItemAsync(PHONE_KEY, v)),
  del: () => (Platform.OS === "web" ? AsyncStorage.removeItem(PHONE_KEY) : SecureStore.deleteItemAsync(PHONE_KEY)),
};

// Driver sign-in: phone number + 6-digit PIN set by dispatch (ArriveWise brand styling).
export default function DriverSignInForm({ onSignedIn, initialPhone = "", notice, scroll = true }: { onSignedIn?: (u: SessionUser) => void; initialPhone?: string; notice?: string | null; scroll?: boolean }) {
  const s = useBrandForm();
  const { colors } = useTheme();
  const [phone, setPhone] = useState(initialPhone);
  const [saved, setSaved] = useState<string | null>(null);
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);

  useEffect(() => {
    if (initialPhone) return;
    rememberedPhone.get().then((p) => { if (p) { setSaved(p); setPhone(p); } }).catch(() => {});
  }, [initialPhone]);

  const forgetPhone = () => {
    rememberedPhone.del().catch(() => {});
    setSaved(null);
    setPhone("");
    setPin("");
    setInfo(null);
  };

  const forgotPin = async () => {
    setInfo(null);
    if (!phone.trim()) return setError("Enter your phone number first, then tap Forgot PIN");
    setAsking(true);
    setError(null);
    try {
      const r = await api<{ message: string }>("/auth/driver-forgot-pin", { method: "POST", body: { phone: phone.trim() } });
      setInfo(r.message);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAsking(false);
    }
  };

  const submit = async () => {
    if (!phone.trim() || pin.length !== 6) return setError("Enter your phone number and 6-digit PIN");
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ token: string; user: SessionUser }>("/auth/driver-login", { method: "POST", body: { phone: phone.trim(), pin } });
      await rememberedPhone.set(phone.trim()).catch(() => {});
      await setSession(r.token, r.user);
      onSignedIn?.(r.user);
    } catch (e: any) {
      setError(e.message);
      setPin("");
    } finally {
      setBusy(false);
    }
  };

  const msg = info ?? (!error ? notice : null);
  const body = (
    <View style={s.card}>
      <Text style={s.title}>{saved ? "Welcome back" : "Start your shift"}</Text>
      <Text style={s.hint}>{saved ? "Enter your 6-digit PIN to start your shift." : "Sign in with your phone number and the 6-digit PIN from dispatch."}</Text>
      {msg && !error ? <View style={[s.err, { borderLeftColor: colors.brandForest, backgroundColor: colors.brandSoftGray }]} testID={info ? "driver-forgot-pin-sent" : "driver-sign-in-notice"}><Text style={s.errText}>{msg}</Text></View> : null}
      {error ? <View style={s.err} testID="driver-sign-in-error"><Text style={s.errText}>{error}</Text></View> : null}
      <Text style={s.label}>Phone number</Text>
      {saved ? (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
          <Text style={[s.input, { flex: 1, lineHeight: 48 }]} testID="driver-saved-phone">{saved}</Text>
          <Pressable testID="driver-not-you" onPress={forgetPhone} hitSlop={8} style={{ minHeight: 44, justifyContent: "center" }}>
            <Text style={[s.hint, { color: colors.brandForest, textDecorationLine: "underline" }]}>Not you?</Text>
          </Pressable>
        </View>
      ) : (
        <TextInput testID="driver-sign-in-phone" value={phone} onChangeText={setPhone} keyboardType="phone-pad" textContentType="telephoneNumber"
          autoComplete="tel" placeholder="(519) 555-0101" placeholderTextColor={colors.brandMuted} style={s.input} />
      )}
      <Text style={s.label}>PIN</Text>
      <TextInput testID="driver-sign-in-pin" key={saved ? "pin-saved" : "pin"} value={pin} onChangeText={(v) => setPin(v.replace(/\D/g, "").slice(0, 6))} keyboardType="number-pad" autoFocus={!!saved}
        secureTextEntry maxLength={6} placeholder="••••••" placeholderTextColor={colors.brandMuted} style={[s.input, s.pin]} onSubmitEditing={submit} returnKeyType="go" />
      <BrandButton testID="driver-sign-in-submit" label="Sign in" icon="arrow-forward" loading={busy} onPress={submit} style={{ marginTop: 8 }} />
      <Pressable testID="driver-forgot-pin" onPress={forgotPin} disabled={asking} style={{ minHeight: 44, alignItems: "center", justifyContent: "center", opacity: asking ? 0.5 : 1 }}>
        <Text style={[s.hint, { color: colors.brandForest, textDecorationLine: "underline" }]}>{asking ? "Asking dispatch…" : "Forgot PIN? Ask dispatch for a new one"}</Text>
      </Pressable>
    </View>
  );
  if (!scroll) return <View testID="driver-sign-in-form">{body}</View>;
  return (
    <KeyboardAwareScrollView style={s.screen} contentContainerStyle={s.wrap} keyboardShouldPersistTaps="handled" bottomOffset={24} testID="driver-sign-in-form">
      {body}
    </KeyboardAwareScrollView>
  );
}
