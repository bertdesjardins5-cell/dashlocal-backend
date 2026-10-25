import React, { useState } from "react";
import { TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Btn, Header, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

export default function CustomerEntry() {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const [code, setCode] = useState("");

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
        <Mono style={s.section}>Your tracking code is in the text or email from the store.</Mono>
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  form: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 2 },
  input: { height: 64, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.lg, fontFamily: fonts.mono, fontSize: 24, fontWeight: "700", color: c.onSurface, backgroundColor: c.surface },
  section: { padding: spacing.lg, fontSize: 12, color: c.muted },
}));
