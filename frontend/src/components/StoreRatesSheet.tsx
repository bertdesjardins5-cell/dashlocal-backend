import React, { useEffect, useState } from "react";
import { Modal, Pressable, Switch, TextInput, View } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import PharmacyPartnerCard from "@/src/components/PharmacyPartnerCard";
import { Btn, Display, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type P = { base_fee: number; included_km: number; per_km: number; priority_fee: number; charge_failed: boolean; tax_pct: number };
type Rates = { name: string; shared: P; override: Partial<P>; effective: P; pharmacy_partner: boolean; pickup: { lat: number; lng: number; address: string } | null };

const FIELDS: { k: keyof Omit<P, "charge_failed">; label: string }[] = [
  { k: "base_fee", label: "PER-DELIVERY FEE (CAD)" },
  { k: "included_km", label: "KM INCLUDED" },
  { k: "per_km", label: "EXTRA PER KM (CAD)" },
  { k: "priority_fee", label: "PRIORITY SURCHARGE (CAD)" },
  { k: "tax_pct", label: "SALES TAX %" },
];

// Admin sheet: custom rates for one store. Empty field = follows the shared price list.
export default function StoreRatesSheet({ merchantId, onClose, onSaved, notify }: {
  merchantId: string; onClose: () => void; onSaved: () => void; notify: (m: string, tone?: "dark" | "red" | "green") => void;
}) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [r, setR] = useState<Rates | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [cf, setCf] = useState<"shared" | "yes" | "no">("shared");
  const [busy, setBusy] = useState(false);
  const [pharm, setPharm] = useState(false);

  useEffect(() => {
    api<Rates>(`/admin/merchants/${merchantId}/pricing`).then((x) => {
      setR(x);
      setPharm(x.pharmacy_partner);
      setForm(Object.fromEntries(FIELDS.map((f) => [f.k, x.override[f.k] != null ? String(x.override[f.k]) : ""])));
      setCf(x.override.charge_failed == null ? "shared" : x.override.charge_failed ? "yes" : "no");
    }).catch((e) => { notify(e.message, "red"); onClose(); });
    // load once per store; notify/onClose identities may change on every parent render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [merchantId]);

  const save = async (clear = false) => {
    const body: any = {};
    if (!clear) {
      for (const f of FIELDS) {
        const raw = (form[f.k] ?? "").trim().replace(",", ".");
        if (raw === "") continue;
        const v = Number(raw);
        if (!isFinite(v)) return notify(`ENTER A NUMBER FOR ${f.label}`, "red");
        body[f.k] = v;
      }
      if (cf !== "shared") body.charge_failed = cf === "yes";
    }
    setBusy(true);
    try {
      await api(`/admin/merchants/${merchantId}/pricing`, { method: "PUT", body });
      notify(clear || Object.keys(body).length === 0 ? `${r?.name.toUpperCase()} USES SHARED PRICES` : `CUSTOM RATES SAVED FOR ${r?.name.toUpperCase()}`, "green");
      onSaved();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal transparent animationType="slide" visible onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} testID="store-rates-backdrop" />
      <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="store-rates-sheet">
        <View style={{ flexDirection: "row", alignItems: "center" }}>
          <Display style={{ fontSize: 20, flex: 1 }} numberOfLines={2}>RATES · {r?.name ?? "…"}</Display>
          <Pressable testID="store-rates-close" onPress={onClose} style={s.close}><Ionicons name="close" size={24} color={colors.onSurface} /></Pressable>
        </View>
        <KeyboardAwareScrollView style={{ flexShrink: 1 }} contentContainerStyle={{ gap: spacing.md, paddingVertical: spacing.sm }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
          <Mono style={s.meta}>Leave a field empty to use the shared price. Custom rates apply to this store&apos;s new orders only.</Mono>
          {r ? <PharmacyPartnerCard merchantId={merchantId} enabled={r.pharmacy_partner} pickup={r.pickup} onChange={setPharm} /> : null}
          {FIELDS.map((f) => (
            <View key={f.k} style={[s.field, pharm && { opacity: 0.4 }]} pointerEvents={pharm ? "none" : "auto"}>
              <View style={{ flex: 1, gap: 2 }}>
                <Mono style={s.label}>{f.label}</Mono>
                <Mono style={s.meta}>SHARED: {r ? r.shared[f.k] : "…"}</Mono>
              </View>
              <TextInput testID={`store-rates-${f.k}`} value={form[f.k] ?? ""} onChangeText={(v) => setForm((x) => ({ ...x, [f.k]: v.replace(/[^0-9.,]/g, "") }))}
                placeholder={r ? String(r.shared[f.k]) : ""} placeholderTextColor={colors.muted} keyboardType="decimal-pad" style={s.input} />
            </View>
          ))}
          <View style={s.field}>
            <View style={{ flex: 1, gap: 2 }}>
              <Mono style={s.label}>CHARGE FAILED DROPS</Mono>
              <Mono style={s.meta}>{cf === "shared" ? `SHARED: ${r?.shared.charge_failed ? "YES" : "NO"}` : "CUSTOM"}</Mono>
            </View>
            <Pressable testID="store-rates-cf-shared" onPress={() => setCf("shared")} style={[s.chip, cf === "shared" && { backgroundColor: colors.surfaceInverse }]}>
              <Mono style={[s.chipText, cf === "shared" && { color: colors.onSurfaceInverse }]}>SHARED</Mono>
            </Pressable>
            <Switch testID="store-rates-cf-switch" value={cf === "shared" ? !!r?.shared.charge_failed : cf === "yes"} onValueChange={(v) => setCf(v ? "yes" : "no")}
              trackColor={{ true: colors.brandPrimary, false: colors.surfaceSecondary }} />
          </View>
        </KeyboardAwareScrollView>
        <View style={{ flexDirection: "row", gap: spacing.sm, paddingTop: spacing.md }}>
          <Btn testID="store-rates-clear" label="USE SHARED" variant="outline" loading={busy} onPress={() => save(true)} style={{ flex: 1 }} />
          <Btn testID="store-rates-save" label="SAVE RATES" icon="save" loading={busy} onPress={() => save()} style={{ flex: 1 }} />
        </View>
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  backdrop: { flex: 1, backgroundColor: c.surfaceInverse, opacity: 0.5 },
  sheet: { backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.lg, maxHeight: "90%" },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  field: { flexDirection: "row", alignItems: "center", gap: spacing.sm, minHeight: 56 },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1 },
  meta: { fontSize: 11, color: c.muted },
  input: { width: 96, height: 52, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 17, color: c.onSurface, textAlign: "right" },
  chip: { height: 44, paddingHorizontal: spacing.sm, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 11, fontWeight: "700" },
}));
