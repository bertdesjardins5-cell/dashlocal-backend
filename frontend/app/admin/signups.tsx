import React, { useState } from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, ErrorBlock, Header, Loading, Mono, Pill, useToast } from "@/src/ui";
import { makeStyles, spacing } from "@/src/theme";

type Signup = { id: string; store_name: string; contact_name: string; email: string; phone: string | null; address: string | null; message: string | null; status: "pending" | "approved" | "declined"; created_at: string; email_status?: string; business_type?: string };
const TYPE_LABEL: Record<string, string> = { store: "STORE", law_office: "LAW OFFICE", flower_shop: "FLOWER SHOP", pharmacy: "PHARMACY", other: "OTHER" };

// Admin: approve (creates the store + emails a set-password link) or decline shop sign-up requests.
export default function SignupsScreen() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const toast = useToast();
  const q = useQuery({ queryKey: ["admin-signups"], queryFn: () => api<Signup[]>("/admin/signup-requests") });
  const [busy, setBusy] = useState<string | null>(null);
  const act = async (r: Signup, action: "approve" | "decline") => {
    setBusy(r.id + action);
    try {
      const res = await api<{ email_status?: string }>(`/admin/signup-requests/${r.id}/${action}`, { method: "POST" });
      toast.show(action === "decline" ? "REQUEST DECLINED" : res.email_status === "sent" ? "STORE CREATED · SET-PASSWORD EMAIL SENT" : "STORE CREATED · EMAIL FAILED", action === "approve" && res.email_status !== "sent" ? "red" : "green");
      q.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const pending = q.data?.filter((r) => r.status === "pending") ?? [];
  const done = q.data?.filter((r) => r.status !== "pending") ?? [];

  const row = (r: Signup) => (
    <View key={r.id} style={s.item} testID={`signup-request-${r.id}`}>
      <View style={s.top}>
        <Mono style={s.name}>{r.store_name}</Mono>
        <Pill label={r.status} tone={r.status === "pending" ? "yellow" : r.status === "approved" ? "green" : "light"} />
      </View>
      <Mono style={s.meta}>{TYPE_LABEL[r.business_type ?? "store"] ?? "STORE"} · {r.business_type === "pharmacy" ? "ACCEPTED PIPEDA/PHIPA TERMS" : "ACCEPTED TERMS OF SERVICE"}</Mono>
      <Mono style={s.meta}>{r.contact_name} · {r.email}{r.phone ? ` · ${r.phone}` : ""}</Mono>
      {r.address ? <Mono style={s.meta}>{r.address}</Mono> : null}
      {r.message ? <Mono style={s.msg}>“{r.message}”</Mono> : null}
      <Mono style={s.meta}>{new Date(r.created_at).toLocaleString()}{r.email_status && r.email_status !== "sent" ? ` · EMAIL ${r.email_status.toUpperCase()}` : ""}</Mono>
      {r.status === "pending" ? (
        <View style={s.actions}>
          <Btn testID={`signup-decline-${r.id}`} label="DECLINE" variant="outline" loading={busy === r.id + "decline"} disabled={!!busy} onPress={() => act(r, "decline")} style={{ flex: 1 }} />
          <Btn testID={`signup-approve-${r.id}`} label="APPROVE" icon="checkmark" loading={busy === r.id + "approve"} disabled={!!busy} onPress={() => act(r, "approve")} style={{ flex: 2 }} />
        </View>
      ) : null}
    </View>
  );

  return (
    <View style={s.root} testID="admin-signups-screen">
      <Header title="SIGN-UP REQUESTS" subtitle="New shops asking to join" />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : (
        <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); }} />}>
          <Mono style={s.section}>WAITING · {pending.length}</Mono>
          {pending.length ? pending.map(row) : <Mono style={[s.meta, { padding: spacing.lg }]} testID="signups-empty">No requests waiting.</Mono>}
          {done.length ? <Mono style={s.section}>DECIDED</Mono> : null}
          {done.map(row)}
        </ScrollView>
      )}
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  item: { padding: spacing.lg, gap: spacing.xs, borderBottomWidth: 2, borderColor: c.border },
  top: { flexDirection: "row", alignItems: "center", gap: spacing.sm, justifyContent: "space-between" },
  name: { fontSize: 16, fontWeight: "700", flex: 1 },
  meta: { fontSize: 12, color: c.muted },
  msg: { fontSize: 13 },
  actions: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm },
}));
