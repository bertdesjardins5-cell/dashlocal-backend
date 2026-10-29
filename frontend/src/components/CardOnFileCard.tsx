import React, { useState } from "react";
import { ActivityIndicator, Alert, Linking, Platform, Pressable, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, dayLabel } from "@/src/api";
import { money } from "@/src/components/BillCard";
import SquarePaySheet from "@/src/components/SquarePaySheet";
import { queryClient } from "@/src/query-client";
import { Btn, Display, Mono, Pill } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Charge = { id: string; kind: string; label: string; status: "paid" | "processing" | "failed" | "gave_up" | "superseded"; total: number | null; attempts: number;
  last_error: string | null; next_try_at: string | null; paid_at: string | null; created_at: string; receipt_url: string | null };
type Resp = { configured: boolean; sandbox: boolean; monthly_fee: number; hst_pct: number;
  card: { brand: string; last4: string; exp_month: number; exp_year: number; saved_at: string } | null; charges: Charge[] };
type Notify = (m: string, tone?: "dark" | "red" | "green") => void;

const TONE = { paid: "green", processing: "dark", failed: "red", gave_up: "red", superseded: "light" } as const;
const STATUS = { paid: "charged", processing: "charging", failed: "failed · will retry", gave_up: "failed", superseded: "replaced by newer charge" } as const;

// Merchant autopay: card saved in Square, charged for the monthly platform fee and, each Monday, last week's deliveries.
export default function CardOnFileCard({ merchantId, notify }: { merchantId: string; notify: Notify }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["square", merchantId], queryFn: () => api<Resp>(`/merchants/${merchantId}/square`), refetchInterval: 30000 });
  const d = q.data;

  const addCard = async () => {
    setBusy("add");
    try {
      setUrl((await api<{ url: string }>(`/merchants/${merchantId}/square/card-link`, { method: "POST" })).url);
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const doRemove = async () => {
    setBusy("remove");
    try {
      await api(`/merchants/${merchantId}/square/card`, { method: "DELETE" });
      notify("CARD REMOVED · AUTOPAY IS OFF", "dark");
      q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const remove = () => {
    const msg = "Autopay stops. Invoices stay unpaid until you pay them by card here.";
    if (Platform.OS === "web") {
      if (window.confirm(`Remove this card? ${msg}`)) doRemove();
    } else Alert.alert("Remove this card?", msg, [{ text: "Keep card", style: "cancel" }, { text: "Remove", style: "destructive", onPress: doRemove }]);
  };
  const openReceipt = (u: string) => (Platform.OS === "web" ? window.open(u, "_blank") : Linking.openURL(u));

  if (!d) return <View style={s.block}><ActivityIndicator color={colors.onSurface} /></View>;
  return (
    <View style={s.block} testID="card-on-file">
      <Mono style={s.title}>AUTOPAY · CARD ON FILE</Mono>
      {d.sandbox ? <Pill label="Square test mode" tone="yellow" /> : null}
      {d.card ? (
        <View style={s.cardRow} testID="card-on-file-saved">
          <View style={{ flex: 1, gap: 2 }}>
            <Display style={{ fontSize: 18 }}>{d.card.brand.replace("_", " ")} ••{d.card.last4}</Display>
            <Mono style={s.meta}>EXPIRES {String(d.card.exp_month).padStart(2, "0")}/{d.card.exp_year} · SAVED {new Date(d.card.saved_at).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }).toUpperCase()}</Mono>
          </View>
          <Pill label="autopay on" tone="green" />
        </View>
      ) : (
        <Mono style={s.meta} testID="card-on-file-empty">NO CARD SAVED · ADD ONE TO PAY AUTOMATICALLY</Mono>
      )}
      <Mono style={s.meta}>
        We charge {money(d.monthly_fee)} + {d.hst_pct}% HST ({money(d.monthly_fee * (1 + d.hst_pct / 100))} CAD) at the start of each month, and every Monday the deliveries completed the week before. You get an email receipt after every charge.
      </Mono>
      <View style={s.row}>
        <Btn testID="card-on-file-add" label={d.card ? "REPLACE CARD" : "ADD CARD"} icon="card" loading={busy === "add"} disabled={!d.configured} onPress={addCard} style={{ flex: 2 }} />
        {d.card ? <Btn testID="card-on-file-remove" label="REMOVE" variant="outline" loading={busy === "remove"} onPress={remove} style={{ flex: 1 }} /> : null}
      </View>
      {d.charges.length ? (
        <View style={{ gap: spacing.xs }} testID="card-charges">
          <Mono style={s.title}>RECENT CHARGES</Mono>
          {d.charges.map((c) => (
            <Pressable key={c.id} testID={`card-charge-${c.id}`} disabled={!c.receipt_url} onPress={() => c.receipt_url && openReceipt(c.receipt_url)} style={s.charge}>
              <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                <Mono style={s.main} numberOfLines={1}>{c.label}</Mono>
                <Mono style={s.meta} numberOfLines={2}>
                  {dayLabel((c.paid_at ?? c.created_at).slice(0, 10))}{c.last_error ? ` · ${c.last_error}` : ""}{c.receipt_url ? " · TAP FOR RECEIPT" : ""}
                </Mono>
              </View>
              <View style={{ alignItems: "flex-end", gap: 4 }}>
                {c.total != null ? <Mono style={s.main}>{money(c.total)}</Mono> : null}
                <Pill label={STATUS[c.status] ?? c.status} tone={TONE[c.status] ?? "light"} />
              </View>
            </Pressable>
          ))}
        </View>
      ) : null}
      <SquarePaySheet url={url} title={d.card ? "REPLACE CARD" : "ADD CARD"} onClose={() => setUrl(null)} onDone={(r) => {
        setUrl(null);
        notify(`${r.card ?? "CARD"} SAVED · AUTOPAY IS ON`, "green");
        q.refetch();
        setTimeout(() => { q.refetch(); queryClient.invalidateQueries({ queryKey: ["invoices", merchantId] }); }, 4000);
      }} />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  title: { fontSize: 13, fontWeight: "700" },
  row: { flexDirection: "row", gap: spacing.sm },
  cardRow: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderWidth: 2, borderColor: c.border, padding: spacing.md },
  charge: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderWidth: 2, borderColor: c.border, padding: spacing.sm, minHeight: 56 },
  main: { fontSize: 13, fontWeight: "700" },
  meta: { fontSize: 11, color: c.muted },
}));
