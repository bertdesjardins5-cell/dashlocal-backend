import React, { useState } from "react";
import { TextInput } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { View } from "react-native";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { SessionUser, setSession } from "@/src/session";
import { Btn, Display, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

// Driver sign-in: phone number + 6-digit PIN set by dispatch.
export default function DriverSignInForm() {
  const s = useStyles();
  const { colors } = useTheme();
  const [phone, setPhone] = useState("");
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
    } catch (e: any) {
      setError(e.message);
      setPin("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAwareScrollView contentContainerStyle={s.wrap} keyboardShouldPersistTaps="handled" bottomOffset={24} testID="driver-sign-in-form">
      <View style={s.icon}><Ionicons name="car" size={28} color={colors.onSurfaceInverse} /></View>
      <Display style={s.title}>START YOUR SHIFT</Display>
      <Mono style={s.hint}>Sign in with your phone number and the 6-digit PIN from dispatch. Forgot it? Ask dispatch to reset it.</Mono>
      {error ? <View style={s.err} testID="driver-sign-in-error"><Mono style={s.errText}>{error}</Mono></View> : null}
      <Mono style={s.label}>PHONE NUMBER</Mono>
      <TextInput testID="driver-sign-in-phone" value={phone} onChangeText={setPhone} keyboardType="phone-pad" textContentType="telephoneNumber"
        autoComplete="tel" placeholder="(415) 555-0101" placeholderTextColor={colors.muted} style={s.input} />
      <Mono style={s.label}>PIN</Mono>
      <TextInput testID="driver-sign-in-pin" value={pin} onChangeText={(v) => setPin(v.replace(/\D/g, "").slice(0, 6))} keyboardType="number-pad"
        secureTextEntry maxLength={6} placeholder="••••••" placeholderTextColor={colors.muted} style={[s.input, s.pin]} onSubmitEditing={submit} returnKeyType="go" />
      <Btn testID="driver-sign-in-submit" label="SIGN IN" icon="log-in" loading={busy} onPress={submit} />
    </KeyboardAwareScrollView>
  );
}

const useStyles = makeStyles((c) => ({
  wrap: { padding: spacing.xl, gap: spacing.md },
  icon: { width: 56, height: 56, backgroundColor: c.surfaceInverse, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 28, letterSpacing: -0.5 },
  hint: { fontSize: 12, color: c.muted, lineHeight: 18 },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.muted },
  input: { height: 56, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 18, color: c.onSurface },
  pin: { letterSpacing: 8, fontSize: 24 },
  err: { backgroundColor: c.brandPrimary, padding: spacing.md },
  errText: { color: c.onBrandPrimary, fontSize: 12, fontWeight: "700" },
}));
