import React, { useState } from "react";
import { Pressable, RefreshControl, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, dayLabel } from "@/src/api";
import { money } from "@/src/components/BillCard";
import type { Invoice } from "@/src/components/InvoicesCard";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, useToast } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Resp = { outstanding: number; paid: number; unpaid_count: number; reported_count: number; invoices: Invoice[] };
type Biz = { name: string; address: string; hst_number: string; hst_pct: number };

// Admin: mark per-delivery invoices paid when money arrives, and edit the business details printed on invoices.
export default function AdminInvoices() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const [status, setStatus] = useState<"reported" | "unpaid" | "paid">("reported");
  const q = useQuery({ queryKey: ["admin-invoices", status], queryFn: () => api<Resp>(`/admin/invoices?status=${status}`) });
  const biz = useQuery({ queryKey: ["business"], queryFn: () => api<Biz>("/admin/business") });
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [edit, setForm] = useState<Record<string, string> | null>(null);
  const form = edit ?? (biz.data ? { name: biz.data.name, address: biz.data.address, hst_number: biz.data.hst_number, hst_pct: String(biz.data.hst_pct) } : null);

  const toggle = (id: string) => setSel((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const mark = async (paid: boolean, ids = Array.from(sel)) => {
    setBusy(paid ? "paid" : "unpaid");
    try {
      const r = await api<{ updated: number }>("/admin/invoices/mark-paid", { method: "POST", body: { ids, paid, note } });
      toast.show(paid ? `${r.updated} MARKED PAID` : `${r.updated} BACK TO UNPAID`, "green");
      setSel(new Set());
      setNote("");
      q.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const saveBiz = async () => {
    setBusy("biz");
    try {
      await api("/admin/business", { method: "PUT", body: { ...form, hst_pct: Number(form!.hst_pct) } });
      biz.refetch();
      toast.show("INVOICE DETAILS SAVED · APPLY TO NEW INVOICES", "green");
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const selTotal = (q.data?.invoices ?? []).filter((i) => sel.has(i.id)).reduce((a, i) => a + i.total, 0);

  return (
    <View style={s.root} testID="admin-invoices-screen">
      <Header title="INVOICES" subtitle={`Per delivery · ${biz.data?.hst_pct ?? 13}% HST`} />
      <KeyboardAwareScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} keyboardShouldPersistTaps="handled" bottomOffset={24}
        refreshControl={<RefreshControl refreshing={false} onRefresh={() => q.refetch()} />}>
        <View style={s.totals}>
          <View style={s.cell}><Display style={[s.val, { color: colors.brandPrimary }]} testID="admin-invoices-outstanding">{money(q.data?.outstanding ?? 0)}</Display><Mono style={s.label}>OUTSTANDING · {q.data?.unpaid_count ?? 0}</Mono></View>
          <View style={[s.cell, { borderLeftWidth: 2, borderColor: colors.border }]}><Display style={s.val}>{money(q.data?.paid ?? 0)}</Display><Mono style={s.label}>PAID</Mono></View>
        </View>
        <View style={s.block}>
          <View style={s.row}>
            {(["reported", "unpaid", "paid"] as const).map((f) => (
              <Pressable key={f} testID={`admin-invoices-filter-${f}`} onPress={() => { setStatus(f); setSel(new Set()); }} style={[s.chip, status === f && { backgroundColor: colors.surfaceInverse }]}>
                <Mono style={[s.chipText, status === f && { color: colors.onSurfaceInverse }]}>{f === "reported" ? `TO CONFIRM · ${q.data?.reported_count ?? 0}` : f.toUpperCase()}</Mono>
              </Pressable>
            ))}
          </View>
          {status !== "paid" && sel.size ? (
            <View style={s.payBox} testID="admin-invoices-pay-box">
              <Mono style={s.title}>{sel.size} SELECTED · {money(selTotal)}</Mono>
              <TextInput testID="admin-invoices-note" value={note} onChangeText={setNote} placeholder={status === "reported" ? "Optional: keeps the shop's note if empty" : "Payment note, e.g. e-Transfer ref 8841"} placeholderTextColor={colors.muted} style={s.input} />
              <View style={s.row}>
                {status === "reported" ? <Btn testID="admin-invoices-reject" label="NOT RECEIVED" variant="outline" loading={busy === "unpaid"} onPress={() => mark(false)} style={{ flex: 1 }} /> : null}
                <Btn testID="admin-invoices-mark-paid" label={status === "reported" ? "CONFIRM PAID" : "MARK PAID"} icon="checkmark" loading={busy === "paid"} disabled={status === "unpaid" && !note.trim()} onPress={() => mark(true)} style={{ flex: 2 }} />
              </View>
            </View>
          ) : null}
        </View>
        {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : !q.data?.invoices.length ? (
          <Mono style={[s.meta, { padding: spacing.lg }]} testID="admin-invoices-empty">No {status} invoices.</Mono>
        ) : q.data.invoices.map((inv) => (
          <Pressable key={inv.id} testID={`admin-invoice-${inv.number}`} onPress={() => status !== "paid" && toggle(inv.id)} style={[s.item, sel.has(inv.id) && { backgroundColor: colors.surfaceSecondary }]}>
            {status !== "paid" ? <Ionicons name={sel.has(inv.id) ? "checkbox" : "square-outline"} size={22} color={colors.onSurface} /> : null}
            <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
              <Mono style={s.main} numberOfLines={1}>{inv.number} · {inv.merchant.name}</Mono>
              <Mono style={s.meta} numberOfLines={1}>{dayLabel(inv.date)} · {inv.delivery.tracking_code} · HST {money(inv.hst)}</Mono>
              {inv.report && inv.status === "reported" ? <Mono style={s.meta} numberOfLines={2}>SHOP SAYS PAID · {inv.report.note}</Mono> : null}
              {inv.paid_at ? <Mono style={s.meta} numberOfLines={1}>PAID {dayLabel(inv.paid_at.slice(0, 10))} · {inv.paid_note}</Mono> : null}
            </View>
            <View style={{ alignItems: "flex-end", gap: 4 }}>
              <Mono style={s.main}>{money(inv.total)}</Mono>
              {status === "paid" ? (
                <Pressable testID={`admin-invoice-unpay-${inv.number}`} onPress={() => mark(false, [inv.id])} style={s.undo}><Mono style={s.chipText}>UNDO</Mono></Pressable>
              ) : <Pill label={inv.status === "reported" ? "to confirm" : "unpaid"} tone={inv.status === "reported" ? "yellow" : "red"} />}
            </View>
          </Pressable>
        ))}
        <Mono style={s.section}>DETAILS PRINTED ON INVOICES</Mono>
        {form ? (
          <View style={s.block} testID="business-form">
            {([["name", "Business name"], ["address", "Business address"], ["hst_number", "HST registration no. (e.g. 123456789 RT0001)"], ["hst_pct", "HST %"]] as const).map(([k, ph]) => (
              <View key={k} style={{ gap: 4 }}>
                <Mono style={s.meta}>{ph.toUpperCase()}</Mono>
                <TextInput testID={`business-${k}`} value={form[k]} onChangeText={(v) => setForm({ ...form, [k]: v })} placeholder={ph} placeholderTextColor={colors.muted}
                  keyboardType={k === "hst_pct" ? "decimal-pad" : "default"} style={s.input} />
              </View>
            ))}
            <Btn testID="business-save" label="SAVE DETAILS" loading={busy === "biz"} onPress={saveBiz} />
            <Mono style={s.meta}>Changes apply to new invoices. Issued invoices keep the details they were issued with.</Mono>
          </View>
        ) : <Loading />}
      </KeyboardAwareScrollView>
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  totals: { flexDirection: "row", borderBottomWidth: 2, borderColor: c.border },
  cell: { flex: 1, padding: spacing.md, gap: 2 },
  val: { fontSize: 24 },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1 },
  block: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  row: { flexDirection: "row", gap: spacing.sm },
  chip: { minHeight: 44, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 12, fontWeight: "700" },
  payBox: { gap: spacing.sm, borderWidth: 2, borderColor: c.border, padding: spacing.sm },
  title: { fontSize: 13, fontWeight: "700" },
  input: { minHeight: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
  item: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.lg, borderBottomWidth: 2, borderColor: c.border, minHeight: 64 },
  main: { fontSize: 13, fontWeight: "700" },
  meta: { fontSize: 11, color: c.muted },
  undo: { minHeight: 36, paddingHorizontal: spacing.sm, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
}));
