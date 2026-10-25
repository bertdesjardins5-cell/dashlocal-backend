import React, { useState } from "react";
import { ActivityIndicator, Pressable, Switch, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, fmtDepotTime, dayLabel } from "@/src/api";
import { Btn, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Log = { status: "sent" | "partial" | "failed"; error: string | null; created_at: string; trigger: string; week_start: string; week_end: string };
type Weekly = { enabled: boolean; recipients: string[]; next_send: string; last: Log | null };

const when = (iso: string) => `${dayLabel(new Date(iso).toLocaleDateString("en-CA", { timeZone: "America/Los_Angeles" }))} ${fmtDepotTime(iso)}`;

// Merchant: Monday-morning email with last week's summary and a 7-day CSV download link.
export default function WeeklyEmailCard({ merchantId, notify }: { merchantId: string; notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["weekly-email", merchantId], queryFn: () => api<Weekly>(`/merchants/${merchantId}/weekly-email`) });
  const [busy, setBusy] = useState<"toggle" | "send" | null>(null);
  const w = q.data;
  if (!w) {
    return q.isError ? (
      <Pressable testID="weekly-email-retry" onPress={() => q.refetch()} style={s.block}>
        <Mono style={[s.title, { color: colors.brandPrimary }]}>COULD NOT LOAD WEEKLY EMAIL · TAP TO RETRY</Mono>
      </Pressable>
    ) : (
      <View style={s.block} testID="weekly-email-loading"><ActivityIndicator color={colors.onSurface} /></View>
    );
  }

  const toggle = async (enabled: boolean) => {
    setBusy("toggle");
    try {
      await api(`/merchants/${merchantId}/weekly-email`, { method: "PUT", body: { enabled } });
      notify(enabled ? "WEEKLY EMAIL ON · MONDAYS 8:00" : "WEEKLY EMAIL OFF", "green");
      q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  const sendNow = async () => {
    setBusy("send");
    try {
      const r = await api<Weekly & { sent: Log }>(`/merchants/${merchantId}/weekly-email/send-now`, { method: "POST" });
      notify(r.sent.status === "sent" ? "LAST WEEK'S REPORT EMAILED" : `EMAIL NOT SENT: ${r.sent.error}`, r.sent.status === "sent" ? "green" : "red");
      q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  return (
    <View style={s.block} testID="weekly-email-card">
      <View testID="weekly-email-toggle" style={s.row}>
        <View style={{ flex: 1, gap: 2 }}>
          <Mono style={s.title}>EMAIL ME EVERY MONDAY</Mono>
          <Mono style={s.meta} numberOfLines={2}>To {w.recipients.join(", ") || "—"} · 8:00 SF time</Mono>
        </View>
        <Switch value={w.enabled} onValueChange={toggle} disabled={busy === "toggle"} trackColor={{ true: colors.brandPrimary, false: colors.surfaceSecondary }} testID="weekly-email-switch" />
      </View>
      <Mono style={s.meta} testID="weekly-email-next">{w.enabled ? `NEXT: ${when(w.next_send)}` : "PAUSED — NO WEEKLY EMAILS"}</Mono>
      {w.last ? (
        <Mono style={[s.meta, w.last.status !== "sent" && { color: colors.brandPrimary, fontWeight: "700" }]} testID="weekly-email-last">
          LAST: {when(w.last.created_at)} · {w.last.status.toUpperCase()}{w.last.error ? ` · ${w.last.error}` : ""}
        </Mono>
      ) : null}
      <Btn testID="weekly-email-send-now" label="EMAIL LAST WEEK NOW" icon="mail" variant="outline" loading={busy === "send"} onPress={sendNow} />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, minHeight: 48 },
  title: { fontSize: 13, fontWeight: "700" },
  meta: { fontSize: 11, color: c.muted },
}));
