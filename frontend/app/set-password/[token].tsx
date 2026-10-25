import React, { useState } from "react";
import { ActivityIndicator, Text, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { api } from "@/src/api";
import { SessionUser, setSession } from "@/src/session";
import BrandHeader, { Kicker } from "@/src/components/brand/BrandHeader";
import BrandButton from "@/src/components/brand/BrandButton";
import { useBrandForm } from "@/src/components/brand/formStyles";
import { makeStyles, useTheme } from "@/src/theme";

// Approved shop chooses its password from the emailed one-time link, then lands in its portal.
export default function SetPassword() {
  const { token } = useLocalSearchParams<{ token: string }>();
  const f = useBrandForm();
  const s = useStyles();
  const router = useRouter();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["setpw", token], queryFn: () => api<{ email: string; store_name: string | null }>(`/auth/set-password/${token}`), retry: false });
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (pw.length < 8) return setError("Use at least 8 characters");
    if (pw !== pw2) return setError("The two passwords don't match");
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ token: string; user: SessionUser }>("/auth/set-password", { method: "POST", body: { token, password: pw } });
      await setSession(r.token, r.user);
      router.replace(`/merchant/${r.user.merchant_id}`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.root} testID="set-password-screen">
      <BrandHeader />
      <KeyboardAwareScrollView contentContainerStyle={f.wrap} keyboardShouldPersistTaps="handled" bottomOffset={24}>
        <View style={f.card}>
          {q.isLoading ? <ActivityIndicator color={colors.brandForest} /> : q.error ? (
            <>
              <Text style={f.title}>Link not valid</Text>
              <Text style={f.hint} testID="set-password-link-error">{(q.error as Error).message}</Text>
              <BrandButton label="Go to merchant sign-in" variant="outline" onPress={() => router.replace("/sign-in/merchant")} />
            </>
          ) : (
            <>
              <Kicker>{q.data!.store_name ?? "Your store"}</Kicker>
              <Text style={f.title}>Choose your password</Text>
              <Text style={f.hint}>You&apos;ll sign in with {q.data!.email}.</Text>
              {error ? <View style={f.err} testID="set-password-error"><Text style={f.errText}>{error}</Text></View> : null}
              <Text style={f.label}>New password</Text>
              <TextInput testID="set-password-1" value={pw} onChangeText={setPw} secureTextEntry textContentType="newPassword" placeholder="At least 8 characters" placeholderTextColor={colors.brandMuted} style={f.input} />
              <Text style={f.label}>Repeat password</Text>
              <TextInput testID="set-password-2" value={pw2} onChangeText={setPw2} secureTextEntry textContentType="newPassword" onSubmitEditing={submit} returnKeyType="go" style={f.input} />
              <BrandButton testID="set-password-submit" label="Save and open my portal" icon="arrow-forward" loading={busy} disabled={!pw || !pw2} onPress={submit} style={{ marginTop: 8 }} />
            </>
          )}
        </View>
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({ root: { flex: 1, backgroundColor: c.brandSoftGray } }));
