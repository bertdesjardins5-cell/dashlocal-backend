import React, { useEffect, useState } from "react";
import { TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/src/api";
import { Btn, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

// Admin: the Privacy Officer named on the public Privacy Policy page.
export default function PrivacyOfficerForm({ notify }: { notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["privacy-officer"], queryFn: () => api<{ officer_name: string; officer_email: string }>("/privacy/officer") });
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (q.data) {
      setName(q.data.officer_name);
      setEmail(q.data.officer_email);
    }
  }, [q.data]);
  const changed = !!q.data && (name.trim() !== q.data.officer_name || email.trim().toLowerCase() !== q.data.officer_email);
  const save = async () => {
    setBusy(true);
    try {
      await api("/admin/privacy/officer", { method: "PUT", body: { officer_name: name.trim(), officer_email: email.trim() } });
      await q.refetch();
      notify("PRIVACY OFFICER SAVED · POLICY PAGE UPDATED", "green");
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };
  return (
    <View style={s.block} testID="privacy-officer-form">
      <Mono style={s.meta}>Shown as the contact on the public Privacy Policy page. Use the person accountable for privacy at DashLocal.</Mono>
      <TextInput testID="privacy-officer-name" value={name} onChangeText={setName} placeholder="Full name" placeholderTextColor={colors.muted} style={s.input} />
      <TextInput testID="privacy-officer-email" value={email} onChangeText={setEmail} placeholder="privacy@dashlocal.ca" autoCapitalize="none" keyboardType="email-address" placeholderTextColor={colors.muted} style={s.input} />
      <Btn testID="privacy-officer-save" label="SAVE" loading={busy} disabled={!changed || name.trim().length < 2} onPress={save} />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  meta: { fontSize: 12, color: c.muted },
  input: { height: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
}));
