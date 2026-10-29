import React, { useState } from "react";
import { Platform, Alert, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/src/api";
import { Btn, Display, Mono, Pill } from "@/src/ui";
import { makeStyles, spacing } from "@/src/theme";

type Store = { id: string; name: string; reason: "re-add" | "no-card"; reminded_at: string | null };
type Resp = { configured: boolean; sandbox: boolean; stores: Store[]; re_add: number; no_card: number };

// Admin: email every store without a working card a one-tap link to Square's secure card page (valid 7 days).
export default function CardReminderCard({ notify }: { notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const q = useQuery({ queryKey: ["card-reminders"], queryFn: () => api<Resp>("/admin/square/card-reminders") });
  const [busy, setBusy] = useState(false);
  const d = q.data;
  if (!d) return null;
  const total = d.stores.length;

  const send = async () => {
    setBusy(true);
    try {
      const r = await api<{ sent: number; results: { status: string }[] }>("/admin/square/card-reminders", { method: "POST" });
      const recent = r.results.filter((x) => x.status === "skipped_recent").length;
      notify(`${r.sent} STORE${r.sent === 1 ? "" : "S"} EMAILED${recent ? ` · ${recent} ALREADY EMAILED TODAY` : ""}`, r.sent ? "green" : "dark");
      q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };
  const confirm = () => {
    const msg = `Email ${total} store${total === 1 ? "" : "s"} a secure link to add their card for autopay?`;
    if (Platform.OS === "web") {
      if (window.confirm(msg)) send();
    } else Alert.alert("Send card emails?", msg, [{ text: "Cancel", style: "cancel" }, { text: "Send", onPress: send }]);
  };

  return (
    <View style={s.card} testID="card-reminders">
      <Display style={s.title}>STORE AUTOPAY CARDS</Display>
      {total === 0 ? (
        <Pill label="EVERY STORE HAS A WORKING CARD" tone="green" />
      ) : (
        <>
          <Mono style={s.meta} testID="card-reminders-counts">
            {d.re_add} NEED TO RE-ADD (TEST-MODE CARD) · {d.no_card} HAVE NO CARD
          </Mono>
          {d.stores.slice(0, 6).map((x) => (
            <Mono key={x.id} style={s.meta}>• {x.name} — {x.reason === "re-add" ? "re-add" : "no card"}{x.reminded_at ? " · emailed" : ""}</Mono>
          ))}
          {total > 6 ? <Mono style={s.meta}>…and {total - 6} more</Mono> : null}
          <Btn testID="card-reminders-send" label={`EMAIL ${total} STORE${total === 1 ? "" : "S"} A CARD LINK`} icon="mail" loading={busy}
            disabled={busy || !d.configured || d.sandbox} onPress={confirm} />
          <Mono style={s.meta}>{d.sandbox ? "Available once Square is live." : "One tap opens Square's secure card page. Links work for 7 days. Each store is emailed at most once a day."}</Mono>
        </>
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  card: { borderWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary, padding: spacing.md, gap: spacing.xs },
  title: { fontSize: 16, color: c.onSurface },
  meta: { fontSize: 12, color: c.onSurfaceSecondary },
}));
