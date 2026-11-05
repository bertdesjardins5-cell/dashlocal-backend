import React, { useState } from "react";
import { ActivityIndicator, Pressable, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api, dayLabel, fmtDepotTime } from "@/src/api";
import { Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Ratings = {
  count: number; avg: number | null; stars: Record<string, number>;
  drivers: { driver_id: string; name: string; count: number; avg: number }[];
  recent: { stop_id: string; tracking_code: string; customer_name: string; driver: string | null; shop: string | null; stars: number; tags: string[]; comment: string | null; rated_at: string | null }[];
};

// Store (merchantId) or admin (no merchantId): customer ratings, per-driver averages and latest comments.
export default function RatingsCard({ merchantId }: { merchantId?: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  const path = merchantId ? `/merchants/${merchantId}/ratings` : "/admin/ratings";
  const q = useQuery({ queryKey: ["ratings", merchantId ?? "all"], queryFn: () => api<Ratings>(path), refetchInterval: 60000 });
  const d = q.data;
  const [all, setAll] = useState(false);
  if (!d) {
    return q.isError ? (
      <Pressable testID="ratings-retry" onPress={() => q.refetch()} style={s.block}>
        <Mono style={[s.title, { color: colors.brandPrimary }]}>COULD NOT LOAD RATINGS · TAP TO RETRY</Mono>
      </Pressable>
    ) : (
      <View style={s.block}><ActivityIndicator color={colors.onSurface} /></View>
    );
  }
  if (!d.count) return <View style={s.block} testID="ratings-card"><Mono style={s.meta} testID="ratings-empty">No ratings yet. Customers can rate each delivery on their tracking page after it arrives.</Mono></View>;
  const max = Math.max(1, ...Object.values(d.stars));
  return (
    <View style={s.block} testID="ratings-card">
      <View style={s.row}>
        <Display style={s.big} testID="ratings-avg">{d.avg?.toFixed(1)}★</Display>
        <Mono style={s.meta}>{d.count} rating{d.count === 1 ? "" : "s"}</Mono>
      </View>
      {["5", "4", "3", "2", "1"].map((k) => (
        <View key={k} style={s.row}>
          <Mono style={[s.meta, { width: 22 }]}>{k}★</Mono>
          <View style={s.track}><View style={{ width: `${(d.stars[k] / max) * 100}%`, height: "100%", backgroundColor: Number(k) >= 4 ? colors.brandForest : colors.brandPrimary }} /></View>
          <Mono style={[s.meta, { width: 28, textAlign: "right" }]}>{d.stars[k]}</Mono>
        </View>
      ))}
      {d.drivers.length ? (
        <>
          <Mono style={s.label}>BY DRIVER</Mono>
          {d.drivers.map((x) => (
            <View key={x.driver_id} style={s.row} testID={`ratings-driver-${x.driver_id}`}>
              <Mono style={[s.title, { flex: 1 }]} numberOfLines={1}>{x.name}</Mono>
              <Mono style={[s.title, { color: x.avg >= 4 ? colors.brandForest : x.avg <= 2 ? colors.brandPrimary : colors.onSurface }]}>{x.avg.toFixed(1)}★</Mono>
              <Mono style={[s.meta, { width: 64, textAlign: "right" }]}>{x.count} rated</Mono>
            </View>
          ))}
        </>
      ) : null}
      <Mono style={s.label}>LATEST</Mono>
      {(all ? d.recent : d.recent.slice(0, 5)).map((r) => (
        <View key={r.stop_id} style={s.item} testID={`rating-row-${r.tracking_code}`}>
          <View style={s.row}>
            <Mono style={[s.stars, { color: r.stars <= 2 ? colors.brandPrimary : colors.onSurface }]}>{"★".repeat(r.stars)}{"☆".repeat(5 - r.stars)}</Mono>
            <Mono style={[s.meta, { flex: 1 }]} numberOfLines={1}>{r.tracking_code} · {r.customer_name}{r.driver ? ` · ${r.driver}` : ""}{!merchantId && r.shop ? ` · ${r.shop}` : ""}</Mono>
          </View>
          {r.tags.length ? <Mono style={s.meta}>{r.tags.join(" · ").toLowerCase()}</Mono> : null}
          {r.comment ? <Mono style={s.comment}>&ldquo;{r.comment}&rdquo;</Mono> : null}
          {r.rated_at ? <Mono style={s.meta}>{dayLabel(new Date(r.rated_at).toLocaleDateString("en-CA", { timeZone: "America/Toronto" }))} · {fmtDepotTime(r.rated_at)}</Mono> : null}
        </View>
      ))}
      {d.recent.length > 5 ? (
        <Pressable testID="ratings-toggle-all" onPress={() => setAll(!all)} style={s.more}>
          <Mono style={s.title}>{all ? "SHOW FEWER" : `SHOW ALL ${d.recent.length} RECENT`}</Mono>
        </Pressable>
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  big: { fontSize: 32 },
  track: { flex: 1, height: 10, borderWidth: 2, borderColor: c.border, overflow: "hidden" },
  title: { fontSize: 13, fontWeight: "700" },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 1, marginTop: spacing.sm },
  meta: { fontSize: 11, color: c.muted },
  stars: { fontSize: 13, fontWeight: "700" },
  item: { gap: 2, paddingVertical: spacing.sm, borderTopWidth: 1, borderColor: c.border },
  more: { minHeight: 44, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.border },
  comment: { fontSize: 12, color: c.onSurface, fontStyle: "italic", paddingLeft: spacing.sm, borderLeftWidth: 2, borderColor: c.border },
}));
