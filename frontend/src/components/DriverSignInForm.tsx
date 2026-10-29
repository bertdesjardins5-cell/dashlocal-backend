import React, { useState } from "react";
import { Text, TextInput, View } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { api } from "@/src/api";
import { SessionUser, setSession } from "@/src/session";
import BrandButton from "@/src/components/brand/BrandButton";
import { useBrandForm } from "@/src/components/brand/formStyles";
import { useTheme } from "@/src/theme";

// Driver sign-in: phone number + 6-digit PIN set by dispatch (DashLocal brand styling).
export default function DriverSignInForm({ onSignedIn, initialPhone = "", notice, scroll = true }: { onSignedIn?: (u: SessionUser) => void; initialPhone?: string; notice?: string | null; scroll?: boolean }) {
  const s = useBrandForm();
  const { colors } = useTheme();
  const [phone, setPhone] = useState(initialPhone);
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!phone.trim() || pin.length !== 6) return setError("Enter your phone number and 6-digit PIN");
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ token: string; user: SessionUser }>("/auth/driver-login", { method: "POST", body: { phone: phone.trim(), pin } });
      await setSession(r.token, r.user);
      onSignedIn?.(r.user);
    } catch (e: any) {
      setError(e.message);
      setPin("");
    } finally {
      setBusy(false);
    }
  };

  const body = (
    <View style={s.card}>
      <Text style={s.title}>Start your shift</Text>
      <Text style={s.hint}>Sign in with your phone number and the 6-digit PIN from dispatch. Forgot it? Ask dispatch to reset it.</Text>
      {notice && !error ? <View style={[s.err, { borderLeftColor: colors.brandForest }]} testID="driver-sign-in-notice"><Text style={s.errText}>{notice}</Text></View> : null}
      {error ? <View style={s.err} testID="driver-sign-in-error"><Text style={s.errText}>{error}</Text></View> : null}
      <Text style={s.label}>Phone number</Text>
      <TextInput testID="driver-sign-in-phone" value={phone} onChangeText={setPhone} keyboardType="phone-pad" textContentType="telephoneNumber"
        autoComplete="tel" placeholder="(519) 555-0101" placeholderTextColor={colors.brandMuted} style={s.input} />
      <Text style={s.label}>PIN</Text>
      <TextInput testID="driver-sign-in-pin" value={pin} onChangeText={(v) => setPin(v.replace(/\D/g, "").slice(0, 6))} keyboardType="number-pad"
        secureTextEntry maxLength={6} placeholder="••••••" placeholderTextColor={colors.brandMuted} style={[s.input, s.pin]} onSubmitEditing={submit} returnKeyType="go" />
      <BrandButton testID="driver-sign-in-submit" label="Sign in" icon="arrow-forward" loading={busy} onPress={submit} style={{ marginTop: 8 }} />
    </View>
  );
  if (!scroll) return <View testID="driver-sign-in-form">{body}</View>;
  return (
    <KeyboardAwareScrollView style={s.screen} contentContainerStyle={s.wrap} keyboardShouldPersistTaps="handled" bottomOffset={24} testID="driver-sign-in-form">
      {body}
    </KeyboardAwareScrollView>
  );
}
