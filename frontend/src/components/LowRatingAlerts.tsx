import React, { useEffect, useState } from "react";
import { Modal, Pressable, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, dayLabel, DEPOT_TZ, fmtDepotTime, Stop, WS_BASE } from "@/src/api";
import { Btn, Display, Mono, Pill } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Alert = { stop: Stop & { follow_up_status: string | null; follow_up_note: string | null; rated_at: string | null }; driver: { id: string; name: string } | null; route_name: string | null };

// Admin dashboard block: open 1-2 star deliveries, live via /ws/admin, with a follow-up sheet.
export default function LowRatingAlerts({ notify }: { notify: (msg: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [sel, setSel] = useState<Alert | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const q = useQuery({ queryKey: ["low-ratings"], queryFn: () => api<Alert[]>("/alerts/low-ratings?status=open"), refetchInterval: 20000 });

  useEffect(() => {
    let closed = false;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout>;
    const connect = () => {
      ws = new WebSocket(`${WS_BASE}/admin`);
      ws.onmessage = (e) => {
        try {
          const m = JSON.parse(e.data);
          if (m.type === "low_rating") {
            notify(`NEW ${m.alert.stop.rating}★ RATING · ${m.alert.stop.tracking_code}`, "red");
            q.refetch();
          }
        } catch {}
      };
      ws.onclose = () => { if (!closed) retry = setTimeout(connect, 4000); };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => { closed = true; clearTimeout(retry); ws?.close(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const open = (a: Alert) => { setNote(a.stop.follow_up_note ?? ""); setSel(a); };

  const save = async (resolved: boolean) => {
    if (!sel) return;
    setSaving(true);
    try {
      await api(`/stops/${sel.stop.id}/follow-up`, { method: "POST", body: { note, resolved } });
      notify(resolved ? `${sel.stop.tracking_code} RESOLVED` : "NOTE SAVED", "green");
      setSel(null);
      q.refetch();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setSaving(false);
    }
  };

  const items = q.data ?? [];
  return (
    <View testID="low-rating-alerts">
      <View style={[s.head, items.length > 0 && { backgroundColor: colors.brandPrimary }]}>
        <Ionicons name={items.length ? "alert-circle" : "checkmark-circle"} size={18} color={items.length ? colors.onBrandPrimary : colors.onSurface} />
        <Mono style={[s.headText, items.length > 0 && { color: colors.onBrandPrimary }]} testID="low-rating-count">
          {items.length ? `${items.length} LOW RATING${items.length > 1 ? "S" : ""} NEED FOLLOW-UP` : "NO LOW RATINGS TO FOLLOW UP"}
        </Mono>
      </View>
      {(showAll ? items : items.slice(0, 5)).map((a) => (
        <Pressable key={a.stop.id} testID={`low-rating-${a.stop.tracking_code}`} onPress={() => open(a)} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }]}>
          <Display style={s.stars}>{a.stop.rating}★</Display>
          <View style={{ flex: 1, gap: 2 }}>
            <Display style={{ fontSize: 15 }} numberOfLines={1}>{a.stop.customer_name} · {a.stop.tracking_code}</Display>
            <Mono style={s.comment} numberOfLines={2}>“{a.stop.rating_comment || "No comment"}”</Mono>
            <Mono style={s.meta}>{a.driver?.name ?? "—"} · {a.route_name ?? "—"}{a.stop.rated_at ? ` · ${dayLabel(new Date(a.stop.rated_at).toLocaleDateString("en-CA", { timeZone: DEPOT_TZ }))} ${fmtDepotTime(a.stop.rated_at)}` : ""}</Mono>
          </View>
          <Ionicons name="chevron-forward" size={20} color={colors.onSurface} />
        </Pressable>
      ))}
      {items.length > 5 ? (
        <Pressable testID="low-rating-toggle-all" onPress={() => setShowAll((v) => !v)} style={{ minHeight: 44, justifyContent: "center", paddingHorizontal: spacing.lg, borderBottomWidth: 2, borderColor: colors.border }}>
          <Mono style={{ fontSize: 12, fontWeight: "700", color: colors.brandPrimary }}>{showAll ? "SHOW FEWER" : `SHOW ALL ${items.length}`}</Mono>
        </Pressable>
      ) : null}

      {sel ? (
        <Modal transparent animationType="slide" visible onRequestClose={() => setSel(null)}>
          <Pressable style={s.backdrop} onPress={() => setSel(null)} testID="follow-up-backdrop" />
          <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="follow-up-sheet">
            <KeyboardAwareScrollView contentContainerStyle={{ gap: spacing.md }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.md }}>
                <Display style={{ fontSize: 22, flex: 1 }}>{sel.stop.rating}★ · {sel.stop.customer_name}</Display>
                <Pressable testID="follow-up-close" onPress={() => setSel(null)} style={s.close}><Ionicons name="close" size={24} color={colors.onSurface} /></Pressable>
              </View>
              <Mono style={s.comment}>“{sel.stop.rating_comment || "No comment"}”</Mono>
              <View style={{ flexDirection: "row", gap: spacing.sm, flexWrap: "wrap" }}>
                <Pill label={sel.stop.tracking_code} tone="dark" />
                <Pill label={sel.driver?.name ?? "no driver"} tone="light" />
                <Pill label={sel.stop.customer_phone} tone="light" />
              </View>
              <Mono style={s.meta}>{sel.stop.address}</Mono>
              {sel.stop.apology_code ? (
                <Mono style={s.apology} testID="follow-up-apology">
                  APOLOGY TEXT {sel.stop.apology_sms_status === "sent" ? "SENT" : sel.stop.apology_sms_status === "skipped_fictional" ? "SKIPPED (DEMO NUMBER)" : (sel.stop.apology_sms_status ?? "PENDING").toUpperCase()} · CODE {sel.stop.apology_code}
                </Mono>
              ) : null}
              <Mono style={s.label}>FOLLOW-UP NOTE</Mono>
              <TextInput
                testID="follow-up-note-input"
                value={note}
                onChangeText={setNote}
                multiline
                placeholder="e.g. Called customer, refunded delivery fee, coached driver"
                placeholderTextColor={colors.muted}
                style={s.input}
              />
              <View style={{ flexDirection: "row", gap: spacing.sm }}>
                <Btn testID="follow-up-save-open-button" label="SAVE NOTE" variant="outline" loading={saving} onPress={() => save(false)} style={{ flex: 1 }} />
                <Btn testID="follow-up-resolve-button" label="RESOLVE" icon="checkmark-done" variant="success" loading={saving} onPress={() => save(true)} style={{ flex: 1 }} />
              </View>
            </KeyboardAwareScrollView>
          </View>
        </Modal>
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  head: { flexDirection: "row", alignItems: "center", gap: spacing.sm, padding: spacing.lg, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  headText: { fontSize: 12, fontWeight: "700", letterSpacing: 1, flex: 1 },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.lg, borderBottomWidth: 2, borderColor: c.border },
  stars: { fontSize: 22, color: c.brandPrimary, width: 44 },
  comment: { fontSize: 13, fontWeight: "700" },
  meta: { fontSize: 11, color: c.muted },
  backdrop: { flex: 1, backgroundColor: c.surfaceInverse, opacity: 0.5 },
  sheet: { backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.lg, maxHeight: "80%" },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  apology: { fontSize: 11, fontWeight: "700", backgroundColor: c.surfaceInverse, color: c.onSurfaceInverse, padding: spacing.sm },
  input: { minHeight: 96, borderWidth: 2, borderColor: c.border, padding: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface, textAlignVertical: "top" },
}));
