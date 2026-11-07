import React, { useState } from "react";
import { Switch, TextInput, View } from "react-native";
import { api } from "@/src/api";
import { Btn, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Pickup = { lat: number; lng: number; address: string } | null;

// Admin: put a store on the Pharmacy Batch contract (Addendum A) and set where pickups happen.
export default function PharmacyPartnerCard({ merchantId, enabled, pickup, onChange }: {
  merchantId: string; enabled: boolean; pickup: Pickup; onChange: (enabled: boolean) => void;
}) {
  const s = useStyles();
  const { colors } = useTheme();
  const [on, setOn] = useState(enabled);
  const [addr, setAddr] = useState(pickup?.address ?? "");
  const [saved, setSaved] = useState(pickup?.address ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  const save = async (next: boolean) => {
    setBusy(true);
    try {
      const r = await api<{ pharmacy_partner: boolean; pickup: Pickup }>(`/admin/merchants/${merchantId}/pharmacy`, {
        method: "PUT", body: { enabled: next, pickup_address: addr.trim() || null },
      });
      setOn(r.pharmacy_partner);
      onChange(r.pharmacy_partner);
      setSaved(r.pickup?.address ?? "");
      setMsg({ text: r.pharmacy_partner ? "PHARMACY CONTRACT ON · $25/MONTH FEE BILLED" : "PHARMACY CONTRACT OFF", ok: true });
    } catch (e: any) {
      setMsg({ text: e.message, ok: false });
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.box} testID="pharmacy-card">
      <View style={s.row}>
        <View style={{ flex: 1, gap: 2 }}>
          <Mono style={s.label}>PHARMACY BATCH CONTRACT</Mono>
          <Mono style={s.meta}>$6.25/drop with 10+ drops per pickup, else $8.99 · 3 km included from the pharmacy, then $1.25/km · $25/month platform fee. Replaces the rates below.</Mono>
        </View>
        <Switch testID="pharmacy-switch" value={on} disabled={busy} onValueChange={save}
          trackColor={{ true: colors.brandPrimary, false: colors.surfaceSecondary }} />
      </View>
      <Mono style={s.label}>PICKUP ADDRESS</Mono>
      <View style={s.row}>
        <TextInput testID="pharmacy-pickup-input" value={addr} onChangeText={setAddr} placeholder="e.g. 385 Fairway Rd S, Kitchener"
          placeholderTextColor={colors.muted} style={s.input} />
        <Btn testID="pharmacy-pickup-save" label="SAVE" loading={busy} disabled={!addr.trim() || addr.trim() === saved} onPress={() => save(on)} />
      </View>
      {msg ? <Mono testID="pharmacy-msg" style={[s.label, { color: msg.ok ? colors.brandForest : colors.brandPrimary }]}>{msg.text}</Mono> : null}
      <Mono style={s.meta}>{saved ? `Distance is measured from ${saved}` : "No pickup address yet — distance is measured from the depot. The store can also set it in its portal."}</Mono>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { borderWidth: 2, borderColor: c.border, padding: spacing.md, gap: spacing.sm },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1 },
  meta: { fontSize: 11, color: c.muted },
  input: { flex: 1, height: 52, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
}));
