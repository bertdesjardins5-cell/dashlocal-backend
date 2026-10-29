import React, { useState } from "react";
import { ActivityIndicator, Text, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import BrandHeader, { Kicker } from "@/src/components/brand/BrandHeader";
import BrandButton from "@/src/components/brand/BrandButton";
import { useBrandForm } from "@/src/components/brand/formStyles";
import { makeStyles, useTheme } from "@/src/theme";

// Shop writes its own quote from the emailed link; it waits for admin approval before going on the landing page.
export default function WriteReview() {
  const { token } = useLocalSearchParams<{ token: string }>();
  const f = useBrandForm();
  const s = useStyles();
  const router = useRouter();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["review-request", token], queryFn: () => api<{ shop: string; status: string }>(`/review-requests/${token}`), retry: false });
  const [quote, setQuote] = useState("");
  const [person, setPerson] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api(`/review-requests/${token}`, { method: "POST", body: { quote, person: person || null } });
      setDone(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const used = done || q.data?.status === "submitted";
  return (
    <View style={s.root} testID="write-review-screen">
      <BrandHeader />
      <KeyboardAwareScrollView contentContainerStyle={f.wrap} keyboardShouldPersistTaps="handled" bottomOffset={24}>
        <View style={f.card}>
          {q.isLoading ? <ActivityIndicator color={colors.brandForest} /> : q.error ? (
            <>
              <Text style={f.title}>Link not valid</Text>
              <Text style={f.hint} testID="review-link-error">{(q.error as Error).message}. Ask ArriveWise to send a new one.</Text>
            </>
          ) : used ? (
            <View style={{ gap: 10 }} testID="review-thanks">
              <Ionicons name="heart" size={40} color={colors.brandRed} />
              <Text style={f.title}>Thank you!</Text>
              <Text style={f.hint}>Your review was sent to the ArriveWise team. We&apos;ll check it before it appears on our website.</Text>
              <BrandButton label="Visit ArriveWise" variant="outline" onPress={() => router.replace("/")} />
            </View>
          ) : (
            <>
              <Kicker>{q.data!.shop}</Kicker>
              <Text style={f.title}>How is ArriveWise working for you?</Text>
              <Text style={f.hint}>A sentence or two is perfect. With your OK, it may be shown on our website, with your shop name.</Text>
              {error ? <View style={f.err} testID="review-error"><Text style={f.errText}>{error}</Text></View> : null}
              <Text style={f.label}>Your review</Text>
              <TextInput testID="write-review-quote" value={quote} onChangeText={setQuote} multiline maxLength={400} placeholder="Same-day delivery changed how we…"
                placeholderTextColor={colors.brandMuted} style={[f.input, s.multi]} />
              <Text style={f.label}>Your name & role (optional)</Text>
              <TextInput testID="write-review-person" value={person} onChangeText={setPerson} maxLength={60} placeholder="Ana, owner" placeholderTextColor={colors.brandMuted} style={f.input} />
              <BrandButton testID="write-review-submit" label="Send review" icon="send" loading={busy} disabled={!quote.trim()} onPress={submit} style={{ marginTop: 8 }} />
            </>
          )}
        </View>
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.brandSoftGray },
  multi: { minHeight: 120, paddingTop: 12, textAlignVertical: "top" },
}));
