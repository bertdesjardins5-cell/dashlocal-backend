import React, { useState } from "react";
import { Pressable, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import BrandHeader, { Kicker } from "@/src/components/brand/BrandHeader";
import BrandButton from "@/src/components/brand/BrandButton";
import ConsentModal from "@/src/components/ConsentModal";
import TermsModal from "@/src/components/TermsModal";
import { useBrandForm } from "@/src/components/brand/formStyles";
import { fonts, makeStyles, useTheme } from "@/src/theme";

const FIELDS = [
  { k: "store_name", label: "Business name", ph: "e.g. Mission Bakery Co.", req: true },
  { k: "contact_name", label: "Your name", ph: "e.g. Ana Lopez", req: true },
  { k: "email", label: "Work email", ph: "you@business.com", req: true, kb: "email-address" as const },
  { k: "phone", label: "Phone (optional)", ph: "(519) 555-0123", kb: "phone-pad" as const },
  { k: "address", label: "Business address (optional)", ph: "e.g. 1 Market St" },
] as const;

// Pharmacies accept the PIPEDA/PHIPA privacy terms; every other business accepts the Terms of Service.
const TYPES = [
  { k: "store", label: "Store" },
  { k: "law_office", label: "Law office" },
  { k: "flower_shop", label: "Flower shop" },
  { k: "pharmacy", label: "Pharmacy" },
  { k: "other", label: "Other" },
] as const;

// Public: a shop requests a LocalDash account; the admin approves and the shop gets a set-password email.
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
  const pharmacy = form.business_type === "pharmacy";
  const ready = !!(form.business_type && form.store_name?.trim() && form.contact_name?.trim() && form.email?.trim());

  const submit = async (consent_timestamp: string) => {
    setBusy(true);
    setError(null);
    try {
      const consent = pharmacy ? { privacy_consent: true } : { terms_consent: true };
      await api("/signup-requests", { method: "POST", body: { ...form, ...consent, consent_timestamp } });
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
            <Kicker>Join LocalDash</Kicker>
            <Text style={f.title}>Request a merchant account</Text>
            <Text style={f.hint}>Tell us about your shop. We&apos;ll get back to you by email, usually within a business day.</Text>
            {error ? <View style={f.err} testID="signup-error"><Text style={f.errText}>{error}</Text></View> : null}
            <Text style={f.label}>Type of business</Text>
            <View style={s.chips}>
              {TYPES.map((t) => {
                const on = form.business_type === t.k;
                return (
                  <Pressable key={t.k} testID={`signup-type-${t.k}`} onPress={() => setForm({ ...form, business_type: t.k })}
                    accessibilityRole="radio" accessibilityState={{ selected: on }}
                    style={[s.chip, on && { backgroundColor: colors.brandForest, borderColor: colors.brandForest }]}>
                    <Text style={[s.chipText, on && { color: colors.onBrandPrimary }]}>{t.label}</Text>
                  </Pressable>
                );
              })}
            </View>
            {form.business_type ? (
              <Text style={f.hint} testID="signup-terms-hint">
                {pharmacy ? "Pharmacies accept our PIPEDA / PHIPA privacy terms before sending." : "You'll accept our Terms of Service before sending."}
              </Text>
            ) : null}
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
            <BrandButton testID="signup-submit" label={pharmacy ? "Review privacy terms & send" : "Review terms & send"} icon="send" loading={busy} disabled={!ready} onPress={() => setConsentOpen(true)} style={{ marginTop: 8 }} />
            {pharmacy || !form.business_type
              ? <Text style={s.small} onPress={() => router.push("/privacy")} testID="signup-privacy-link">How we protect your data — Privacy Policy</Text>
              : <Text style={s.small} onPress={() => router.push("/terms")} testID="signup-terms-link">Read our Terms of Service</Text>}
            <Text style={s.small} onPress={() => router.push("/sign-in/merchant")} testID="signup-have-account">Already have an account? Sign in</Text>
          </View>
        )}
      </KeyboardAwareScrollView>
      {pharmacy ? (
        <ConsentModal visible={consentOpen} role="merchant" busy={busy} onAgree={submit} onClose={() => setConsentOpen(false)}
          onOpenPolicy={() => { setConsentOpen(false); router.push("/privacy"); }} />
      ) : (
        <TermsModal visible={consentOpen} busy={busy} onAgree={submit} onClose={() => setConsentOpen(false)}
          onOpenTerms={() => { setConsentOpen(false); router.push("/terms"); }} />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.brandSoftGray },
  multi: { minHeight: 96, paddingTop: 12, textAlignVertical: "top" },
  small: { fontFamily: fonts.brandHeadingMedium, fontSize: 14, color: c.brandForest, textAlign: "center", paddingVertical: 12 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { minHeight: 44, paddingHorizontal: 16, justifyContent: "center", borderRadius: 22, borderWidth: 1.5, borderColor: c.brandLine, backgroundColor: c.surface },
  chipText: { fontFamily: fonts.brandHeadingMedium, fontSize: 14, color: c.brandCharcoal },
}));
