import React, { useState } from "react";
import { Linking, Platform, Pressable, RefreshControl, ScrollView, View } from "react-native";
import { Redirect, useLocalSearchParams, useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import * as DocumentPicker from "expo-document-picker";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { clearSession, getToken, useSession } from "@/src/session";
import ReportDownload from "@/src/components/ReportDownload";
import WeeklyEmailCard from "@/src/components/WeeklyEmailCard";
import ProblemAlertsCard from "@/src/components/ProblemAlertsCard";
import BillCard, { money } from "@/src/components/BillCard";
import { api, API, dayLabel, Stop, windowLabel } from "@/src/api";
import { uploadForm } from "@/src/utils/upload";
import FixRowSheet, { ImportRow } from "@/src/components/FixRowSheet";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, statusTone, useToast } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Job = { id: string; filename: string; total: number; processed: number; created: number; flagged: number; status: "running" | "done"; created_at: string };
type Detail = {
  id: string; name: string; orders: Stop[]; flagged: ImportRow[]; jobs: Job[];
  counts: { orders: number; pending: number; delivered: number; failed: number; flagged: number };
};

export default function MerchantHome() {
  const { merchantId } = useLocalSearchParams<{ merchantId: string }>();
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const { ready, user } = useSession();
  const allowed = !!user && (user.role === "admin" || user.merchant_id === merchantId);
  const [uploading, setUploading] = useState(false);
  const [fix, setFix] = useState<ImportRow | null>(null);
  const [busyOrder, setBusyOrder] = useState<string | null>(null);

  const togglePriority = async (o: Stop) => {
    setBusyOrder(o.id);
    try {
      const r = await api<Stop>(`/merchants/${merchantId}/orders/${o.id}/priority`, { method: "PUT", body: { priority: !o.priority } });
      toast.show(`${o.tracking_code} ${r.priority ? "IS PRIORITY" : "IS STANDARD"} · NOW ${r.fee ? money(r.fee.total) : ""}`, "green");
      q.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusyOrder(null);
    }
  };

  const q = useQuery({
    queryKey: ["merchant", merchantId],
    queryFn: () => api<Detail>(`/merchants/${merchantId}`),
    enabled: allowed,
    refetchInterval: (query) => (query.state.data?.jobs.some((j) => j.status === "running") ? 2000 : 20000),
  });
  const d = q.data;
  const running = d?.jobs.find((j) => j.status === "running");
  const last = d?.jobs[0];

  const upload = async () => {
    try {
      const picked = await DocumentPicker.getDocumentAsync({
        type: ["text/csv", "text/comma-separated-values", "application/vnd.ms-excel", "text/plain"],
        copyToCacheDirectory: true,
      });
      if (picked.canceled) return;
      const a = picked.assets[0];
      if (!a.name.toLowerCase().endsWith(".csv")) return toast.show("PICK A .CSV FILE", "red");
      setUploading(true);
      const res = await uploadForm(`${API}/merchants/${merchantId}/import`, {
        files: { file: { uri: a.uri, name: a.name, mimeType: "text/csv" } },
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      const j = await res.json();
      if (!res.ok) throw new Error(typeof j.detail === "string" ? j.detail : `Upload failed (${res.status})`);
      toast.show(`${j.total} ROWS RECEIVED · LOOKING UP ADDRESSES`, "green");
      q.refetch();
    } catch (e: any) {
      toast.show(e.message ?? "Upload failed", "red");
    } finally {
      setUploading(false);
    }
  };

  const template = () => {
    const url = `${API}/merchant-template.csv`;
    if (Platform.OS === "web") window.open(url, "_blank");
    else Linking.openURL(url);
  };

  if (!ready) return <View style={s.root}><Header title="MERCHANT" /><Loading /></View>;
  if (!allowed) return <Redirect href="/merchant" />;

  const signOut = async () => {
    await clearSession();
    router.replace("/merchant");
  };

  return (
    <View style={s.root} testID="merchant-screen">
      <Header title={d?.name ?? "MERCHANT"} subtitle={user?.role === "admin" ? "Admin view · orders · CSV import" : `${user?.email} · orders · CSV import`} right={
        <Pressable testID="sign-out-button" onPress={signOut} style={s.iconBtn}><Ionicons name="log-out" size={22} color={colors.onSurfaceInverse} /></Pressable>
      } />
      {q.isLoading ? <Loading /> : q.error || !d ? <ErrorBlock message={(q.error as Error)?.message ?? "Not found"} onRetry={q.refetch} /> : (
        <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} refreshControl={<RefreshControl refreshing={false} onRefresh={() => q.refetch()} />}>
          <View style={s.totals}>
            {[
              { k: "ORDERS", v: d.counts.orders, id: "merchant-count-orders" },
              { k: "ON THE WAY", v: d.counts.pending, id: "merchant-count-pending" },
              { k: "DELIVERED", v: d.counts.delivered, id: "merchant-count-delivered" },
              { k: "TO FIX", v: d.counts.flagged, id: "merchant-count-flagged" },
            ].map((c, i) => (
              <View key={c.k} style={[s.cell, i > 0 && { borderLeftWidth: 2, borderColor: colors.border }]} testID={c.id}>
                <Display style={[s.cellVal, c.k === "TO FIX" && c.v > 0 && { color: colors.brandPrimary }]}>{c.v}</Display>
                <Mono style={s.label}>{c.k}</Mono>
              </View>
            ))}
          </View>

          <Mono style={s.section}>IMPORT ORDERS FROM CSV</Mono>
          <View style={s.block}>
            <Mono style={s.meta}>Columns: customer_name, phone, address (required) · notes, window_date, window_start, window_end, priority (yes = priority delivery, surcharge applies) (optional). Up to 200 rows per file. Addresses are looked up on the map automatically.</Mono>
            <View style={{ flexDirection: "row", gap: spacing.sm }}>
              <Btn testID="merchant-template-button" label="TEMPLATE" icon="download" variant="outline" onPress={template} style={{ flex: 1 }} />
              <Btn testID="merchant-upload-button" label="UPLOAD CSV" icon="cloud-upload" loading={uploading} disabled={!!running} onPress={upload} style={{ flex: 2 }} />
            </View>
            {last ? (
              <View style={s.job} testID="merchant-import-progress">
                <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }}>
                  <Display style={{ fontSize: 15, flex: 1 }} numberOfLines={1}>{last.filename}</Display>
                  <Pill label={last.status === "running" ? "importing" : "done"} tone={last.status === "running" ? "dark" : "green"} />
                </View>
                <View style={s.track}><View style={[s.bar, { width: `${(last.processed / Math.max(1, last.total)) * 100}%` }]} /></View>
                <Mono style={s.meta} testID="merchant-import-summary">
                  {last.processed}/{last.total} CHECKED · {last.created} ORDERS CREATED · {last.flagged} TO FIX
                </Mono>
              </View>
            ) : null}
            <Mono style={s.attrib}>Address lookup © OpenStreetMap contributors</Mono>
          </View>

          {d.flagged.length ? (
            <>
              <Mono style={[s.section, { color: colors.brandPrimary }]}>ROWS TO FIX · {d.flagged.length}</Mono>
              {d.flagged.map((r) => (
                <Pressable key={r.id} testID={`flagged-row-${r.line}`} onPress={() => setFix(r)} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }]}>
                  <Display style={s.line}>{r.line}</Display>
                  <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                    <Display style={s.name} numberOfLines={1}>{r.data.customer_name || "NO NAME"}</Display>
                    <Mono style={s.meta} numberOfLines={1}>{r.data.address || "NO ADDRESS"}</Mono>
                    <Mono style={s.errText} numberOfLines={2}>{r.error}</Mono>
                  </View>
                  <Mono style={s.fix}>FIX</Mono>
                </Pressable>
              ))}
            </>
          ) : null}

          <Mono style={s.section}>BILLING · CAD</Mono>
          <BillCard merchantId={merchantId} notify={toast.show} />

          <Mono style={s.section}>DELIVERY REPORT</Mono>
          <ReportDownload merchantId={merchantId} notify={toast.show} />
          <WeeklyEmailCard merchantId={merchantId} notify={toast.show} />
          <Mono style={s.section}>PROBLEM ALERTS</Mono>
          <ProblemAlertsCard merchantId={merchantId} notify={toast.show} />

          <Mono style={s.section}>ORDERS</Mono>
          {d.orders.length === 0 ? <Mono style={s.empty} testID="merchant-orders-empty">NO ORDERS YET · UPLOAD A CSV TO GET STARTED</Mono> : null}
          {d.orders.map((o) => (
            <Pressable key={o.id} testID={`merchant-order-${o.tracking_code}`} onPress={() => router.push(`/customer/${o.tracking_code}`)} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }]}>
              <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                <Display style={s.name} numberOfLines={1}>{o.customer_name}</Display>
                <Mono style={s.meta} numberOfLines={1}>{o.tracking_code} · {o.address}</Mono>
                <View style={{ flexDirection: "row", gap: spacing.xs, flexWrap: "wrap" }}>
                  <Pill label={o.route_id ? o.status : "awaiting dispatch"} tone={o.route_id ? statusTone(o.status) : "light"} />
                  <Pill label={o.zone} tone="light" />
                  {windowLabel(o) ? <Pill label={`⏱ ${windowLabel(o)}`} tone="dark" /> : o.window_date ? <Pill label={dayLabel(o.window_date)} tone="dark" /> : null}
                  {o.priority ? <Pill label="PRIORITY" tone="red" /> : null}
                  {o.fee ? <Pill label={money(o.fee.total)} tone="light" /> : null}
                </View>
                {!o.route_id && o.status === "pending" ? (
                  <Pressable testID={`order-priority-${o.tracking_code}`} onPress={() => togglePriority(o)} disabled={busyOrder === o.id} style={s.priBtn}>
                    <Mono style={s.priText}>{busyOrder === o.id ? "…" : o.priority ? "REMOVE PRIORITY" : "MAKE PRIORITY"}</Mono>
                  </Pressable>
                ) : null}
              </View>
              <Ionicons name="chevron-forward" size={22} color={colors.onSurface} />
            </Pressable>
          ))}
        </ScrollView>
      )}
      {fix ? <FixRowSheet row={fix} onClose={() => setFix(null)} onDone={() => { setFix(null); q.refetch(); }} notify={toast.show} /> : null}
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  iconBtn: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.onSurfaceInverse },
  totals: { flexDirection: "row", borderBottomWidth: 2, borderColor: c.border },
  cell: { flex: 1, padding: spacing.md, alignItems: "center", gap: 2 },
  cellVal: { fontSize: 24 },
  label: { fontSize: 9, fontWeight: "700", letterSpacing: 1, color: c.muted },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  job: { borderWidth: 2, borderColor: c.border, padding: spacing.md, gap: spacing.sm },
  track: { height: 8, backgroundColor: c.surfaceSecondary },
  bar: { height: 8, backgroundColor: c.brandPrimary },
  attrib: { fontSize: 10, color: c.muted },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.lg, borderBottomWidth: 2, borderColor: c.border, minHeight: 72 },
  line: { fontSize: 20, width: 32, color: c.brandPrimary },
  name: { fontSize: 16 },
  meta: { fontSize: 11, color: c.muted },
  errText: { fontSize: 11, fontWeight: "700", color: c.brandPrimary },
  fix: { fontSize: 12, fontWeight: "700", borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, minHeight: 44, textAlignVertical: "center", lineHeight: 26 },
  empty: { padding: spacing.lg, fontSize: 13, color: c.muted },
  priBtn: { alignSelf: "flex-start", minHeight: 44, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center", marginTop: spacing.xs },
  priText: { fontSize: 11, fontWeight: "700" },
}));
