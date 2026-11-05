import React, { useState } from "react";
import { Pressable, Text, TextInput, useWindowDimensions, View } from "react-native";
import { useRouter } from "expo-router";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import Animated, { FadeInDown } from "react-native-reanimated";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import BrandHeader, { Kicker } from "@/src/components/brand/BrandHeader";
import BrandButton from "@/src/components/brand/BrandButton";
import { LogoMark } from "@/src/components/brand/Logo";
import { fonts, makeStyles, useTheme } from "@/src/theme";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/src/api";

const HERO = "https://images.unsplash.com/photo-1575663620136-5ebbfcc2c597?crop=entropy&cs=srgb&fm=jpg&q=80&w=1600";

const STEPS = [
  { icon: "cloud-upload-outline" as const, title: "Upload your orders", body: "Drop in a CSV from your shop system. We check every address and flag anything that needs a fix." },
  { icon: "git-network-outline" as const, title: "We plan and dispatch", body: "Orders are grouped by zone and handed to local drivers on optimised routes the same day." },
  { icon: "navigate-outline" as const, title: "Customers follow along", body: "Live tracking, arrival times and texts as the driver gets close. Proof of delivery at the door." },
];

const PERKS = [
  { icon: "flash-outline" as const, title: "Same-day, every day", body: "Order by the afternoon, delivered by evening across the city." },
  { icon: "camera-outline" as const, title: "Proof at every door", body: "Photo, signature or parcel scan on each drop." },
  { icon: "receipt-outline" as const, title: "Clear invoices", body: "An invoice for every delivery, with HST shown. No subscriptions." },
  { icon: "chatbubble-ellipses-outline" as const, title: "Notes that reach the driver", body: "Gate codes and drop-off tips saved for your regulars." },
];

export default function Landing() {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const { width } = useWindowDimensions();
  const wide = width >= 820;
  const [code, setCode] = useState("");
  const track = () => code.trim() && router.push(`/customer/${code.trim().toUpperCase()}`);
  const reviews = useQuery({ queryKey: ["testimonials"], queryFn: () => api<{ id: string; quote: string; person: string | null; shop: string }[]>("/testimonials") });
  const rates = useQuery({ queryKey: ["rate-card"], queryFn: () => api<{ currency: string; base_fee: number; included_km: number; per_km: number; priority_fee: number; hst_pct: number; tiers: { name: string; min_drops: number; max_drops: number | null; rate: number }[];
    pharmacy: { batch_rate: number; standard_rate: number; min_drops: number; included_km: number; per_km: number; monthly_fee: number;
      runs: { key: string; label: string; cutoff_label: string; pickup_label: string; window_label: string }[] } }>("/rate-card") });

  return (
    <View style={s.root} testID="landing-screen">
      <BrandHeader back={false} right={
        <Pressable testID="landing-track-link" onPress={() => router.push("/customer")} style={s.trackPill} hitSlop={4}>
          <Ionicons name="locate-outline" size={16} color={colors.brandCharcoal} />
          <Text style={s.trackPillText}>Track my order</Text>
        </Pressable>
      } />
      <KeyboardAwareScrollView contentContainerStyle={{ paddingBottom: insets.bottom }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
        {/* Hero */}
        <View style={[s.hero, { minHeight: wide ? 560 : 520 }]}>
          <Image source={HERO} style={s.abs} contentFit="cover" transition={300} />
          <LinearGradient colors={["rgba(51,51,53,0.15)", "rgba(51,51,53,0.55)", "rgba(51,51,53,0.92)"]} locations={[0, 0.45, 1]} style={s.abs} />
          <View style={[s.container, s.heroInner]}>
            <Animated.View entering={FadeInDown.duration(500)} style={{ gap: 16, maxWidth: 640 }}>
              <Text style={s.heroKicker}>Same-day local delivery</Text>
              <Text style={[s.heroTitle, wide && { fontSize: 54, lineHeight: 62 }]} testID="landing-hero-title">Your shop’s deliveries, done today.</Text>
              <Text style={s.heroBody}>DashLocal picks up from small businesses and delivers across town the same day, with live tracking your customers will love.</Text>
              <View style={[s.ctaRow, !wide && { flexDirection: "column" }]}>
                <BrandButton testID="landing-cta-merchant" label="Start delivering with us" icon="arrow-forward" onPress={() => router.push("/sign-up")} />
                <BrandButton testID="landing-cta-staff" label="Admin & driver sign-in" variant="outline" onPress={() => router.push("/sign-in/staff")} style={s.heroOutline} />
              </View>
            </Animated.View>
          </View>
        </View>

        {/* How it works */}
        <View style={[s.container, s.section]}>
          <Kicker>How it works</Kicker>
          <Text style={s.h2}>Three steps from counter to doorstep</Text>
          <View style={[s.grid, wide && { flexDirection: "row" }]}>
            {STEPS.map((st, i) => (
              <Animated.View key={st.title} entering={FadeInDown.delay(100 * i)} style={[s.step, wide && { flex: 1 }]}>
                <View style={s.stepIcon}><Ionicons name={st.icon} size={24} color={colors.onBrandSky} /></View>
                <Text style={s.stepNum}>0{i + 1}</Text>
                <Text style={s.h3}>{st.title}</Text>
                <Text style={s.body}>{st.body}</Text>
              </Animated.View>
            ))}
          </View>
        </View>

        {/* Perks */}
        <View style={s.band}>
          <View style={[s.container, s.section]}>
            <Kicker>Built for small businesses</Kicker>
            <Text style={s.h2}>Everything you need, nothing you don’t</Text>
            <View style={[s.grid, wide && { flexDirection: "row", flexWrap: "wrap" }]}>
              {PERKS.map((p) => (
                <View key={p.title} style={[s.perk, wide && { width: "48%" }]}>
                  <Ionicons name={p.icon} size={22} color={colors.brandForest} />
                  <View style={{ flex: 1, gap: 4 }}>
                    <Text style={s.h3}>{p.title}</Text>
                    <Text style={s.body}>{p.body}</Text>
                  </View>
                </View>
              ))}
            </View>
          </View>
        </View>

        {/* Rate card */}
        {rates.data ? (
          <View style={s.band} testID="landing-rate-card">
            <View style={[s.container, s.section]}>
              <Kicker>Pricing</Kicker>
              <Text style={s.h2}>Simple rates, no subscriptions</Text>
              <View style={[s.grid, wide && { flexDirection: "row" }]}>
                {[
                  { k: "base", title: "Per delivery", v: `$${rates.data.base_fee.toFixed(2)}`, sub: `Standard rate, includes the first ${rates.data.included_km} km` },
                  { k: "km", title: "Extra distance", v: `$${rates.data.per_km.toFixed(2)}`, sub: `per km after ${rates.data.included_km} km` },
                  { k: "priority", title: "Priority rush", v: `+$${rates.data.priority_fee.toFixed(2)}`, sub: "Goes out first on the route" },
                ].map((r) => (
                  <View key={r.k} style={[s.rate, wide && { flex: 1 }]} testID={`rate-${r.k}`}>
                    <Text style={s.rateTitle}>{r.title}</Text>
                    <Text style={s.rateVal}>{r.v}</Text>
                    <Text style={s.body}>{r.sub}</Text>
                  </View>
                ))}
              </View>
              <View style={[s.rate, { marginTop: 16 }]} testID="rate-tiers">
                <Text style={s.rateTitle}>Batch pricing: more drops a day, lower rate per drop</Text>
                {rates.data.tiers.map((t) => {
                  const save = Math.round((1 - t.rate / rates.data.base_fee) * 100);
                  return (
                    <View key={t.min_drops} style={s.tierRow} testID={`tier-${t.min_drops}`}>
                      <View style={{ flex: 1 }}>
                        <Text style={s.h3}>{t.name}</Text>
                        <Text style={s.body}>{t.max_drops ? `${t.min_drops}–${t.max_drops}` : `${t.min_drops}+`} drops a day</Text>
                      </View>
                      <View style={{ alignItems: "flex-end" }}>
                        <Text style={s.tierRate}>${t.rate.toFixed(2)}</Text>
                        <Text style={s.body}>{save > 0 ? `Save ${save}%` : "per drop"}</Text>
                      </View>
                    </View>
                  );
                })}
              </View>
              <View style={[s.rate, { marginTop: 16 }]} testID="rate-pharmacy">
                <Text style={s.rateTitle}>Pharmacy batch delivery</Text>
                <Text style={s.rateVal}>${rates.data.pharmacy.batch_rate.toFixed(2)}<Text style={s.body}> per drop</Text></Text>
                <Text style={s.body}>
                  For pharmacy partners with {rates.data.pharmacy.min_drops}+ drops on one pickup (fewer drops are billed at ${rates.data.pharmacy.standard_rate.toFixed(2)}). The first {rates.data.pharmacy.included_km} km from your pharmacy are included, then ${rates.data.pharmacy.per_km.toFixed(2)} per km. Platform fee is ${rates.data.pharmacy.monthly_fee.toFixed(2)} a month.
                </Text>
                {rates.data.pharmacy.runs.map((r) => (
                  <View key={r.key} style={s.tierRow} testID={`pharmacy-run-${r.key}`}>
                    <View style={{ flex: 1 }}>
                      <Text style={s.h3}>{r.label}</Text>
                      <Text style={s.body}>Order by {r.cutoff_label} · pickup {r.pickup_label}</Text>
                    </View>
                    <Text style={[s.body, { textAlign: "right", maxWidth: 130 }]}>Delivered {r.window_label}</Text>
                  </View>
                ))}
              </View>
              <Text style={[s.body, { marginTop: 12 }]} testID="rate-card-note">
                Prices in {rates.data.currency} plus {rates.data.hst_pct}% HST. Your rate is set by how many of your drops we complete that day, and every drop that day gets the lower rate. Distance and priority fees are added on top. You get an invoice for every completed delivery, and you only pay for deliveries we complete.
              </Text>
            </View>
          </View>
        ) : null}

        {/* Shop reviews (admin-managed; hidden until one is published) */}
        {reviews.data?.length ? (
          <View style={[s.container, s.section]} testID="landing-reviews">
            <Kicker>From our shops</Kicker>
            <Text style={s.h2}>Small businesses on DashLocal</Text>
            <View style={[s.grid, wide && { flexDirection: "row", flexWrap: "wrap" }]}>
              {reviews.data.map((r) => (
                <View key={r.id} style={[s.review, wide && { flexGrow: 1, flexBasis: "30%" }]} testID={`landing-review-${r.id}`}>
                  <Text style={s.quoteMark}>“</Text>
                  <Text style={s.quote}>{r.quote}</Text>
                  <View style={s.reviewBy}>
                    <View style={s.reviewDot}><Ionicons name="storefront-outline" size={16} color={colors.onBrandSky} /></View>
                    <View style={{ flex: 1 }}>
                      <Text style={s.reviewShop}>{r.shop}</Text>
                      {r.person ? <Text style={s.reviewPerson}>{r.person}</Text> : null}
                    </View>
                  </View>
                </View>
              ))}
            </View>
          </View>
        ) : null}

        {/* Sign-in options */}
        <View style={[s.container, s.section]} testID="landing-sign-in-options">
          <Kicker>Sign in</Kicker>
          <Text style={s.h2}>Pick your door</Text>
          <View style={[s.grid, wide && { flexDirection: "row" }]}>
            <View style={[s.card, wide && { flex: 1 }]}>
              <View style={[s.cardIcon, { backgroundColor: colors.brandForest }]}><Ionicons name="storefront-outline" size={24} color={colors.onBrandForest} /></View>
              <Text style={s.h3}>For merchants</Text>
              <Text style={s.body}>Upload orders, fix flagged rows, follow every drop, download reports and see your invoices.</Text>
              <BrandButton testID="landing-merchant-sign-in" label="Merchant sign-in" icon="arrow-forward" onPress={() => router.push("/sign-in/merchant")} />
              <BrandButton testID="landing-merchant-sign-up" label="New shop? Request an account" variant="outline" onPress={() => router.push("/sign-up")} />
            </View>
            <View style={[s.card, wide && { flex: 1 }]}>
              <View style={[s.cardIcon, { backgroundColor: colors.brandCharcoal }]}><Ionicons name="people-outline" size={24} color={colors.onBrandCharcoal} /></View>
              <Text style={s.h3}>For admins & drivers</Text>
              <Text style={s.body}>Dispatch opens the full control centre. Drivers sign in with their phone and PIN to see today’s manifest.</Text>
              <BrandButton testID="landing-staff-sign-in" label="Admin & driver sign-in" variant="dark" icon="arrow-forward" onPress={() => router.push("/sign-in/staff")} />
            </View>
          </View>
        </View>

        {/* Track an order */}
        <View style={s.trackBand}>
          <View style={[s.container, s.section, { gap: 12 }]}>
            <Text style={s.h2}>Waiting on a delivery?</Text>
            <Text style={s.body}>Enter the tracking code from your text or email.</Text>
            <View style={[s.trackRow, !wide && { flexDirection: "column" }]}>
              <TextInput
                testID="landing-track-input"
                value={code}
                onChangeText={(v) => setCode(v.toUpperCase())}
                placeholder="e.g. MD1001"
                placeholderTextColor={colors.brandMuted}
                autoCapitalize="characters"
                returnKeyType="go"
                onSubmitEditing={track}
                style={s.trackInput}
              />
              <BrandButton testID="landing-track-button" label="Track order" variant="gold" icon="locate-outline" disabled={!code.trim()} onPress={track} />
            </View>
          </View>
        </View>

        {/* Footer */}
        <View style={s.footer}>
          <View style={[s.container, s.footerInner]}>
            <LogoMark size={28} />
            <Text style={s.footerText}>© {new Date().getFullYear()} DashLocal · Same-day local delivery for small businesses</Text>
            <Text style={[s.footerText, { flex: 0, textDecorationLine: "underline" }]} onPress={() => router.push("/privacy")} testID="landing-privacy-link">Privacy Policy</Text>
          </View>
        </View>
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  abs: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  container: { width: "100%", maxWidth: 1120, alignSelf: "center", paddingHorizontal: 24 },
  hero: { justifyContent: "flex-end", backgroundColor: c.brandCharcoal },
  heroInner: { paddingVertical: 48 },
  heroKicker: { fontFamily: fonts.brandHeadingMedium, fontSize: 13, letterSpacing: 1.5, color: c.brandSky, textTransform: "uppercase" },
  heroTitle: { fontFamily: fonts.brandHeading, fontSize: 38, lineHeight: 46, color: c.onBrandCharcoal },
  heroBody: { fontFamily: fonts.brandBody, fontSize: 18, lineHeight: 28, color: c.onBrandCharcoal, opacity: 0.92 },
  ctaRow: { flexDirection: "row", gap: 12, marginTop: 8 },
  heroOutline: { borderColor: c.onBrandCharcoal, backgroundColor: c.surface },
  section: { paddingVertical: 56, gap: 8 },
  h2: { fontFamily: fonts.brandHeading, fontSize: 28, lineHeight: 36, color: c.brandCharcoal, marginBottom: 16 },
  h3: { fontFamily: fonts.brandHeading, fontSize: 19, color: c.brandCharcoal },
  body: { fontFamily: fonts.brandBody, fontSize: 16, lineHeight: 25, color: c.brandMuted },
  grid: { gap: 16 },
  step: { gap: 10, padding: 24, borderRadius: 20, borderWidth: 1, borderColor: c.brandLine, backgroundColor: c.surface },
  stepIcon: { width: 52, height: 52, borderRadius: 26, backgroundColor: c.brandSky, alignItems: "center", justifyContent: "center" },
  stepNum: { fontFamily: fonts.brandHeadingMedium, fontSize: 13, color: c.brandGold, letterSpacing: 1 },
  band: { backgroundColor: c.brandSoftGray },
  perk: { flexDirection: "row", gap: 16, padding: 20, borderRadius: 16, backgroundColor: c.surface },
  card: { gap: 12, padding: 24, borderRadius: 20, backgroundColor: c.surface, borderWidth: 1, borderColor: c.brandLine },
  review: { gap: 12, padding: 24, borderRadius: 20, backgroundColor: c.surface, borderWidth: 1, borderColor: c.brandLine },
  rate: { gap: 6, padding: 24, borderRadius: 20, backgroundColor: c.surface, borderWidth: 1, borderColor: c.brandLine },
  rateTitle: { fontFamily: fonts.brandHeadingMedium, fontSize: 14, color: c.brandForest, letterSpacing: 0.5 },
  tierRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12, borderTopWidth: 1, borderTopColor: c.brandLine },
  tierRate: { fontFamily: fonts.brandHeading, fontSize: 24, color: c.brandCharcoal },
  rateVal: { fontFamily: fonts.brandHeading, fontSize: 36, lineHeight: 44, color: c.brandCharcoal },
  quoteMark: { fontFamily: fonts.brandHeading, fontSize: 44, lineHeight: 44, color: c.brandGold, marginBottom: -12 },
  quote: { fontFamily: fonts.brandBody, fontSize: 17, lineHeight: 27, color: c.brandCharcoal },
  reviewBy: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 4 },
  reviewDot: { width: 32, height: 32, borderRadius: 16, backgroundColor: c.brandSky, alignItems: "center", justifyContent: "center" },
  reviewShop: { fontFamily: fonts.brandHeading, fontSize: 15, color: c.brandCharcoal },
  reviewPerson: { fontFamily: fonts.brandBody, fontSize: 14, color: c.brandMuted },
  cardIcon: { width: 52, height: 52, borderRadius: 14, alignItems: "center", justifyContent: "center" },
  trackPill: { minHeight: 44, flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 14, borderRadius: 22, borderWidth: 1.5, borderColor: c.brandGold },
  trackPillText: { fontFamily: fonts.brandHeadingMedium, fontSize: 14, color: c.brandCharcoal },
  trackBand: { backgroundColor: c.brandCream },
  trackRow: { flexDirection: "row", gap: 12, maxWidth: 560 },
  trackInput: { flex: 1, minHeight: 52, borderRadius: 12, backgroundColor: c.surface, borderWidth: 1, borderColor: c.brandLine, paddingHorizontal: 16, fontFamily: fonts.brandHeadingMedium, fontSize: 18, letterSpacing: 2, color: c.brandCharcoal },
  footer: { backgroundColor: c.brandCharcoal },
  footerInner: { paddingVertical: 28, flexDirection: "row", alignItems: "center", gap: 12 },
  footerText: { flex: 1, fontFamily: fonts.brandBody, fontSize: 13, color: c.onBrandCharcoal, opacity: 0.8 },
}));
