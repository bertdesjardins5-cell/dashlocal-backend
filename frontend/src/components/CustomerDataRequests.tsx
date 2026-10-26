import React, { useState } from "react";
import { Platform, TextInput, View, Linking } from "react-native";
import { api, BACKEND_URL, fmtDepotTime } from "@/src/api";
import { Btn, Mono, Pill } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Lookup = {
  phone: string; names: string[]; orders: number; saved_notes: number;
  latest: { tracking_code: string; store: string | null; status: string; created_at: string | null; erased_at: string | null }[];
  requests: { kind: string; at: string; by: string; orders: number }[];
};

// Admin: PIPEDA access / erasure requests for one customer, found by phone number.
export default function CustomerDataRequests({ notify }: { notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [phone, setPhone] = useState("");
  const [res, setRes] = useState<Lookup | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);

  const lookup = async (p = phone) => {
    setBusy("find");
    setConfirm(null);
    try {
      setRes(await api<Lookup>(`/admin/customers/lookup?phone=${encodeURIComponent(p)}`));
    } catch (e: any) {
      setRes(null);
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const download = async () => {
    if (!res) return;
    const popup = Platform.OS === "web" ? window.open("", "_blank") : null; // Safari: open the tab inside the tap
    setBusy("export");
    try {
      const r = await api<{ path: string }>(`/admin/customers/export-link?phone=${encodeURIComponent(res.phone)}`, { method: "POST" });
      const url = `${BACKEND_URL}${r.path}`;
      if (Platform.OS === "web") {
        if (popup) popup.location.href = url;
        else window.location.href = url;
      } else await Linking.openURL(url);
      notify("CUSTOMER FILE DOWNLOADING", "green");
      setTimeout(() => lookup(res.phone), 1500);
    } catch (e: any) {
      popup?.close();
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const erase = async () => {
    if (!res) return;
    setBusy("erase");
    try {
      const r = await api<{ erased_orders: number; deleted_saved_notes: number }>("/admin/customers/erase", { method: "POST", body: { phone: res.phone, confirm } });
      notify(`ERASED ${r.erased_orders} ORDER${r.erased_orders === 1 ? "" : "S"} · ${r.deleted_saved_notes} SAVED NOTE${r.deleted_saved_notes === 1 ? "" : "S"}`, "green");
      setConfirm(null);
      lookup(res.phone);
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  return (
    <View style={s.block} testID="customer-data-requests">
      <Mono style={s.meta}>When a customer asks what we hold about them, or asks us to delete it, find them by phone. Every export and erasure is logged.</Mono>
      <View style={s.row}>
        <TextInput testID="customer-phone-lookup" value={phone} onChangeText={setPhone} placeholder="+15195550123" keyboardType="phone-pad"
          placeholderTextColor={colors.muted} style={[s.input, { flex: 1 }]} />
        <Btn testID="customer-lookup-button" label="FIND" loading={busy === "find"} disabled={!phone.trim()} onPress={() => lookup()} />
      </View>
      {res ? (
        <View style={{ gap: spacing.sm }} testID="customer-lookup-result">
          <Mono style={s.strong}>{res.phone} · {res.names.join(", ") || "no name on file"}</Mono>
          <View style={s.row}>
            <Pill label={`${res.orders} ORDERS`} tone="light" />
            <Pill label={`${res.saved_notes} SAVED NOTES`} tone="light" />
          </View>
          {res.latest.map((o) => (
            <Mono key={o.tracking_code} style={s.meta}>{o.tracking_code} · {o.store ?? "—"} · {o.status.toUpperCase()}{o.erased_at ? " · ERASED" : ""}</Mono>
          ))}
          {res.requests.map((r, i) => (
            <Mono key={i} style={s.meta} testID="customer-request-log">{r.kind.toUpperCase()} {fmtDepotTime(r.at)} · {r.orders} orders</Mono>
          ))}
          <View style={s.row}>
            <Btn testID="customer-export-button" label="DOWNLOAD FILE" icon="download" variant="dark" loading={busy === "export"} disabled={!res.orders && !res.saved_notes} onPress={download} style={{ flex: 1 }} />
            <Btn testID="customer-erase-button" label="ERASE" icon="trash" loading={busy === "erase"} disabled={!res.orders && !res.saved_notes} onPress={() => setConfirm("")} style={{ flex: 1 }} />
          </View>
          {confirm !== null ? (
            <View style={s.warn} testID="customer-erase-confirm">
              <Mono style={[s.meta, { color: colors.onWarning }]}>This permanently removes the name, phone, address, notes, photos and signatures from all {res.orders} orders. Invoice totals stay. Type ERASE to confirm.</Mono>
              <View style={s.row}>
                <TextInput testID="customer-erase-input" value={confirm} onChangeText={setConfirm} autoCapitalize="characters" style={[s.input, { flex: 1 }]} />
                <Btn testID="customer-erase-confirm-button" label="CONFIRM" disabled={confirm !== "ERASE"} loading={busy === "erase"} onPress={erase} />
              </View>
            </View>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm, flexWrap: "wrap" },
  meta: { fontSize: 12, color: c.muted },
  strong: { fontSize: 13, fontWeight: "700", color: c.onSurface },
  input: { height: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface, minWidth: 120 },
  warn: { backgroundColor: c.warning, padding: spacing.md, gap: spacing.sm },
}));
