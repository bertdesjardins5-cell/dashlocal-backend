import React, { useState } from "react";
import { Pressable, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, Display, Header, Mono, Pill, statusTone } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Code = { tracking_code: string; customer_name: string; status: string; seq: number };

export default function CustomerEntry() {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const [code, setCode] = useState("");
  const q = useQuery({ queryKey: ["track-codes"], queryFn: () => api<Code[]>("/track-codes") });

  const go = (c: string) => c.trim() && router.push(`/customer/${c.trim().toUpperCase()}`);

  return (
    <View style={s.root}>
      <Header title="TRACK MY ORDER" subtitle="Enter your tracking code" />
      <KeyboardAwareScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
        <View style={s.form}>
          <Mono style={s.label}>TRACKING CODE</Mono>
          <TextInput
            testID="tracking-code-input"
            value={code}
            onChangeText={setCode}
            placeholder="MD1001"
            placeholderTextColor={colors.muted}
            autoCapitalize="characters"
            returnKeyType="go"
            onSubmitEditing={() => go(code)}
            style={s.input}
          />
          <Btn testID="track-order-button" label="TRACK" icon="locate" onPress={() => go(code)} disabled={!code.trim()} />
        </View>
        <Mono style={s.section}>DEMO ORDERS ON ROUTES</Mono>
        {q.data?.map((c) => (
          <Pressable key={c.tracking_code} testID={`demo-code-${c.tracking_code}`} onPress={() => go(c.tracking_code)} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }]}>
            <Display style={s.code}>{c.tracking_code}</Display>
            <Mono style={[s.meta, { flex: 1 }]} numberOfLines={1}>{c.customer_name}</Mono>
            <Pill label={c.status} tone={statusTone(c.status)} />
          </Pressable>
        ))}
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  form: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 2 },
  input: { height: 64, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.lg, fontFamily: fonts.mono, fontSize: 24, fontWeight: "700", color: c.onSurface, backgroundColor: c.surface },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.lg, borderBottomWidth: 2, borderColor: c.border, minHeight: 60 },
  code: { fontSize: 18, width: 90 },
  meta: { fontSize: 12, color: c.muted },
}));
