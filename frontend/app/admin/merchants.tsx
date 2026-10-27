import React, { useState } from "react";
import { Pressable, RefreshControl, ScrollView, Switch, TextInput, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { clearSession, useSession } from "@/src/session";
import SignInForm from "@/src/components/SignInForm";
import { Btn, Display, Header, Loading, Mono, useToast } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Account = { id: string; email: string; created_at: string };
type Store = { id: string; name: string; accounts: Account[]; orders: number; business_type?: string; signature_required?: boolean; pharmacy_partner?: boolean };

const TYPES = [
  { k: "store", label: "STORE" },
  { k: "law_office", label: "LAW OFFICE" },
  { k: "flower_shop", label: "FLOWER SHOP" },
  { k: "pharmacy", label: "PHARMACY" },
  { k: "other", label: "OTHER" },
] as const;
const typeOf = (m: Store) => m.business_type ?? (m.pharmacy_partner ? "pharmacy" : "store");
const typeLabel = (k: string) => TYPES.find((t) => t.k === k)?.label ?? "STORE";

// Admin: create merchant stores + logins, and reset a merchant's password.
export default function MerchantAccounts() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const { ready, user } = useSession();
  const isAdmin = user?.role === "admin";
  const q = useQuery({ queryKey: ["merchant-accounts"], queryFn: () => api<Store[]>("/admin/merchant-accounts"), enabled: isAdmin });
  const [form, setForm] = useState({ store_name: "", email: "", password: "", business_type: "store" });
  const [busy, setBusy] = useState<string | null>(null);
  const [resetFor, setResetFor] = useState<string | null>(null);
  const [newPw, setNewPw] = useState("");
  const [filter, setFilter] = useState<string>("all");
  const [editType, setEditType] = useState<string | null>(null);

  if (!ready) return <View style={s.root}><Header title="MERCHANT ACCOUNTS" /><Loading /></View>;
  if (!isAdmin) {
    return (
      <View style={s.root} testID="admin-sign-in-screen">
        <Header title="ADMIN SIGN-IN" subtitle="Merchant accounts" />
        <SignInForm role="admin" title="DISPATCH ADMIN" hint="Sign in with the dispatch admin account to create and manage merchant logins." />
      </View>
    );
  }

  const create = async () => {
    setBusy("create");
    try {
      const m = await api<Store>("/admin/merchant-accounts", { method: "POST", body: form });
      toast.show(`${m.name.toUpperCase()} CAN NOW SIGN IN`, "green");
      setForm({ store_name: "", email: "", password: "", business_type: "store" });
      q.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  const saveSettings = async (m: Store, body: { business_type?: string; signature_required?: boolean }, msg: string) => {
    setBusy(`set-${m.id}`);
    try {
      await api(`/admin/merchants/${m.id}/settings`, { method: "PUT", body });
      toast.show(msg, "green");
      setEditType(null);
      q.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  const stores = (q.data ?? []).filter((m) => filter === "all" || typeOf(m) === filter);
  const chip = (on: boolean) => [s.chip, on && { backgroundColor: colors.onSurface }];
  const chipText = (on: boolean) => [s.chipText, on && { color: colors.surface }];

  const resetPassword = async (a: Account) => {
    setBusy(a.id);
    try {
      await api(`/admin/merchant-accounts/${a.id}/password`, { method: "PUT", body: { password: newPw } });
      toast.show(`PASSWORD UPDATED FOR ${a.email.toUpperCase()}`, "green");
      setResetFor(null);
      setNewPw("");
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(null);
    }
  };

  return (
    <View style={s.root} testID="merchant-accounts-screen">
      <Header title="MERCHANT ACCOUNTS" subtitle={`Signed in · ${user?.email}`} right={
        <Pressable testID="sign-out-button" onPress={() => clearSession()} style={s.iconBtn}><Ionicons name="log-out" size={22} color={colors.onSurfaceInverse} /></Pressable>
      } />
      <KeyboardAwareScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} keyboardShouldPersistTaps="handled" bottomOffset={24}
        refreshControl={<RefreshControl refreshing={false} onRefresh={() => q.refetch()} />}>
        <Mono style={s.section}>NEW STORE LOGIN</Mono>
        <View style={s.block}>
          {([
            { k: "store_name", label: "STORE NAME", ph: "Golden Gate Florist", secure: false },
            { k: "email", label: "LOGIN EMAIL", ph: "owner@store.com", secure: false },
            { k: "password", label: "PASSWORD (8+ CHARACTERS)", ph: "••••••••", secure: true },
          ] as const).map((f) => (
            <View key={f.k} style={{ gap: 4 }}>
              <Mono style={s.label}>{f.label}</Mono>
              <TextInput testID={`new-merchant-${f.k}`} value={form[f.k]} onChangeText={(v) => setForm((x) => ({ ...x, [f.k]: v }))} placeholder={f.ph}
                placeholderTextColor={colors.muted} secureTextEntry={f.secure} autoCapitalize={f.k === "store_name" ? "words" : "none"}
                keyboardType={f.k === "email" ? "email-address" : "default"} autoCorrect={false} style={s.input} />
            </View>
          ))}
          <View style={{ gap: 4 }}>
            <Mono style={s.label}>TYPE OF BUSINESS</Mono>
            <View style={s.chips}>
              {TYPES.map((t) => (
                <Pressable key={t.k} testID={`new-merchant-type-${t.k}`} onPress={() => setForm((x) => ({ ...x, business_type: t.k }))} style={chip(form.business_type === t.k)}>
                  <Mono style={chipText(form.business_type === t.k)}>{t.label}</Mono>
                </Pressable>
              ))}
            </View>
            {form.business_type === "law_office" ? <Mono style={s.meta}>Law offices need a signature at the door by default — you can turn this off below.</Mono> : null}
          </View>
          <Btn testID="new-merchant-submit" label="CREATE ACCOUNT" icon="person-add" loading={busy === "create"} onPress={create} />
          <Mono style={s.meta}>Share the email and password with the store owner. They sign in from the Merchant console and only see their own orders.</Mono>
        </View>

        <Mono style={s.section}>STORES · {stores.length}{filter !== "all" ? ` OF ${q.data?.length ?? 0}` : ""}</Mono>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={[s.chips, { padding: spacing.md, flexWrap: "nowrap" }]} testID="store-type-filter">
          {[{ k: "all", label: "ALL" }, ...TYPES].map((t) => (
            <Pressable key={t.k} testID={`store-filter-${t.k}`} onPress={() => setFilter(t.k)} style={chip(filter === t.k)}>
              <Mono style={chipText(filter === t.k)}>{t.label} · {t.k === "all" ? q.data?.length ?? 0 : (q.data ?? []).filter((m) => typeOf(m) === t.k).length}</Mono>
            </Pressable>
          ))}
        </ScrollView>
        {q.isLoading ? <Loading /> : null}
        {!q.isLoading && stores.length === 0 ? <Mono style={[s.meta, { padding: spacing.lg }]} testID="store-filter-empty">No stores of this type yet.</Mono> : null}
        {stores.map((m) => (
          <View key={m.id} style={s.store} testID={`merchant-account-${m.id}`}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }}>
              <Display style={{ fontSize: 18, flex: 1 }} numberOfLines={1}>{m.name}</Display>
              <Mono style={s.meta}>{m.orders} ORDERS</Mono>
            </View>
            <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm, flexWrap: "wrap" }}>
              <Pressable testID={`store-type-${m.id}`} onPress={() => setEditType(editType === m.id ? null : m.id)} style={s.smallBtn}>
                <Mono style={s.smallBtnText}>{typeLabel(typeOf(m))} ▾</Mono>
              </Pressable>
              <View style={{ flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "flex-end", gap: spacing.sm }}>
                <Mono style={s.meta}>SIGNATURE AT DOOR</Mono>
                <Switch testID={`store-signature-${m.id}`} value={!!m.signature_required} disabled={busy === `set-${m.id}`}
                  onValueChange={(v) => saveSettings(m, { signature_required: v }, v ? `${m.name.toUpperCase()}: SIGNATURE REQUIRED` : `${m.name.toUpperCase()}: SIGNATURE OPTIONAL`)} />
              </View>
            </View>
            {editType === m.id ? (
              <View style={s.chips}>
                {TYPES.map((t) => (
                  <Pressable key={t.k} testID={`store-type-${m.id}-${t.k}`} style={chip(typeOf(m) === t.k)}
                    onPress={() => saveSettings(m, { business_type: t.k }, `${m.name.toUpperCase()} IS NOW ${t.label}`)}>
                    <Mono style={chipText(typeOf(m) === t.k)}>{t.label}</Mono>
                  </Pressable>
                ))}
              </View>
            ) : null}
            {m.accounts.length === 0 ? <Mono style={s.meta}>NO LOGIN YET</Mono> : null}
            {m.accounts.map((a) => (
              <View key={a.id} style={{ gap: spacing.sm }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }}>
                  <Mono style={[s.email, { flex: 1 }]} numberOfLines={1}>{a.email}</Mono>
                  <Pressable testID={`reset-password-${a.id}`} onPress={() => { setNewPw(""); setResetFor(resetFor === a.id ? null : a.id); }} style={s.smallBtn}>
                    <Mono style={s.smallBtnText}>{resetFor === a.id ? "CANCEL" : "RESET PASSWORD"}</Mono>
                  </Pressable>
                </View>
                {resetFor === a.id ? (
                  <View style={{ flexDirection: "row", gap: spacing.sm }}>
                    <TextInput testID={`reset-password-input-${a.id}`} value={newPw} onChangeText={setNewPw} secureTextEntry placeholder="New password"
                      placeholderTextColor={colors.muted} style={[s.input, { flex: 1 }]} />
                    <Btn testID={`reset-password-save-${a.id}`} label="SAVE" loading={busy === a.id} onPress={() => resetPassword(a)} />
                  </View>
                ) : null}
              </View>
            ))}
          </View>
        ))}
      </KeyboardAwareScrollView>
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  iconBtn: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.onSurfaceInverse },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.muted },
  input: { height: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 15, color: c.onSurface },
  meta: { fontSize: 11, color: c.muted },
  store: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  email: { fontSize: 13, fontWeight: "700" },
  smallBtn: { minHeight: 44, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  smallBtnText: { fontSize: 11, fontWeight: "700" },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  chip: { minHeight: 44, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 11, fontWeight: "700", color: c.onSurface },
}));
