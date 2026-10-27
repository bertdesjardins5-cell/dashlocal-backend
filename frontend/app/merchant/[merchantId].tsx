import React, { useState } from "react";
import { Linking, Platform, Pressable, RefreshControl, ScrollView, View } from "react-native";
import { Redirect, useLocalSearchParams, useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import * as DocumentPicker from "expo-document-picker";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import Checkbox from "expo-checkbox";
import { clearSession, getToken, useSession } from "@/src/session";
import ReportDownload from "@/src/components/ReportDownload";
import WeeklyEmailCard from "@/src/components/WeeklyEmailCard";
import SavedNotesCard from "@/src/components/SavedNotesCard";
import WelcomeChecklist from "@/src/components/WelcomeChecklist";
import ProblemAlertsCard from "@/src/components/ProblemAlertsCard";
import { money } from "@/src/components/BillCard";
import InvoicesCard from "@/src/components/InvoicesCard";
import ReceiptArchive from "@/src/components/ReceiptArchive";
import MatterTag from "@/src/components/MatterTag";
import { queryClient } from "@/src/query-client";
import BatchRateTracker from "@/src/components/BatchRateTracker";
import PharmacySummaryCard from "@/src/components/PharmacySummaryCard";
import { api, API, dayLabel, Stop, windowLabel } from "@/src/api";
import { uploadForm } from "@/src/utils/upload";
import FixRowSheet, { ImportRow } from "@/src/components/FixRowSheet";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, statusTone, useToast } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Job = { id: string; filename: string; total: number; processed: number; created: number; flagged: number; status: "running" | "done"; created_at: string };
type Run = { key: "afternoon" | "evening"; label: string; today: boolean; date: string; cutoff_label: string; pickup_label: string; window_label: string; drops_today: number;
  cutoff_at: string; pickup: { picked_up_at: string; parcel_count: number; expected: number; on_time: boolean } | null };
type Detail = {
  id: string; name: string; orders: Stop[]; flagged: ImportRow[]; jobs: Job[]; pharmacy_partner?: boolean; runs?: Run[]; business_type?: string;
  pharmacy_terms?: { batch_rate: number; standard_rate: number; min_drops: number };
  counts: { orders: number; pending: number; delivered: number; failed: number; flagged: number };
};

export default function MerchantHome() {
  const { merchantId, paid } = useLocalSearchParams<{ merchantId: string; paid?: string }>();
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
  const [run, setRun] = useState<Run["key"] | null>(null);
  const [consent, setConsent] = useState(false);

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
    const pharmacy = !!d?.runs;
    const chosen = d?.runs?.find((r) => r.key === run);
    if (pharmacy && !chosen) return toast.show("PICK A PICKUP RUN FIRST", "red");
    if (!consent) return toast.show("TICK THE PRIVACY CONSENT BOX FIRST", "red");
    try {
      const picked = await DocumentPicker.getDocumentAsync({
        type: ["text/csv", "text/comma-separated-values", "application/vnd.ms-excel", "text/plain"],
        copyToCacheDirectory: true,
      });
      if (picked.canceled) return;
      const a = picked.assets[0];
      if (!a.name.toLowerCase().endsWith(".csv")) return toast.show("PICK A .CSV FILE", "red");
      setUploading(true);
      const res = await uploadForm(`${API}/merchants/${merchantId}/import?consent=true${pharmacy ? `&run=${run}` : ""}`, {
        files: { file: { uri: a.uri, name: a.name, mimeType: "text/csv" } },
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      const j = await res.json();
      if (!res.ok) throw new Error(typeof j.detail === "string" ? j.detail : `Upload failed (${res.status})`);
      toast.show(chosen ? `${j.total} ROWS RECEIVED FOR ${chosen.label.toUpperCase()} ${chosen.today ? "TODAY" : "TOMORROW"}` : `${j.total} ROWS RECEIVED · LOOKING UP ADDRESSES`, "green");
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

          <WelcomeChecklist merchantId={merchantId} refreshKey={q.dataUpdatedAt} notify={toast.show} />

          <Mono style={s.section}>IMPORT ORDERS FROM CSV</Mono>
          <View style={s.block}>
            <Mono style={s.meta}>Columns: customer_name, phone, address (required) · notes, window_date, window_start, window_end, priority (yes = priority delivery, surcharge applies), delivery_note or gate_code (sent to the driver and saved for that customer&apos;s next orders){d.business_type === "law_office" ? ", matter_ref or file_number (printed on the signed receipt)" : ""} (optional). Up to 200 rows per file. Addresses are looked up on the map automatically.{d.runs ? " Pharmacy runs set the delivery day and window, so window columns are ignored." : ""}</Mono>
            <View style={s.privacyWarn} testID="health-info-warning">
              <Ionicons name="shield-checkmark" size={18} color={colors.onWarning} />
              <Mono style={[s.meta, { flex: 1, color: colors.onWarning }]}>
                NO HEALTH INFO: notes are for delivery instructions only (gate code, &quot;leave at side door&quot;). Never include prescription names, medications, dosages or diagnoses.
              </Mono>
            </View>
            <Pressable testID="order-consent-checkbox" onPress={() => setConsent(!consent)} style={s.consentRow} accessibilityRole="checkbox" accessibilityState={{ checked: consent }}>
              <Checkbox value={consent} onValueChange={setConsent} color={consent ? colors.brandForest : undefined} />
              <Mono style={[s.meta, { flex: 1, color: colors.onSurface }]}>
                I consent to DashLocal and its designated independent drivers collecting and processing my name, address, and contact details solely for the purpose of same-day parcel/prescription fulfillment. I agree to the{" "}
                <Mono style={{ color: colors.brandForest, textDecorationLine: "underline", fontSize: 12 }} onPress={() => router.push("/privacy")} testID="order-privacy-link">Privacy Policy</Mono>.
              </Mono>
            </Pressable>
            {d.runs ? (
              <View style={{ gap: spacing.sm }} testID="pharmacy-runs">
                <Mono style={s.label}>PICKUP RUN (EASTERN TIME)</Mono>
                {d.runs.map((r) => {
                  const sel = run === r.key;
                  const need = (d.pharmacy_terms?.min_drops ?? 10) - r.drops_today;
                  return (
                    <Pressable key={r.key} testID={`run-${r.key}`} onPress={() => setRun(r.key)}
                      style={[s.run, sel && { backgroundColor: colors.surfaceInverse }]}>
                      <View style={{ flex: 1, gap: 2 }}>
                        <Display style={[{ fontSize: 15 }, sel && { color: colors.onSurfaceInverse }]}>{r.label.toUpperCase()} · {r.today ? "TODAY" : `TOMORROW ${dayLabel(r.date).toUpperCase()}`}</Display>
                        <Mono style={[s.meta, sel && { color: colors.onSurfaceInverse }]}>
                          Order cut-off {r.cutoff_label} · Pickup {r.pickup_label} · Patients {r.window_label}
                        </Mono>
                        <Mono style={[s.meta, sel && { color: colors.onSurfaceInverse }]} testID={`run-${r.key}-drops`}>
                          {r.drops_today} drop{r.drops_today === 1 ? "" : "s"} on this pickup · {need > 0 ? `${need} more for the $${d.pharmacy_terms?.batch_rate.toFixed(2)} batch rate` : `batch rate $${d.pharmacy_terms?.batch_rate.toFixed(2)}/drop`}
                        </Mono>
                        <BatchRateTracker run={r} minDrops={d.pharmacy_terms?.min_drops ?? 10} inverse={sel} />
                      </View>
                      {sel ? <Ionicons name="checkmark-circle" size={22} color={colors.onSurfaceInverse} /> : null}
                    </Pressable>
                  );
                })}
              </View>
            ) : null}
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

          <Mono style={s.section}>INVOICES · CAD + HST</Mono>
          {d.runs ? <PharmacySummaryCard merchantId={merchantId} notify={toast.show} /> : null}
          <InvoicesCard merchantId={merchantId} notify={toast.show} paidSession={paid} />
          <ReceiptArchive merchantId={merchantId} notify={toast.show} />

          <Mono style={s.section}>DELIVERY REPORT</Mono>
          <ReportDownload merchantId={merchantId} notify={toast.show} />
          <WeeklyEmailCard merchantId={merchantId} notify={toast.show} />
          <SavedNotesCard merchantId={merchantId} notify={toast.show} />
          <Mono style={s.section}>PROBLEM ALERTS</Mono>
          <ProblemAlertsCard merchantId={merchantId} notify={toast.show} />

          <Mono style={s.section}>ORDERS</Mono>
          {d.orders.length === 0 ? <Mono style={s.empty} testID="merchant-orders-empty">NO ORDERS YET · UPLOAD A CSV TO GET STARTED</Mono> : null}
          {d.orders.map((o) => (
            <View key={o.id} style={s.orderWrap}>
              <Pressable testID={`merchant-order-${o.tracking_code}`} onPress={() => router.push(`/customer/${o.tracking_code}`)} style={({ pressed }) => [s.orderRow, pressed && { backgroundColor: colors.surfaceTertiary }]}>
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
                </View>
                <Ionicons name="chevron-forward" size={22} color={colors.onSurface} />
              </Pressable>
              {/* actions sit outside the row's tap area so tapping them never opens the tracking page */}
              {!o.route_id && o.status === "pending" ? (
                <Pressable testID={`order-priority-${o.tracking_code}`} onPress={() => togglePriority(o)} disabled={busyOrder === o.id} style={[s.priBtn, s.orderAction]}>
                  <Mono style={s.priText}>{busyOrder === o.id ? "…" : o.priority ? "REMOVE PRIORITY" : "MAKE PRIORITY"}</Mono>
                </Pressable>
              ) : null}
              {d.business_type === "law_office" ? (
                <View style={{ marginHorizontal: spacing.lg }}>
                  <MatterTag merchantId={merchantId} stopId={o.id} value={o.matter_ref} notify={toast.show}
                    onSaved={() => { q.refetch(); queryClient.invalidateQueries({ queryKey: ["receipts", merchantId] }); }} />
                </View>
              ) : null}
            </View>
          ))}
        </ScrollView>
      )}
      {fix ? <FixRowSheet row={fix} lawOffice={q.data?.business_type === "law_office"} onClose={() => setFix(null)} onDone={() => { setFix(null); q.refetch(); }} notify={toast.show} /> : null}
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
  orderWrap: { borderBottomWidth: 2, borderColor: c.border, paddingBottom: spacing.sm },
  orderRow: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.sm, minHeight: 72 },
  orderAction: { marginHorizontal: spacing.lg, alignSelf: "flex-start" },
  line: { fontSize: 20, width: 32, color: c.brandPrimary },
  name: { fontSize: 16 },
  meta: { fontSize: 11, color: c.muted },
  errText: { fontSize: 11, fontWeight: "700", color: c.brandPrimary },
  fix: { fontSize: 12, fontWeight: "700", borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, minHeight: 44, textAlignVertical: "center", lineHeight: 26 },
  empty: { padding: spacing.lg, fontSize: 13, color: c.muted },
  priBtn: { alignSelf: "flex-start", minHeight: 44, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center", marginTop: spacing.xs },
  priText: { fontSize: 11, fontWeight: "700" },
  run: { flexDirection: "row", alignItems: "center", gap: spacing.sm, borderWidth: 2, borderColor: c.border, padding: spacing.md, minHeight: 56 },
  privacyWarn: { flexDirection: "row", gap: spacing.sm, alignItems: "flex-start", backgroundColor: c.warning, padding: spacing.md },
  consentRow: { flexDirection: "row", gap: spacing.sm, alignItems: "flex-start", paddingVertical: spacing.sm, minHeight: 44 },
}));
