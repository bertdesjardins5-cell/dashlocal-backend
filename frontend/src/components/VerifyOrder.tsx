import React, { useState } from "react";
import { TextInput, View } from "react-native";
import { api, OrderPrivate } from "@/src/api";
import { Btn, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

// Customer proves the order is theirs (last 4 digits of the phone on it) before seeing address/proof or making changes.
export default function VerifyOrder({ code, onVerified }: { code: string; onVerified: (o: OrderPrivate & { token: string }) => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [digits, setDigits] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      onVerified(await api<OrderPrivate & { token: string }>(`/track/${code}/verify`, { method: "POST", body: { last4: digits } }));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <View style={s.box} testID="verify-order">
      <Mono style={s.label}>MANAGE THIS ORDER</Mono>
      <Mono style={s.meta}>Enter the last 4 digits of your phone to see the address, book a window, view proof or rate.</Mono>
      <View style={s.row}>
        <TextInput
          testID="verify-last4-input"
          value={digits}
          onChangeText={(v) => setDigits(v.replace(/\D/g, "").slice(0, 4))}
          placeholder="0000"
          placeholderTextColor={colors.muted}
          keyboardType="number-pad"
          maxLength={4}
          returnKeyType="done"
          onSubmitEditing={() => digits.length === 4 && submit()}
          style={s.input}
        />
        <View style={{ flex: 1 }}>
          <Btn testID="verify-order-button" label="UNLOCK" icon="lock-open" loading={busy} disabled={digits.length !== 4} onPress={submit} />
        </View>
      </View>
      {err ? <Mono style={[s.meta, { color: colors.brandPrimary }]} testID="verify-error">{err.toUpperCase()}</Mono> : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { gap: spacing.xs, borderWidth: 2, borderColor: c.border, padding: spacing.md },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  meta: { fontSize: 12, color: c.muted },
  row: { flexDirection: "row", gap: spacing.sm, alignItems: "center" },
  input: { width: 110, height: 52, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 22, fontWeight: "700", letterSpacing: 4, color: c.onSurface, backgroundColor: c.surface },
}));
