import React, { useState } from "react";
import { Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import BrandHeader, { Kicker } from "@/src/components/brand/BrandHeader";
import BrandButton from "@/src/components/brand/BrandButton";
import ConsentModal from "@/src/components/ConsentModal";
import { useBrandForm } from "@/src/components/brand/formStyles";
import { fonts, makeStyles, useTheme } from "@/src/theme";

const FIELDS = [
  { k: "store_name", label: "Store name", ph: "e.g. Mission Bakery Co.", req: true },
  { k: "contact_name", label: "Your name", ph: "e.g. Ana Lopez", req: true },
  { k: "email", label: "Work email", ph: "you@store.com", req: true, kb: "email-address" as const },
  { k: "phone", label: "Phone (optional)", ph: "(519) 555-0123", kb: "phone-pad" as const },
  { k: "address", label: "Store address (optional)", ph: "e.g. 1 Market St" },
] as const;

// Public: a shop requests a DashLocal account; the admin approves and the shop gets a set-password email.
export default function SignUp() {
  const f = useBrandForm();
  const s = useStyles();
  const router = useRouter();
  const { colors } = useTheme();
  const [form, setForm] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [consentOpen, setConsentOpen] = useState(false);
  const ready = !!(form.store_name?.trim() && form.contact_name?.trim() && form.email?.trim());

  const submit = async (consent_timestamp: string) => {
    setBusy(true);
    setError(null);
    try {
      await api("/signup-requests", { method: "POST", body: { ...form, privacy_consent: true, consent_timestamp } });
      setConsentOpen(false);
      setDone(true);
    } catch (e: any) {
      setConsentOpen(false);
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.root} testID="signup-screen">
      <BrandHeader />
      <KeyboardAwareScrollView contentContainerStyle={f.wrap} keyboardShouldPersistTaps="handled" bottomOffset={24}>
        {done ? (
          <View style={f.card} testID="signup-done">
            <Ionicons name="checkmark-circle" size={44} color={colors.brandForest} />
            <Text style={f.title}>Thanks — request received</Text>
            <Text style={f.hint}>We review every shop personally. Once approved, you&apos;ll get an email with a link to choose your password and start delivering.</Text>
            <BrandButton testID="signup-home" label="Back to home" variant="outline" onPress={() => router.replace("/")} />
          </View>
        ) : (
          <View style={f.card}>
            <Kicker>Join DashLocal</Kicker>
            <Text style={f.title}>Request a merchant account</Text>
            <Text style={f.hint}>Tell us about your shop. We&apos;ll get back to you by email, usually within a business day.</Text>
            {error ? <View style={f.err} testID="signup-error"><Text style={f.errText}>{error}</Text></View> : null}
            {FIELDS.map((x) => (
              <View key={x.k} style={{ gap: 6 }}>
                <Text style={f.label}>{x.label}</Text>
                <TextInput testID={`signup-${x.k}`} value={form[x.k] ?? ""} onChangeText={(v) => setForm({ ...form, [x.k]: v })} placeholder={x.ph}
                  placeholderTextColor={colors.brandMuted} keyboardType={"kb" in x ? x.kb : "default"} autoCapitalize={x.k === "email" ? "none" : "words"} style={f.input} />
              </View>
            ))}
            <Text style={f.label}>Anything else? (optional)</Text>
            <TextInput testID="signup-message" value={form.message ?? ""} onChangeText={(v) => setForm({ ...form, message: v })} placeholder="How many deliveries a week, what you sell…"
              placeholderTextColor={colors.brandMuted} multiline maxLength={500} style={[f.input, s.multi]} />
            <BrandButton testID="signup-submit" label="Review privacy terms & send" icon="send" loading={busy} disabled={!ready} onPress={() => setConsentOpen(true)} style={{ marginTop: 8 }} />
            <Text style={s.small} onPress={() => router.push("/privacy")} testID="signup-privacy-link">How we protect your data — Privacy Policy</Text>
            <Text style={s.small} onPress={() => router.push("/sign-in/merchant")} testID="signup-have-account">Already have an account? Sign in</Text>
          </View>
        )}
      </KeyboardAwareScrollView>
      <ConsentModal visible={consentOpen} role="merchant" busy={busy} onAgree={submit} onClose={() => setConsentOpen(false)}
        onOpenPolicy={() => { setConsentOpen(false); router.push("/privacy"); }} />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.brandSoftGray },
  multi: { minHeight: 96, paddingTop: 12, textAlignVertical: "top" },
  small: { fontFamily: fonts.brandHeadingMedium, fontSize: 14, color: c.brandForest, textAlign: "center", paddingVertical: 12 },
}));
