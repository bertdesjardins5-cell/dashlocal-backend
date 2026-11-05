import React, { useState } from "react";
import { Pressable, Switch, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { Btn, ErrorBlock, Header, Loading, Mono, Pill, useToast } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Review = { id: string; quote: string; person: string | null; shop: string; published: boolean; created_at: string; from_shop?: boolean };
const EMPTY = { quote: "", person: "", shop: "", published: true };

// Admin: manage shop quotes shown on the public landing page (newest 6 published appear).
export default function ReviewsScreen() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const q = useQuery({ queryKey: ["admin-testimonials"], queryFn: () => api<Review[]>("/admin/testimonials") });
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const stores = useQuery({ queryKey: ["merchants"], queryFn: () => api<{ id: string; name: string }[]>("/merchants") });
  const requests = useQuery({ queryKey: ["review-requests"], queryFn: () => api<{ id: string; shop: string; status: string; created_at: string; reminded_at?: string | null; reminder_status?: string }[]>("/admin/review-requests") });
  const [store, setStore] = useState<string | null>(null);
  const sendRequest = async () => {
    setBusy("request");
    try {
      await api("/admin/review-requests", { method: "POST", body: { merchant_id: store } });
      toast.show("REVIEW LINK EMAILED", "green");
      setStore(null);
      requests.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    setBusy("save");
    try {
      const body = { ...form, person: form.person || null };
      await api(editing ? `/admin/testimonials/${editing}` : "/admin/testimonials", { method: editing ? "PUT" : "POST", body });
      toast.show(editing ? "REVIEW UPDATED" : form.published ? "REVIEW PUBLISHED" : "REVIEW SAVED AS HIDDEN", "green");
      setForm(EMPTY);
      setEditing(null);
      q.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const run = async (id: string, fn: () => Promise<unknown>, msg: string) => {
    setBusy(id);
    try {
      await fn();
      toast.show(msg, "green");
      q.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };
  const toggle = (r: Review) =>
    run(r.id, () => api(`/admin/testimonials/${r.id}`, { method: "PUT", body: { quote: r.quote, person: r.person, shop: r.shop, published: !r.published } }), r.published ? "HIDDEN FROM LANDING PAGE" : "PUBLISHED");
  const [armed, setArmed] = useState<string | null>(null);
  const remove = (r: Review) => {
    if (armed !== r.id) {
      setArmed(r.id);
      toast.show("TAP DELETE AGAIN TO REMOVE THIS REVIEW", "dark");
      return;
    }
    setArmed(null);
    if (editing === r.id) {
      setEditing(null);
      setForm(EMPTY);
    }
    run(r.id, () => api(`/admin/testimonials/${r.id}`, { method: "DELETE" }), "REVIEW DELETED");
  };
  const edit = (r: Review) => {
    setEditing(r.id);
    setForm({ quote: r.quote, person: r.person ?? "", shop: r.shop, published: r.published });
  };

  return (
    <View style={s.root} testID="admin-reviews-screen">
      <Header title="SHOP REVIEWS" subtitle="On the landing page" />
      <KeyboardAwareScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
        <View style={s.block} testID="review-form">
          <Mono style={s.label}>{editing ? "EDIT REVIEW" : "ADD A REVIEW"}</Mono>
          <Mono style={s.meta}>Only use real quotes, with the shop&apos;s permission.</Mono>
          <TextInput testID="review-quote" value={form.quote} onChangeText={(v) => setForm({ ...form, quote: v })} placeholder="What the shop said about DashLocal" placeholderTextColor={colors.muted} multiline maxLength={400} style={[s.input, { minHeight: 96, textAlignVertical: "top", paddingTop: spacing.sm }]} />
          <TextInput testID="review-shop" value={form.shop} onChangeText={(v) => setForm({ ...form, shop: v })} placeholder="Shop name" placeholderTextColor={colors.muted} maxLength={80} style={s.input} />
          <TextInput testID="review-person" value={form.person} onChangeText={(v) => setForm({ ...form, person: v })} placeholder="Person & role (optional), e.g. Ana, owner" placeholderTextColor={colors.muted} maxLength={60} style={s.input} />
          <View style={s.row}>
            <Mono style={[s.meta, { flex: 1 }]}>SHOW ON LANDING PAGE</Mono>
            <Switch testID="review-published" value={form.published} onValueChange={(v) => setForm({ ...form, published: v })} trackColor={{ true: colors.success, false: colors.surfaceTertiary }} />
          </View>
          <View style={s.row}>
            {editing ? <Btn testID="review-cancel" label="CANCEL" variant="outline" onPress={() => { setEditing(null); setForm(EMPTY); }} style={{ flex: 1 }} /> : null}
            <Btn testID="review-save" label={editing ? "SAVE" : "ADD REVIEW"} icon="checkmark" loading={busy === "save"} disabled={!form.quote.trim() || !form.shop.trim()} onPress={save} style={{ flex: 2 }} />
          </View>
        </View>
        <View style={s.block} testID="review-request-section">
          <Mono style={s.label}>ASK A SHOP FOR A REVIEW</Mono>
          <Mono style={s.meta}>Emails the store a one-time link. Their quote arrives hidden here until you publish it.</Mono>
          <View style={s.chips}>
            {(stores.data ?? []).map((m) => (
              <Pressable key={m.id} testID={`review-request-store-${m.id}`} onPress={() => setStore(m.id)} style={[s.chip, store === m.id && { backgroundColor: colors.surfaceInverse }]}>
                <Mono style={[s.chipText, store === m.id && { color: colors.onSurfaceInverse }]}>{m.name}</Mono>
              </Pressable>
            ))}
          </View>
          <Btn testID="review-request-send" label="EMAIL REVIEW LINK" icon="mail" variant="dark" loading={busy === "request"} disabled={!store} onPress={sendRequest} />
          {(requests.data ?? []).slice(0, 5).map((r) => (
            <Mono key={r.id} style={s.meta} testID={`review-request-${r.id}`}>{r.shop} · {r.status === "submitted" ? "REVIEW RECEIVED" : r.reminder_status === "failed" ? "REMINDER FAILED" : r.reminded_at ? "REMINDER SENT" : "LINK SENT"} · {new Date(r.created_at).toLocaleDateString()}</Mono>
          ))}
        </View>
        <Mono style={s.section}>ALL REVIEWS</Mono>
        {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : !q.data?.length ? (
          <Mono style={[s.meta, { padding: spacing.lg }]} testID="reviews-empty">No reviews yet. The landing page hides the reviews section until one is published.</Mono>
        ) : q.data.map((r) => (
          <View key={r.id} style={s.item} testID={`review-${r.id}`}>
            <View style={{ flex: 1, gap: spacing.xs }}>
              <Mono style={s.quote}>“{r.quote}”</Mono>
              <Mono style={s.meta}>{r.shop}{r.person ? ` · ${r.person}` : ""}</Mono>
              <Pill label={r.published ? "published" : r.from_shop ? "needs approval" : "hidden"} tone={r.published ? "green" : r.from_shop ? "yellow" : "light"} />
            </View>
            <View style={{ gap: spacing.xs }}>
              <Pressable testID={`review-toggle-${r.id}`} accessibilityLabel={r.published ? "Hide review" : "Publish review"} disabled={busy === r.id} onPress={() => toggle(r)} style={s.icon}><Ionicons name={r.published ? "eye-off" : "eye"} size={20} color={colors.onSurface} /></Pressable>
              <Pressable testID={`review-edit-${r.id}`} accessibilityLabel="Edit review" onPress={() => edit(r)} style={s.icon}><Ionicons name="create" size={20} color={colors.onSurface} /></Pressable>
              <Pressable testID={`review-delete-${r.id}`} accessibilityLabel="Delete review" disabled={busy === r.id} onPress={() => remove(r)} style={[s.icon, armed === r.id && { backgroundColor: colors.brandPrimary }]}><Ionicons name="trash" size={20} color={armed === r.id ? colors.onBrandPrimary : colors.onSurface} /></Pressable>
            </View>
          </View>
        ))}
      </KeyboardAwareScrollView>
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  block: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 1.5 },
  meta: { fontSize: 12, color: c.muted },
  input: { minHeight: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  item: { flexDirection: "row", gap: spacing.md, padding: spacing.lg, borderBottomWidth: 2, borderColor: c.border },
  quote: { fontSize: 14, lineHeight: 20 },
  icon: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.border },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  chip: { minHeight: 44, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 12, fontWeight: "700" },
}));
