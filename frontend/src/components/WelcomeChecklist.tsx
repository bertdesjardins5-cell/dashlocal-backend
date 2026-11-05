import React, { useEffect, useState } from "react";
import { Pressable, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Checklist = { show: boolean; steps: { key: string; label: string; done: boolean }[]; done: number; total: number };

// First steps for a newly approved shop. Steps tick themselves from real activity; the shop can hide it.
export default function WelcomeChecklist({ merchantId, refreshKey, notify }: { merchantId: string; refreshKey: unknown; notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["checklist", merchantId], queryFn: () => api<Checklist>(`/merchants/${merchantId}/checklist`) });
  const refetch = q.refetch;
  useEffect(() => {
    refetch(); // portal data changed (upload, delivery): re-check steps without blanking the card
  }, [refreshKey, refetch]);
  const [hiding, setHiding] = useState(false);
  if (!q.data?.show) return null;
  const all = q.data.done === q.data.total;
  const hide = async () => {
    setHiding(true);
    try {
      await api(`/merchants/${merchantId}/checklist/dismiss`, { method: "POST" });
      q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setHiding(false);
    }
  };
  return (
    <View style={s.box} testID="welcome-checklist">
      <View style={s.head}>
        <View style={{ flex: 1, gap: 2 }}>
          <Mono style={s.title}>{all ? "YOU'RE ALL SET" : "WELCOME TO LOCALDASH"}</Mono>
          <Mono style={s.meta} testID="welcome-checklist-progress">{q.data.done} OF {q.data.total} FIRST STEPS DONE</Mono>
        </View>
        <Pressable testID="welcome-checklist-hide" onPress={hide} disabled={hiding} style={s.hide} accessibilityLabel="Hide checklist">
          <Mono style={s.hideText}>{all ? "DONE" : "HIDE"}</Mono>
        </Pressable>
      </View>
      <View style={s.bar}><View style={[s.fill, { width: `${(q.data.done / q.data.total) * 100}%` }]} /></View>
      {q.data.steps.map((st) => (
        <View key={st.key} style={s.step} testID={`welcome-step-${st.key}`}>
          <Ionicons name={st.done ? "checkmark-circle" : "ellipse-outline"} size={22} color={st.done ? colors.success : colors.muted} />
          <Mono style={[s.stepText, st.done && { color: colors.muted, textDecorationLine: "line-through" }]}>{st.label}</Mono>
        </View>
      ))}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { margin: spacing.lg, padding: spacing.md, gap: spacing.sm, borderWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  head: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  title: { fontSize: 14, fontWeight: "700", letterSpacing: 1 },
  meta: { fontSize: 11, color: c.muted },
  hide: { minHeight: 44, minWidth: 64, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, alignItems: "center", justifyContent: "center", backgroundColor: c.surface },
  hideText: { fontSize: 12, fontWeight: "700" },
  bar: { height: 6, backgroundColor: c.surface, borderWidth: 1, borderColor: c.border },
  fill: { height: "100%", backgroundColor: c.success },
  step: { flexDirection: "row", alignItems: "center", gap: spacing.sm, minHeight: 36 },
  stepText: { flex: 1, fontSize: 13 },
}));
