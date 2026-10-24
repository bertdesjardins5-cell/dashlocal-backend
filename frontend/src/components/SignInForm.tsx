import React, { useState } from "react";
import { TextInput, View } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { SessionUser, setSession } from "@/src/session";
import { Btn, Display, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

// Email + password sign-in for merchants and the dispatch admin.
export default function SignInForm({ title, hint, role }: { title: string; hint: string; role?: "admin" | "merchant" }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!email.trim() || !password) return setError("Enter your email and password");
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ token: string; user: SessionUser }>("/auth/login", { method: "POST", body: { email: email.trim(), password } });
      if (role && r.user.role !== role) {
        setError(role === "admin" ? "This page needs the dispatch admin account" : "This is a merchant sign-in — use the Admin console for dispatch");
      } else {
        await setSession(r.token, r.user);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAwareScrollView contentContainerStyle={s.wrap} keyboardShouldPersistTaps="handled" bottomOffset={24} testID="sign-in-form">
      <View style={s.icon}><Ionicons name="lock-closed" size={28} color={colors.onSurfaceInverse} /></View>
      <Display style={s.title}>{title}</Display>
      <Mono style={s.hint}>{hint}</Mono>
      {error ? <View style={s.err} testID="sign-in-error"><Mono style={s.errText}>{error}</Mono></View> : null}
      <Mono style={s.label}>EMAIL</Mono>
      <TextInput testID="sign-in-email" value={email} onChangeText={setEmail} autoCapitalize="none" autoCorrect={false} keyboardType="email-address"
        textContentType="username" autoComplete="email" placeholder="you@store.com" placeholderTextColor={colors.muted} style={s.input} />
      <Mono style={s.label}>PASSWORD</Mono>
      <TextInput testID="sign-in-password" value={password} onChangeText={setPassword} secureTextEntry textContentType="password" autoComplete="password"
        placeholder="••••••••" placeholderTextColor={colors.muted} style={s.input} onSubmitEditing={submit} returnKeyType="go" />
      <Btn testID="sign-in-submit" label="SIGN IN" icon="log-in" loading={busy} onPress={submit} />
    </KeyboardAwareScrollView>
  );
}

const useStyles = makeStyles((c) => ({
  wrap: { padding: spacing.xl, gap: spacing.md },
  icon: { width: 56, height: 56, backgroundColor: c.surfaceInverse, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 28, letterSpacing: -0.5 },
  hint: { fontSize: 12, color: c.muted, lineHeight: 18 },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.muted },
  input: { height: 52, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 16, color: c.onSurface },
  err: { backgroundColor: c.brandPrimary, padding: spacing.md },
  errText: { color: c.onBrandPrimary, fontSize: 12, fontWeight: "700" },
}));
