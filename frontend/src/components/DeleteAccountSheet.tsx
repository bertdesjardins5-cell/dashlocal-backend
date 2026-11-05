import React, { useEffect, useState } from "react";
import { Modal, Pressable, TextInput, View } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { Btn, Display, Loading, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Check = { can_delete: boolean; blockers: string[]; role: string };

// Stores and drivers can delete their own account (App Store rule). Asks for the password / PIN, then signs out.
export default function DeleteAccountSheet({ role, onClose, onDeleted }: { role: "merchant" | "driver"; onClose: () => void; onDeleted: () => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [check, setCheck] = useState<Check | null>(null);
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const store = role === "merchant";

  useEffect(() => {
    api<Check>("/me/delete-account").then(setCheck).catch((e) => setError(e.message));
  }, []);

  const go = async () => {
    if (!secret.trim()) return setError(store ? "Enter your password" : "Enter your PIN");
    setBusy(true);
    setError(null);
    try {
      await api("/me/delete-account", { method: "POST", body: { secret } });
      onDeleted();
    } catch (e: any) {
      setError(e.message);
      setSecret("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal transparent animationType="slide" visible onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} testID="delete-account-backdrop" />
      <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="delete-account-sheet">
        <View style={{ flexDirection: "row", alignItems: "center" }}>
          <Display style={{ fontSize: 22, flex: 1 }}>DELETE MY ACCOUNT</Display>
          <Pressable testID="delete-account-close" onPress={onClose} style={s.close}><Ionicons name="close" size={24} color={colors.onSurface} /></Pressable>
        </View>
        <KeyboardAwareScrollView style={{ flexShrink: 1 }} contentContainerStyle={{ gap: spacing.sm, paddingTop: spacing.sm }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
          {!check && !error ? <Loading /> : null}
          {check && !check.can_delete ? (
            <View style={{ gap: spacing.sm }} testID="delete-account-blocked">
              <Mono style={s.body}>YOU CAN&apos;T DELETE YOUR ACCOUNT YET:</Mono>
              {check.blockers.map((b) => <Mono key={b} style={s.body}>• {b}</Mono>)}
              <Btn testID="delete-account-ok" label="OK" variant="outline" onPress={onClose} />
            </View>
          ) : null}
          {check?.can_delete ? (
            <>
              <Mono style={s.body}>
                {store
                  ? "This closes your store on LocalDash right away. Every login for the store stops working and your contact details and saved card are removed."
                  : "This deletes your driver account right away. Your PIN stops working and your name and phone number are removed."}
              </Mono>
              <Mono style={s.meta}>Past delivery and billing records are kept without your personal details, as Canadian tax law requires. This can&apos;t be undone.</Mono>
              <Mono style={s.label}>{store ? "YOUR PASSWORD" : "YOUR 6-DIGIT PIN"}</Mono>
              <TextInput testID="delete-account-secret" value={secret} onChangeText={setSecret} secureTextEntry autoCapitalize="none"
                keyboardType={store ? "default" : "number-pad"} maxLength={store ? 200 : 6} style={s.input} placeholderTextColor={colors.muted}
                placeholder={store ? "Password" : "PIN"} />
              <Btn testID="delete-account-confirm" label="DELETE MY ACCOUNT" icon="trash" variant="dark" loading={busy} onPress={go} />
            </>
          ) : null}
        </KeyboardAwareScrollView>
        {error ? <View style={s.err} testID="delete-account-error"><Mono style={s.errText}>{error}</Mono></View> : null}
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  backdrop: { flex: 1, backgroundColor: c.surfaceInverse, opacity: 0.5 },
  sheet: { backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.lg, maxHeight: "90%" },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  body: { fontSize: 13, color: c.onSurface },
  meta: { fontSize: 12, color: c.muted },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.muted, marginTop: spacing.xs },
  input: { height: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 15, color: c.onSurface },
  err: { backgroundColor: c.brandPrimary, padding: spacing.md, marginTop: spacing.sm },
  errText: { color: c.onBrandPrimary, fontSize: 12, fontWeight: "700" },
}));
