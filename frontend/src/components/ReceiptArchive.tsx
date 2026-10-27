import React, { useMemo, useState } from "react";
import { Linking, Platform, Pressable, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, BACKEND_URL } from "@/src/api";
import { Btn, Display, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Receipt = { id: string; tracking_code: string; customer_name: string; delivered_at: string; matter_ref?: string | null };
type Data = { law_office: boolean; receipts: Receipt[] };
type Notify = (m: string, tone?: "dark" | "red" | "green") => void;
const SHOW = 5;

// Law offices: every past signed delivery receipt, one PDF at a time or all as a ZIP.
export default function ReceiptArchive({ merchantId, notify }: { merchantId: string; notify: Notify }) {
  const s = useStyles();
  const q = useQuery({ queryKey: ["receipts", merchantId], queryFn: () => api<Data>(`/merchants/${merchantId}/receipts`) });
  const [busy, setBusy] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const [search, setSearch] = useState("");
  const { colors } = useTheme();
  const index = useMemo(() => (q.data?.receipts ?? []).map((r) => ({ r, text: searchText(r) })), [q.data]);

  const open = async (stopId: string | null) => {
    // Safari blocks window.open after an await, so open the tab synchronously inside the tap
    const popup = Platform.OS === "web" ? window.open("", "_blank") : null;
    setBusy(stopId ?? "all");
    try {
      const r = await api<{ path: string }>(`/merchants/${merchantId}/receipts/link`, { method: "POST", body: { stop_id: stopId } });
      const url = `${BACKEND_URL}${r.path}`;
      if (Platform.OS === "web") {
        if (popup) popup.location.href = url;
        else window.location.href = url;
      } else await Linking.openURL(url);
      notify(stopId ? "RECEIPT DOWNLOADING" : "ALL RECEIPTS DOWNLOADING (ZIP)", "green");
    } catch (e: any) {
      popup?.close();
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  if (!q.data?.law_office) return null;
  const list = q.data.receipts;
  const needle = search.trim().toLowerCase();
  const terms = needle.replace(/\./g, "").split(/[\s,]+/).filter(Boolean);
  const found = terms.length ? index.filter((x) => terms.every((t) => x.text.includes(t))).map((x) => x.r) : list;
  const shown = all || needle ? found : found.slice(0, SHOW);
  return (
    <View style={s.block} testID="receipt-archive">
      <Display style={s.title}>SIGNED RECEIPTS · {list.length}</Display>
      <Mono style={s.meta}>Every delivery signed for at the door, as a PDF with the recipient&apos;s signature. Download any time.</Mono>
      {list.length > 0 ? (
        <View style={s.searchRow}>
          <TextInput testID="receipt-search-input" value={search} onChangeText={setSearch} placeholder="Client, matter, code or date"
            placeholderTextColor={colors.muted} autoCorrect={false} autoCapitalize="none" style={s.search} />
          {search ? (
            <Pressable testID="receipt-search-clear" onPress={() => setSearch("")} style={s.clear} accessibilityLabel="Clear search">
              <Mono style={s.code}>✕</Mono>
            </Pressable>
          ) : null}
        </View>
      ) : null}
      {needle ? <Mono style={s.meta} testID="receipt-search-count">{found.length} OF {list.length} MATCH</Mono> : null}
      {list.length === 0 ? <Mono style={s.meta} testID="receipt-archive-empty">No signed deliveries yet.</Mono> : null}
      {needle && found.length === 0 ? <Mono style={s.meta} testID="receipt-search-empty">No receipts match “{search.trim()}”.</Mono> : null}
      {shown.map((r) => (
        <View key={r.id} style={s.row} testID={`receipt-row-${r.id}`}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Mono style={s.code}>{r.tracking_code} · {r.customer_name}</Mono>
            <Mono style={s.meta}>{r.matter_ref ? `MATTER ${r.matter_ref} · ` : ""}{when(r.delivered_at)}</Mono>
          </View>
          <Btn testID={`receipt-download-${r.id}`} label="PDF" icon="download" variant="outline" loading={busy === r.id} onPress={() => open(r.id)} />
        </View>
      ))}
      {!needle && found.length > SHOW ? (
        <Btn testID="receipt-archive-toggle" label={all ? "SHOW FEWER" : `SHOW ALL ${list.length}`} variant="outline" onPress={() => setAll(!all)} />
      ) : null}
      {list.length > 0 ? (
        <Btn testID="receipt-download-all" label={`DOWNLOAD ALL ${list.length} (ZIP)`} icon="archive" variant="dark" loading={busy === "all"} onPress={() => open(null)} />
      ) : null}
    </View>
  );
}

const TZ = "America/Toronto";
const when = (iso: string) => new Date(iso).toLocaleString("en-CA", { timeZone: TZ, month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
// what a search can match (every word must match): client name, tracking code and the date in common forms — not the time
const searchText = (r: Receipt) => {
  const d = new Date(r.delivered_at);
  const iso = d.toLocaleDateString("en-CA", { timeZone: TZ }); // YYYY-MM-DD
  const [y, m, day] = iso.split("-");
  const opt = (o: Intl.DateTimeFormatOptions) => d.toLocaleString("en-CA", { timeZone: TZ, ...o });
  const dd = String(Number(day));
  return [r.customer_name, r.tracking_code, r.matter_ref ?? "", iso, `${y}/${m}/${day}`, `${day}/${m}/${y}`, `${m}/${day}/${y}`,
    opt({ month: "long" }), opt({ month: "short" }).replace(".", ""), dd, y].join(" ").toLowerCase();
};

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  title: { fontSize: 18, color: c.onSurface },
  meta: { fontSize: 11, color: c.muted },
  code: { fontSize: 13, fontWeight: "700", color: c.onSurface },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm, borderWidth: 2, borderColor: c.border, padding: spacing.sm },
  searchRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  clear: { width: 48, height: 48, borderWidth: 2, borderColor: c.border, alignItems: "center", justifyContent: "center" },
  search: { flex: 1, minHeight: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface, backgroundColor: c.surface },
}));
