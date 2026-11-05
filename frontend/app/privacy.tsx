import React from "react";
import { ScrollView, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/src/api";
import BrandHeader, { Kicker } from "@/src/components/brand/BrandHeader";
import BrandButton from "@/src/components/brand/BrandButton";
import { fonts, makeStyles } from "@/src/theme";

// Public Privacy Policy. Editable placeholder contact; have a lawyer review before launch.
const UPDATED = "June 2026";

const sections = (officer: string, email: string): { h: string; p: string[] }[] => [
  { h: "Who we are", p: [
    "LocalDash Inc. (\"LocalDash\") provides same-day local delivery for shops and pharmacies in Kitchener-Waterloo, Ontario. This policy explains how we handle personal information under Canada's Personal Information Protection and Electronic Documents Act (PIPEDA) and, for pharmacy deliveries, Ontario's Personal Health Information Protection Act, 2004 (PHIPA).",
    "For pharmacy orders, the pharmacy is the health information custodian. LocalDash acts as its agent and only handles what it needs to complete the delivery.",
  ] },
  { h: "What we collect", p: [
    "Recipients: name, delivery address, phone number, delivery instructions (for example a gate code or \"leave at side door\"), the delivery time window, and proof of delivery (a doorstep photo or signature, and the time it was delivered). We may also keep a rating and comment if you choose to leave one.",
    "Shops and pharmacies: business name, contact name, work email, phone, store and pickup address, and billing records.",
    "Drivers: name, phone number, vehicle, and location while a route is active.",
    "What we do not collect: prescription names, medications, dosages, diagnoses or any other health details. Shops are told never to put these in order notes.",
  ] },
  { h: "Why we collect it", p: [
    "Only to deliver your order: planning the route, telling the driver where to go, letting you track the delivery, contacting you about it through a masked phone line, proving it was delivered, and billing the shop. We do not sell personal information or use it for advertising.",
  ] },
  { h: "Consent", p: [
    "Shops confirm the recipient's consent each time they place orders. We record when consent was given and who gave it, alongside the order. Shops, pharmacies and drivers also accept these terms before they use LocalDash, and we record that acceptance with a timestamp.",
    "You can withdraw consent at any time by contacting the shop you ordered from or our Privacy Officer. If you do, we may not be able to complete a delivery.",
  ] },
  { h: "How we store and protect it", p: [
    "All data travels over encrypted HTTPS connections. Delivery notes, signatures and doorstep photos are also encrypted when they are stored.",
    "Access is limited by role. Drivers see only the recipient's name, street address and delivery notes for their own stops, never phone numbers, prices or invoice items. Shops see only their own orders, and only signed-in LocalDash staff can see everything else.",
    "Calls and texts between drivers and customers go through a masked number, so neither side sees the other's phone number. Every time anyone views or changes personal information, it is recorded in an audit log that shows who did it and when.",
  ] },
  { h: "How long we keep it", p: [
    "Personal details on an order are erased automatically after the retention period, which is 2 years by default. That includes the name, phone, address, notes, photo and signature. We keep invoice numbers and amounts for as long as tax law requires, but without the recipient's details.",
  ] },
  { h: "Sharing", p: [
    "We share information only with the shop that placed the order, the driver assigned to it, and the service providers that run the platform (hosting, SMS and email delivery). They are bound to protect it. We disclose information to authorities only when the law requires it.",
  ] },
  { h: "Your rights", p: [
    "Under PIPEDA and PHIPA you can ask to see the personal information we hold about you, ask us to correct it, ask how it has been used or shared, and withdraw your consent. You can also complain to us, and if you are not satisfied, to the Office of the Privacy Commissioner of Canada (priv.gc.ca) or, for health information, the Information and Privacy Commissioner of Ontario (ipc.on.ca).",
    "If a privacy breach puts you at real risk of significant harm, we will notify you and the relevant commissioner as the law requires.",
  ] },
  { h: "Contact", p: [`${officer}, Privacy Officer, LocalDash Inc. · ${email}. We reply within 30 days.`] },
];

export default function PrivacyPolicy() {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const officer = useQuery({ queryKey: ["privacy-officer"], queryFn: () => api<{ officer_name: string; officer_email: string }>("/privacy/officer") });
  return (
    <View style={s.root} testID="privacy-policy-screen">
      <BrandHeader />
      <ScrollView contentContainerStyle={[s.wrap, { paddingBottom: insets.bottom + 32 }]}>
        <Kicker>Privacy</Kicker>
        <Text style={s.title}>Privacy Policy</Text>
        <Text style={s.updated}>Last updated {UPDATED} · PIPEDA &amp; PHIPA (Ontario)</Text>
        {sections(officer.data?.officer_name ?? "Albert Desjardins", officer.data?.officer_email ?? "bertdesjardins@hotmail.com").map((sec) => (
          <View key={sec.h} style={s.section}>
            <Text style={s.h}>{sec.h}</Text>
            {sec.p.map((t, i) => <Text key={i} style={s.p}>{t}</Text>)}
          </View>
        ))}
        <BrandButton testID="privacy-back" label="Back" variant="outline" onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))} />
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.brandSoftGray },
  wrap: { padding: 20, gap: 12, width: "100%", maxWidth: 760, alignSelf: "center" },
  title: { fontFamily: fonts.brandHeading, fontSize: 30, color: c.brandCharcoal },
  updated: { fontFamily: fonts.brandBody, fontSize: 13, color: c.brandMuted },
  section: { backgroundColor: c.surface, borderRadius: 12, padding: 16, gap: 8 },
  h: { fontFamily: fonts.brandHeading, fontSize: 17, color: c.brandCharcoal },
  p: { fontFamily: fonts.brandBody, fontSize: 15, lineHeight: 23, color: c.brandCharcoal },
}));
