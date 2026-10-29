import React, { useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { ErrorBlock, Header, Loading, Mono, Pill } from "@/src/ui";
import { makeStyles, spacing } from "@/src/theme";

type Crash = { id: string; message: string; stack: string; fatal: boolean; where: string; platform: string | null; os_version: string | null;
  model: string | null; app_version: string | null; received_at: string; delayed: boolean };
const fmt = (iso: string) => new Date(iso).toLocaleString("en-CA", { timeZone: "America/Toronto", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

// Admin: errors the app reported from phones (newest first). Tap one to see the technical details.
export default function CrashReports() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["crash-reports"], queryFn: () => api<Crash[]>("/admin/crash-reports") });
  return (
    <View style={s.root} testID="admin-crashes-screen">
      <Header title="CRASH REPORTS" subtitle="ERRORS SENT FROM PHONES" />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : (
        <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={q.refetch} />}>
          {!q.data?.length ? <Mono style={[s.meta, { padding: spacing.lg }]}>No crashes reported. Reports appear here when the app hits an error on a phone.</Mono> : null}
          {q.data?.map((c) => (
            <Pressable key={c.id} testID={`crash-${c.id}`} onPress={() => setOpen(open === c.id ? null : c.id)} style={s.row}>
              <View style={{ flexDirection: "row", gap: spacing.sm, alignItems: "center" }}>
                <Mono style={[s.title, { flex: 1 }]} numberOfLines={open === c.id ? undefined : 2}>{c.message}</Mono>
                <Pill label={c.fatal ? "crash" : "error"} tone={c.fatal ? "red" : "yellow"} />
              </View>
              <Mono style={s.meta}>{fmt(c.received_at)} · {c.model ?? c.platform} · {c.platform === "android" ? "Android API" : c.platform} {c.os_version} · v{c.app_version}</Mono>
              {open === c.id ? <Mono style={s.stack} selectable>{c.stack || "No stack trace"}</Mono> : null}
            </Pressable>
          ))}
        </ScrollView>
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  row: { padding: spacing.lg, gap: spacing.xs, borderBottomWidth: 1, borderColor: c.border },
  title: { fontSize: 13, fontWeight: "700" },
  meta: { fontSize: 12, color: c.muted },
  stack: { fontSize: 10, color: c.onSurface, backgroundColor: c.surfaceSecondary, padding: spacing.sm },
}));
