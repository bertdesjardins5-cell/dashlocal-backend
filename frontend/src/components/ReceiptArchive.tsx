import React, { useState } from "react";
import { Linking, Platform, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, BACKEND_URL } from "@/src/api";
import { Btn, Display, Mono } from "@/src/ui";
import { makeStyles, spacing } from "@/src/theme";

type Receipt = { id: string; tracking_code: string; customer_name: string; delivered_at: string };
type Data = { law_office: boolean; receipts: Receipt[] };
type Notify = (m: string, tone?: "dark" | "red" | "green") => void;
const SHOW = 5;

// Law offices: every past signed delivery receipt, one PDF at a time or all as a ZIP.
export default function ReceiptArchive({ merchantId, notify }: { merchantId: string; notify: Notify }) {
  const s = useStyles();
  const q = useQuery({ queryKey: ["receipts", merchantId], queryFn: () => api<Data>(`/merchants/${merchantId}/receipts`) });
  const [busy, setBusy] = useState<string | null>(null);
  const [all, setAll] = useState(false);

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
  const shown = all ? list : list.slice(0, SHOW);
  return (
    <View style={s.block} testID="receipt-archive">
      <Display style={s.title}>SIGNED RECEIPTS · {list.length}</Display>
      <Mono style={s.meta}>Every delivery signed for at the door, as a PDF with the recipient&apos;s signature. Download any time.</Mono>
      {list.length === 0 ? <Mono style={s.meta} testID="receipt-archive-empty">No signed deliveries yet.</Mono> : null}
      {shown.map((r) => (
        <View key={r.id} style={s.row} testID={`receipt-row-${r.id}`}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Mono style={s.code}>{r.tracking_code} · {r.customer_name}</Mono>
            <Mono style={s.meta}>{new Date(r.delivered_at).toLocaleString("en-CA", { timeZone: "America/Toronto", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })}</Mono>
          </View>
          <Btn testID={`receipt-download-${r.id}`} label="PDF" icon="download" variant="outline" loading={busy === r.id} onPress={() => open(r.id)} />
        </View>
      ))}
      {list.length > SHOW ? (
        <Btn testID="receipt-archive-toggle" label={all ? "SHOW FEWER" : `SHOW ALL ${list.length}`} variant="outline" onPress={() => setAll(!all)} />
      ) : null}
      {list.length > 0 ? (
        <Btn testID="receipt-download-all" label="DOWNLOAD ALL (ZIP)" icon="archive" variant="dark" loading={busy === "all"} onPress={() => open(null)} />
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  title: { fontSize: 18, color: c.onSurface },
  meta: { fontSize: 11, color: c.muted },
  code: { fontSize: 13, fontWeight: "700", color: c.onSurface },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm, borderWidth: 2, borderColor: c.border, padding: spacing.sm },
}));
