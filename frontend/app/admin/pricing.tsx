import React, { useCallback, useEffect, useState } from "react";
import { Pressable, RefreshControl, ScrollView, Switch, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api, dayLabel } from "@/src/api";
import { Btn, Display, Header, Loading, Mono, Pill, useToast } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";
import { money } from "@/src/components/BillCard";
import StoreRatesSheet from "@/src/components/StoreRatesSheet";

type Pricing = { currency: string; base_fee: number; included_km: number; per_km: number; priority_fee: number; charge_failed: boolean; tax_pct: number };
type Billing = {
  week_start: string; week_end: string; currency: string; total: number; drops: number; weeks: string[]; outstanding: number;
  stores: {
    id: string; name: string; drops: number; subtotal: number; tax: number; total: number; open_orders: { count: number; estimate: number };
    payment: { paid_at: string; note: string | null; amount: number } | null; can_mark_paid: boolean; outstanding: number; unpaid_weeks: number; custom_rates: boolean;
  }[];
};

const FIELDS: { k: keyof Omit<Pricing, "currency" | "charge_failed">; label: string; unit: string }[] = [
  { k: "base_fee", label: "PER-DELIVERY FEE", unit: "CAD per drop" },
  { k: "included_km", label: "KM INCLUDED IN FEE", unit: "km from depot" },
  { k: "per_km", label: "EXTRA PER KM", unit: "CAD per km beyond included" },
  { k: "priority_fee", label: "PRIORITY SURCHARGE", unit: "CAD per priority drop" },
  { k: "tax_pct", label: "SALES TAX (GST/HST)", unit: "% added to bills" },
];

// Admin: edit pricing and see what each store owes this week.
export default function PricingScreen() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const pq = useQuery({ queryKey: ["pricing"], queryFn: () => api<Pricing>("/pricing") });
  const [week, setWeek] = useState<string | null>(null);
  const bq = useQuery({ queryKey: ["admin-billing", week], queryFn: () => api<Billing>(`/admin/billing${week ? `?week=${week}` : ""}`) });
  const [form, setForm] = useState<Record<string, string>>({});
  const [chargeFailed, setChargeFailed] = useState(true);
  const [busy, setBusy] = useState(false);
  const [paying, setPaying] = useState<string | null>(null);
  const [ratesFor, setRatesFor] = useState<string | null>(null);
  const closeRates = useCallback(() => setRatesFor(null), []);

  const markPaid = async (id: string, paid: boolean) => {
    setPaying(id);
    try {
      await api(`/admin/merchants/${id}/bills/${bq.data?.week_start}/paid`, { method: "PUT", body: { paid } });
      toast.show(paid ? "BILL MARKED PAID" : "BILL MARKED UNPAID", "green");
      bq.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setPaying(null);
    }
  };

  useEffect(() => {
    if (pq.data) {
      setForm(Object.fromEntries(FIELDS.map((f) => [f.k, String(pq.data[f.k])])));
      setChargeFailed(pq.data.charge_failed);
    }
  }, [pq.data]);

  const save = async () => {
    const body: any = { charge_failed: chargeFailed };
    for (const f of FIELDS) {
      const v = Number((form[f.k] ?? "").replace(",", "."));
      if (!isFinite(v) || (form[f.k] ?? "").trim() === "") return toast.show(`ENTER A NUMBER FOR ${f.label}`, "red");
      body[f.k] = v;
    }
    setBusy(true);
    try {
      await api("/admin/pricing", { method: "PUT", body });
      toast.show("PRICES SAVED · APPLY TO NEW ORDERS", "green");
      pq.refetch();
      bq.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  const ex = pq.data ? (() => {
    const p = { ...pq.data, ...Object.fromEntries(FIELDS.map((f) => [f.k, Number((form[f.k] ?? "").replace(",", ".")) || 0])) };
    return p.base_fee + Math.max(0, 8 - p.included_km) * p.per_km;
  })() : 0;

  return (
    <View style={s.root} testID="pricing-screen">
      <Header title="PRICING & BILLING" subtitle="What merchants pay · CAD" />
      {pq.isLoading ? <Loading /> : (
        <KeyboardAwareScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} keyboardShouldPersistTaps="handled" bottomOffset={24}
          refreshControl={<RefreshControl refreshing={false} onRefresh={() => { pq.refetch(); bq.refetch(); }} />}>
          <Mono style={s.section}>PRICES</Mono>
          <View style={s.block}>
            {FIELDS.map((f) => (
              <View key={f.k} style={s.field}>
                <View style={{ flex: 1, gap: 2 }}>
                  <Mono style={s.label}>{f.label}</Mono>
                  <Mono style={s.meta}>{f.unit}</Mono>
                </View>
                <TextInput testID={`pricing-${f.k}`} value={form[f.k] ?? ""} onChangeText={(v) => setForm((x) => ({ ...x, [f.k]: v.replace(/[^0-9.,]/g, "") }))}
                  keyboardType="decimal-pad" style={s.input} selectTextOnFocus />
              </View>
            ))}
            <View style={s.field}>
              <View style={{ flex: 1, gap: 2 }}>
                <Mono style={s.label}>CHARGE FAILED DROPS</Mono>
                <Mono style={s.meta}>Failed attempts are billed at the normal fee</Mono>
              </View>
              <Switch testID="pricing-charge-failed" value={chargeFailed} onValueChange={setChargeFailed} trackColor={{ true: colors.brandPrimary, false: colors.surfaceSecondary }} />
            </View>
            <Mono style={s.meta} testID="pricing-example">EXAMPLE · 8 KM DROP = {money(ex)} · PRIORITY +{money(Number(form.priority_fee) || 0)}</Mono>
            <Btn testID="pricing-save" label="SAVE PRICES" icon="save" loading={busy} onPress={save} />
            <Mono style={s.meta}>Distance is the estimated road distance from the depot to the drop. New prices apply to orders created from now on; existing orders keep the price they were booked at. Priority drops are delivered first on their route.</Mono>
          </View>

          <Mono style={s.section}>WEEKLY BILLS</Mono>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={s.weeks} contentContainerStyle={s.weeksInner}>
            {(bq.data?.weeks ?? []).map((w, i) => (
              <Pressable key={w} testID={`billing-week-${i}`} onPress={() => setWeek(i === 0 ? null : w)}
                style={[s.chip, (bq.data?.week_start === w) && { backgroundColor: colors.surfaceInverse }]}>
                <Mono style={[s.chipText, (bq.data?.week_start === w) && { color: colors.onSurfaceInverse }]}>{i === 0 ? "THIS WEEK" : `WK ${dayLabel(w)}`}</Mono>
              </Pressable>
            ))}
          </ScrollView>
          {bq.data ? (
            <>
              <View style={s.totalRow} testID="billing-total">
                <Mono style={s.meta}>{dayLabel(bq.data.week_start)} – {dayLabel(bq.data.week_end)} · {bq.data.drops} BILLED DROPS</Mono>
                <Display style={{ fontSize: 22 }}>{money(bq.data.total)}</Display>
                <Mono style={[s.meta, bq.data.outstanding > 0 && { color: colors.brandPrimary, fontWeight: "700" }]} testID="billing-outstanding">
                  STILL OWED (ALL STORES, UNPAID PAST WEEKS): {money(bq.data.outstanding)}
                </Mono>
              </View>
              {bq.data.stores.map((m) => (
                <View key={m.id} style={s.storeBlock} testID={`billing-store-${m.id}`}>
                  <View style={s.storeTop}>
                    <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                      <Display style={{ fontSize: 16 }} numberOfLines={1}>{m.name}</Display>
                      <Mono style={s.meta}>{m.drops} DROPS · {money(m.subtotal)} + TAX {money(m.tax)}{m.open_orders.count ? ` · ${m.open_orders.count} OPEN ≈ ${money(m.open_orders.estimate)}` : ""}</Mono>
                      {m.outstanding > 0 ? <Mono style={s.owed} testID={`billing-owed-${m.id}`}>OWES {money(m.outstanding)} · {m.unpaid_weeks} UNPAID WEEK{m.unpaid_weeks === 1 ? "" : "S"}</Mono> : null}
                    </View>
                    <Display style={{ fontSize: 16 }}>{money(m.total)}</Display>
                  </View>
                  <View style={s.actions}>
                    {m.payment ? <Pill label={`PAID ${dayLabel(m.payment.paid_at.slice(0, 10))}${m.payment.note ? ` · ${m.payment.note}` : ""}`} tone="green" />
                      : m.can_mark_paid && m.total > 0 ? <Pill label="UNPAID" tone="red" /> : m.can_mark_paid ? null : <Pill label="WEEK IN PROGRESS" tone="light" />}
                    <View style={{ flex: 1 }} />
                    {m.can_mark_paid && (m.total > 0 || m.payment) ? (
                      <Pressable testID={`billing-paid-${m.id}`} onPress={() => markPaid(m.id, !m.payment)} disabled={paying === m.id} style={s.smallBtn}>
                        <Mono style={s.chipText}>{paying === m.id ? "…" : m.payment ? "MARK UNPAID" : "MARK PAID"}</Mono>
                      </Pressable>
                    ) : null}
                    <Pressable testID={`billing-rates-${m.id}`} onPress={() => setRatesFor(m.id)} style={[s.smallBtn, m.custom_rates && { backgroundColor: colors.surfaceInverse }]}>
                      <Mono style={[s.chipText, m.custom_rates && { color: colors.onSurfaceInverse }]}>{m.custom_rates ? "CUSTOM RATES" : "RATES"}</Mono>
                    </Pressable>
                  </View>
                </View>
              ))}
            </>
          ) : <Loading />}
        </KeyboardAwareScrollView>
      )}
      {ratesFor ? <StoreRatesSheet merchantId={ratesFor} onClose={closeRates} onSaved={() => { setRatesFor(null); bq.refetch(); }} notify={toast.show} /> : null}
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  field: { flexDirection: "row", alignItems: "center", gap: spacing.md, minHeight: 56 },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1 },
  meta: { fontSize: 11, color: c.muted },
  input: { width: 104, height: 52, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 18, color: c.onSurface, textAlign: "right" },
  weeks: { height: 60, flexGrow: 0, borderBottomWidth: 2, borderColor: c.border },
  weeksInner: { gap: spacing.sm, alignItems: "center", paddingHorizontal: spacing.lg },
  chip: { height: 44, flexShrink: 0, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 12, fontWeight: "700" },
  totalRow: { padding: spacing.lg, gap: 4, borderBottomWidth: 2, borderColor: c.border },
  store: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.lg, borderBottomWidth: 2, borderColor: c.border },
  storeBlock: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  storeTop: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  actions: { flexDirection: "row", alignItems: "center", gap: spacing.sm, flexWrap: "wrap" },
  owed: { fontSize: 11, fontWeight: "700", color: c.brandPrimary },
  smallBtn: { minHeight: 44, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
}));
