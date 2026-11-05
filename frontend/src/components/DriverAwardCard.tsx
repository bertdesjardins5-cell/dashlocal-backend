import React, { useState } from "react";
import { ActivityIndicator, Pressable, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, dayLabel, fmtDepotTime } from "@/src/api";
import { Btn, Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Winner = { driver_id: string; name: string; avg: number; count: number; comments: { stars: number; comment: string }[]; compliments: [string, number][] };
type Award = {
  month: string; label: string; winner: Winner | null; min_ratings: number; to: string | null;
  last: { status: "sent" | "failed"; error: string | null; created_at: string; month: string; winner: string | null } | null;
};

const when = (iso: string) => `${dayLabel(new Date(iso).toLocaleDateString("en-CA", { timeZone: "America/Toronto" }))} ${fmtDepotTime(iso)}`;

// Admin: last month's top-rated driver, emailed automatically on the 1st at 8:00 Eastern.
export default function DriverAwardCard({ notify }: { notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["driver-award"], queryFn: () => api<Award>("/admin/driver-award") });
  const [busy, setBusy] = useState(false);
  const a = q.data;
  if (!a) {
    return q.isError ? (
      <Pressable testID="driver-award-retry" onPress={() => q.refetch()} style={s.block}>
        <Mono style={[s.title, { color: colors.brandPrimary }]}>COULD NOT LOAD DRIVER OF THE MONTH · TAP TO RETRY</Mono>
      </Pressable>
    ) : (
      <View style={s.block}><ActivityIndicator color={colors.onSurface} /></View>
    );
  }

  const sendNow = async () => {
    setBusy(true);
    try {
      const r = await api<{ status: string; error: string | null }>("/admin/driver-award/send-now", { method: "POST" });
      notify(r.status === "sent" ? "DRIVER OF THE MONTH EMAILED" : `EMAIL NOT SENT: ${r.error}`, r.status === "sent" ? "green" : "red");
      q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  const w = a.winner;
  return (
    <View style={s.block} testID="driver-award-card">
      <Mono style={s.meta}>{a.label.toUpperCase()}</Mono>
      {w ? (
        <>
          <View style={s.row}>
            <Ionicons name="trophy" size={28} color={colors.brandPrimary} />
            <View style={{ flex: 1 }}>
              <Display style={s.name} testID="driver-award-winner">{w.name}</Display>
              <Mono style={s.meta}>{w.avg.toFixed(2)}★ from {w.count} customer ratings</Mono>
            </View>
          </View>
          {w.comments.map((c, i) => (
            <Mono key={i} style={s.comment} testID="driver-award-comment">{"★".repeat(c.stars)} &ldquo;{c.comment}&rdquo;</Mono>
          ))}
        </>
      ) : (
        <Mono style={s.meta} testID="driver-award-none">No driver had {a.min_ratings}+ ratings last month, so there&apos;s no winner yet.</Mono>
      )}
      <Mono style={s.meta}>Emailed to {a.to ?? "—"} on the 1st of each month · 8:00 Eastern time</Mono>
      {a.last ? (
        <Mono style={[s.meta, a.last.status !== "sent" && { color: colors.brandPrimary, fontWeight: "700" }]} testID="driver-award-last">
          LAST: {when(a.last.created_at)} · {a.last.status.toUpperCase()}{a.last.error ? ` · ${a.last.error}` : ""}
        </Mono>
      ) : null}
      <Btn testID="driver-award-send-now" label="EMAIL IT NOW" icon="mail" variant="outline" loading={busy} onPress={sendNow} />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  name: { fontSize: 22 },
  title: { fontSize: 13, fontWeight: "700" },
  meta: { fontSize: 11, color: c.muted },
  comment: { fontSize: 12, color: c.onSurface, fontStyle: "italic", paddingLeft: spacing.sm, borderLeftWidth: 2, borderColor: c.brandPrimary },
}));
