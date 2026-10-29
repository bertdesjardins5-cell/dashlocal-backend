import React, { useState } from "react";
import { Text, TextInput, View } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { api } from "@/src/api";
import { SessionUser, setSession } from "@/src/session";
import BrandButton from "@/src/components/brand/BrandButton";
import { useBrandForm } from "@/src/components/brand/formStyles";
import { useTheme } from "@/src/theme";

// Email + password sign-in for merchants and the dispatch admin (DashLocal brand styling).
export default function SignInForm({ title, hint, role, onSignedIn, onPhone, scroll = true }: { title: string; hint: string; role?: "admin" | "merchant"; onSignedIn?: (u: SessionUser) => void; onPhone?: (phone: string) => void; scroll?: boolean }) {
  const s = useBrandForm();
  const { colors } = useTheme();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    // A driver typed their phone number into the admin email form: send them to the driver sign-in instead.
    if (onPhone && !email.includes("@") && email.replace(/\D/g, "").length >= 10) return onPhone(email.trim());
    if (!email.trim() || !password) return setError("Enter your email and password");
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ token: string; user: SessionUser }>("/auth/login", { method: "POST", body: { email: email.trim(), password } });
      if (role && r.user.role !== role) {
        setError(role === "admin" ? "This page needs the dispatch admin account" : "This is the merchant sign-in — admins use Admin & driver sign-in");
      } else {
        await setSession(r.token, r.user);
        onSignedIn?.(r.user);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const body = (
    <View style={s.card}>
      <Text style={s.title}>{title}</Text>
      <Text style={s.hint}>{hint}</Text>
      {error ? <View style={s.err} testID="sign-in-error"><Text style={s.errText}>{error}</Text></View> : null}
      <Text style={s.label}>Email</Text>
      <TextInput testID="sign-in-email" value={email} onChangeText={setEmail} autoCapitalize="none" autoCorrect={false} keyboardType="email-address"
        textContentType="username" autoComplete="email" placeholder="you@store.com" placeholderTextColor={colors.brandMuted} style={s.input} />
      <Text style={s.label}>Password</Text>
      <TextInput testID="sign-in-password" value={password} onChangeText={setPassword} secureTextEntry textContentType="password" autoComplete="password"
        placeholder="••••••••" placeholderTextColor={colors.brandMuted} style={s.input} onSubmitEditing={submit} returnKeyType="go" />
      <BrandButton testID="sign-in-submit" label="Sign in" icon="arrow-forward" loading={busy} onPress={submit} style={{ marginTop: 8 }} />
    </View>
  );
  if (!scroll) return <View testID="sign-in-form">{body}</View>;
  return (
    <KeyboardAwareScrollView style={s.screen} contentContainerStyle={s.wrap} keyboardShouldPersistTaps="handled" bottomOffset={24} testID="sign-in-form">
      {body}
    </KeyboardAwareScrollView>
  );
}
