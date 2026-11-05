import React, { useState } from "react";
import { ActivityIndicator, Linking, Platform, Pressable, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, BACKEND_URL, dayLabel } from "@/src/api";
import { money } from "@/src/components/BillCard";
import SquarePaySheet, { SquareResult } from "@/src/components/SquarePaySheet";
import { queryClient } from "@/src/query-client";
import { Btn, Display, Mono, Pill } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

export type Invoice = {
  id: string; number: string; date: string; status: "unpaid" | "reported" | "paid"; paid_at: string | null; paid_note: string | null; report?: { note: string; at: string } | null;
  merchant: { name: string; address: string | null }; delivery: { tracking_code: string; customer_name: string; address: string; km: number; priority: boolean };
  lines: { description: string; amount: number }[]; subtotal: number; hst_pct: number; hst: number; total: number; merchant_id: string;
};
type Resp = { outstanding: number; paid: number; unpaid_count: number; count: number; invoices: Invoice[]; billing_address: string | null;
  batch_savings?: { month: string; label: string; drops: number; amount: number } };
type Filter = "all" | "unpaid" | "paid";
const LIMIT = 10;

// Merchant: one invoice per completed delivery (fee + 13% HST), balances, PDF download, billing address.
export default function InvoicesCard({ merchantId, notify }: { merchantId: string; notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [payUrl, setPayUrl] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [addr, setAddr] = useState<string | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");
  const toggleSel = (id: string) => setSel((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const months = Array.from({ length: 6 }, (_, i) => { const t = new Date(); t.setDate(1); t.setMonth(t.getMonth() - i); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}`; });
  const [month, setMonth] = useState(months[0]);
  const q = useQuery({ queryKey: ["invoices", merchantId, filter], queryFn: () => api<Resp>(`/merchants/${merchantId}/invoices${filter === "all" ? "" : `?status=${filter}`}`), refetchInterval: 30000 });
  const d = q.data;

  const download = async (inv: Invoice) => {
    setBusy(inv.id);
    try {
      const r = await api<{ path: string }>(`/merchants/${merchantId}/invoices/${inv.id}/link`, { method: "POST" });
      const url = `${BACKEND_URL}${r.path}`;
      if (Platform.OS === "web") window.location.href = url;
      else await Linking.openURL(url);
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const openUrl = async (path: string) => {
    const url = path.startsWith("http") ? path : `${BACKEND_URL}${path}`;
    if (Platform.OS === "web") window.location.href = url;
    else await Linking.openURL(url);
  };
  const downloadStatement = async () => {
    setBusy("statement");
    try {
      const r = await api<{ path: string }>(`/merchants/${merchantId}/statements/link`, { method: "POST", body: { month } });
      await openUrl(r.path);
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const payCard = async () => {
    setBusy("card");
    try {
      setPayUrl((await api<{ url: string }>(`/merchants/${merchantId}/invoices/square-pay`, { method: "POST", body: { ids: Array.from(sel) } })).url);
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const onPaid = (r: SquareResult) => {
    setPayUrl(null);
    setSel(new Set());
    notify(`PAYMENT RECEIVED · ${money(r.total)} · ${r.count} INVOICE${r.count === 1 ? "" : "S"} PAID · RECEIPT EMAILED`, "green");
    q.refetch();
    queryClient.invalidateQueries({ queryKey: ["square", merchantId] });
  };
  const report = async () => {
    setBusy("report");
    try {
      const r = await api<{ updated: number }>(`/merchants/${merchantId}/invoices/report-paid`, { method: "POST", body: { ids: Array.from(sel), note } });
      notify(`${r.updated} INVOICE${r.updated === 1 ? "" : "S"} REPORTED PAID · LOCALDASH WILL CONFIRM`, "green");
      setSel(new Set());
      setNote("");
      q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const saveAddr = async () => {
    setBusy("addr");
    try {
      await api(`/merchants/${merchantId}/billing-address`, { method: "PUT", body: { address: addr } });
      setAddr(null);
      q.refetch();
      notify("BILLING ADDRESS SAVED", "green");
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  if (!d) {
    return q.isError ? (
      <Pressable testID="invoices-retry" onPress={() => q.refetch()} style={s.block}><Mono style={[s.title, { color: colors.brandPrimary }]}>COULD NOT LOAD INVOICES · TAP TO RETRY</Mono></Pressable>
    ) : <View style={s.block}><ActivityIndicator color={colors.onSurface} /></View>;
  }
  return (
    <View testID="invoices-card">
      <View style={s.totals}>
        <View style={s.cell} testID="invoices-outstanding">
          <Display style={[s.val, d.outstanding > 0 && { color: colors.brandPrimary }]}>{money(d.outstanding)}</Display>
          <Mono style={s.label}>OUTSTANDING · {d.unpaid_count}</Mono>
        </View>
        <View style={[s.cell, { borderLeftWidth: 2, borderColor: colors.border }]} testID="invoices-paid">
          <Display style={s.val}>{money(d.paid)}</Display>
          <Mono style={s.label}>PAID</Mono>
        </View>
      </View>
      {d.batch_savings && d.batch_savings.drops > 0 ? (
        <View style={s.savings} testID="batch-savings-badge">
          <Display style={[s.val, { color: colors.brandForest }]}>{money(d.batch_savings.amount)}</Display>
          <Mono style={s.label}>
            SAVED WITH BATCH PRICING · {d.batch_savings.label.toUpperCase()} · {d.batch_savings.drops} DROP{d.batch_savings.drops === 1 ? "" : "S"} AT THE BATCH RATE
          </Mono>
        </View>
      ) : null}
      <View style={s.block}>
        <View style={s.row}>
          {(["all", "unpaid", "paid"] as Filter[]).map((f) => (
            <Pressable key={f} testID={`invoices-filter-${f}`} onPress={() => { setFilter(f); setAll(false); }} style={[s.chip, filter === f && { backgroundColor: colors.surfaceInverse }]}>
              <Mono style={[s.chipText, filter === f && { color: colors.onSurfaceInverse }]}>{f.toUpperCase()}</Mono>
            </Pressable>
          ))}
        </View>
        {d.invoices.slice(0, all ? undefined : LIMIT).map((inv) => (
          <View key={inv.id} style={s.inv}>
            <Pressable testID={`invoice-${inv.number}`} onPress={() => setOpen(open === inv.id ? null : inv.id)} style={s.invHead}>
              <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                <Mono style={s.main} numberOfLines={1}>{inv.number} · {inv.delivery.tracking_code}</Mono>
                <Mono style={s.meta} numberOfLines={1}>{dayLabel(inv.date)} · {inv.delivery.customer_name}</Mono>
              </View>
              <View style={{ alignItems: "flex-end", gap: 4 }}>
                <Mono style={s.main}>{money(inv.total)}</Mono>
                <Pill label={inv.status === "paid" ? "paid" : inv.status === "reported" ? "payment reported" : "unpaid"} tone={inv.status === "paid" ? "green" : inv.status === "reported" ? "yellow" : "red"} />
              </View>
            </Pressable>
            {open === inv.id ? (
              <View style={s.detail} testID={`invoice-detail-${inv.number}`}>
                <Mono style={s.meta}>{inv.delivery.address}{inv.delivery.priority ? " · PRIORITY" : ""}</Mono>
                {inv.lines.map((l) => (
                  <View key={l.description} style={s.lineRow}><Mono style={s.lineText}>{l.description}</Mono><Mono style={s.lineText}>{money(l.amount)}</Mono></View>
                ))}
                <View style={s.lineRow}><Mono style={s.lineText}>SUBTOTAL</Mono><Mono style={s.lineText}>{money(inv.subtotal)}</Mono></View>
                <View style={s.lineRow}><Mono style={s.lineText}>HST {inv.hst_pct}%</Mono><Mono style={s.lineText} testID={`invoice-hst-${inv.number}`}>{money(inv.hst)}</Mono></View>
                <View style={s.lineRow}><Mono style={s.main}>TOTAL</Mono><Mono style={s.main}>{money(inv.total)}</Mono></View>
                {inv.paid_at ? <Mono style={s.meta}>PAID {dayLabel(inv.paid_at.slice(0, 10))}{inv.paid_note ? ` · ${inv.paid_note}` : ""}</Mono> : null}
                {inv.status === "reported" && inv.report ? <Mono style={s.meta}>YOU REPORTED PAYMENT {dayLabel(inv.report.at.slice(0, 10))} · {inv.report.note} · WAITING FOR LOCALDASH TO CONFIRM</Mono> : null}
                {inv.status !== "paid" ? (
                  <Btn testID={`invoice-select-${inv.number}`} label={sel.has(inv.id) ? "SELECTED TO PAY" : "SELECT TO PAY"} icon={sel.has(inv.id) ? "checkbox" : "square-outline"} variant="outline" onPress={() => toggleSel(inv.id)} />
                ) : null}
                <Btn testID={`invoice-download-${inv.number}`} label="DOWNLOAD PDF" icon="document-text" variant="outline" loading={busy === inv.id} onPress={() => download(inv)} />
              </View>
            ) : null}
          </View>
        ))}
        {d.invoices.length > LIMIT && !all ? (
          <Pressable testID="invoices-show-all" onPress={() => setAll(true)} style={s.more}><Mono style={s.chipText}>SHOW ALL {d.invoices.length}</Mono></Pressable>
        ) : null}
        {sel.size || d.invoices.some((i) => i.status !== "paid") ? (
          <View style={s.payBox} testID="invoices-report-box">
            <Mono style={s.title}>PAY OUTSTANDING INVOICES</Mono>
            {sel.size ? null : (
              <Btn testID="invoices-select-all-unpaid" label="SELECT ALL UNPAID" variant="outline" onPress={() => setSel(new Set(d.invoices.filter((i) => i.status !== "paid").map((i) => i.id)))} />
            )}
            {sel.size ? (
              <>
                <Mono style={s.meta}>{sel.size} INVOICE{sel.size === 1 ? "" : "S"} · {money(d.invoices.filter((i) => sel.has(i.id)).reduce((a, i) => a + i.total, 0))}</Mono>
                <View style={s.row}>
                  <Btn testID="invoices-report-clear" label="CLEAR" variant="outline" onPress={() => setSel(new Set())} style={{ flex: 1 }} />
                  <Btn testID="invoices-pay-card" label="PAY BY CARD" icon="card" loading={busy === "card"} onPress={payCard} style={{ flex: 2 }} />
                </View>
                <Mono style={s.meta}>OR, IF YOU PAID BY E-TRANSFER OR CHEQUE:</Mono>
                <TextInput testID="invoices-report-note" value={note} onChangeText={setNote} placeholder="How you paid, e.g. e-Transfer ref 8841" placeholderTextColor={colors.muted} style={s.input} />
                <Btn testID="invoices-report-submit" label="TELL LOCALDASH I PAID" icon="send" variant="outline" loading={busy === "report"} disabled={!note.trim()} onPress={report} />
              </>
            ) : null}
          </View>
        ) : null}
        {d.invoices.length === 0 ? <Mono style={s.meta} testID="invoices-empty">{filter === "all" ? "AN INVOICE IS CREATED FOR EACH COMPLETED DELIVERY" : `NO ${filter.toUpperCase()} INVOICES`}</Mono> : null}

        <Mono style={[s.title, { marginTop: spacing.sm }]}>MONTHLY STATEMENT (PDF)</Mono>
        <View style={[s.row, { flexWrap: "wrap" }]}>
          {months.map((mo) => (
            <Pressable key={mo} testID={`statement-month-${mo}`} onPress={() => setMonth(mo)} style={[s.chip, month === mo && { backgroundColor: colors.surfaceInverse }]}>
              <Mono style={[s.chipText, month === mo && { color: colors.onSurfaceInverse }]}>{new Date(`${mo}-15T12:00:00`).toLocaleDateString("en-CA", { month: "short", year: "numeric" }).toUpperCase()}</Mono>
            </Pressable>
          ))}
        </View>
        <Btn testID="statement-download" label="DOWNLOAD STATEMENT" icon="document-text" variant="outline" loading={busy === "statement"} onPress={downloadStatement} />

        <Mono style={[s.title, { marginTop: spacing.sm }]}>BILLING ADDRESS ON INVOICES</Mono>
        {addr === null ? (
          <Pressable testID="billing-address-edit" onPress={() => setAddr(d.billing_address ?? "")} style={s.addrRow}>
            <Mono style={[s.meta, { flex: 1 }]} testID="billing-address">{d.billing_address || "NOT SET · TAP TO ADD"}</Mono>
            <Mono style={s.chipText}>EDIT</Mono>
          </Pressable>
        ) : (
          <View style={{ gap: spacing.sm }}>
            <TextInput testID="billing-address-input" value={addr} onChangeText={setAddr} placeholder="Street, city, province, postal code" placeholderTextColor={colors.muted} style={s.input} />
            <View style={s.row}>
              <Btn testID="billing-address-cancel" label="CANCEL" variant="outline" onPress={() => setAddr(null)} style={{ flex: 1 }} />
              <Btn testID="billing-address-save" label="SAVE" loading={busy === "addr"} disabled={!addr.trim()} onPress={saveAddr} style={{ flex: 2 }} />
            </View>
          </View>
        )}
        <Mono style={s.meta}>Prices in CAD plus {d.invoices[0]?.hst_pct ?? 13}% HST (Ontario). With a card on file, invoices are charged automatically; otherwise pay them by card here.</Mono>
      </View>
      <SquarePaySheet url={payUrl} title="PAY INVOICES" onClose={() => setPayUrl(null)} onDone={onPaid} />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  totals: { flexDirection: "row", borderBottomWidth: 2, borderColor: c.border },
  savings: { padding: spacing.lg, gap: 4, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  cell: { flex: 1, padding: spacing.md, gap: 2 },
  val: { fontSize: 24 },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1 },
  block: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  title: { fontSize: 13, fontWeight: "700" },
  row: { flexDirection: "row", gap: spacing.sm },
  chip: { minHeight: 44, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 12, fontWeight: "700" },
  inv: { borderWidth: 2, borderColor: c.border },
  invHead: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.sm, minHeight: 56 },
  detail: { padding: spacing.sm, gap: spacing.xs, borderTopWidth: 1, borderColor: c.surfaceSecondary, backgroundColor: c.surfaceSecondary },
  lineRow: { flexDirection: "row", justifyContent: "space-between", gap: spacing.sm },
  lineText: { fontSize: 12 },
  main: { fontSize: 13, fontWeight: "700" },
  meta: { fontSize: 11, color: c.muted },
  more: { minHeight: 44, justifyContent: "center", alignItems: "center", borderWidth: 2, borderColor: c.border },
  payBox: { gap: spacing.sm, borderWidth: 2, borderColor: c.border, padding: spacing.sm },
  addrRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, minHeight: 44, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.sm },
  input: { minHeight: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
}));
