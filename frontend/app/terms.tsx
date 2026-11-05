import React from "react";
import { ScrollView, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import BrandHeader, { Kicker } from "@/src/components/brand/BrandHeader";
import BrandButton from "@/src/components/brand/BrandButton";
import { fonts, makeStyles } from "@/src/theme";

// Public Terms of Service (customers, merchants, couriers). Shown on /terms and in the sign-up terms modal.
export const TERMS_UPDATED = "October 2026";

export const TERMS: { h: string; p: string[] }[] = [
  { h: "Agreement", p: [
    "By accessing or using our mobile application, website, or local delivery platform, you agree to be bound by these terms.",
  ] },
  { h: "1. Platform Overview and Scope", p: [
    "LocalDash is a technology and dispatch platform designed to facilitate local delivery services, including multi-stop batch runs, retail fulfillment, and specialized courier routing in Ontario, Canada.",
    "Independent Network: LocalDash acts solely as an intermediary technology platform connecting Customers and Merchants with independent third-party Courier Contractors.",
    "Not a Carrier: LocalDash does not directly operate delivery vehicles, provide retail goods, or function as a traditional common carrier.",
  ] },
  { h: "2. User Accounts & Security", p: [
    "Users must register an account to access core delivery or merchant management features.",
    "Customer — Registration requirement: valid phone number & email. Responsibility: accurate delivery addresses and contact availability.",
    "Merchant — Registration requirement: business verification & banking details. Responsibility: accurate product listing, inventory, and compliance with local retail laws.",
    "Courier / Driver — Registration requirement: valid driver licence, insurance & PIN setup. Responsibility: maintaining equipment security, vehicle roadworthiness, and secure PIN access.",
  ] },
  { h: "3. Orders, Pricing, and Payment Terms", p: [
    "All financial transactions are processed securely through integrated payment gateways (such as Square).",
    "Fee Structures: Delivery fees are calculated based on distance tiers, delivery speed (standard vs. express), and multi-stop batch parameters.",
    "Payment Authorization: Customers pay for each order separately, by credit or debit card in the app, when they choose to pay. Stores (merchants) keep a card on file and authorize LocalDash to charge it automatically: the monthly platform fee at the start of each month, and every Monday the deliveries completed the week before. All amounts are in Canadian dollars and include 13% Ontario HST, and a receipt is emailed after every charge.",
    "Cancellations & Refunds: Orders cancelled after dispatch may incur a partial fee to compensate couriers for travel time and effort.",
  ] },
  { h: "4. Independent Contractor Terms (For Drivers)", p: [
    "Couriers utilizing the platform enter into an independent contractor relationship rather than an employment contract.",
    "Expenses & Taxes: Couriers are solely responsible for all vehicle maintenance, fuel, insurance, and tax withholdings.",
    "Service Standards: Couriers agree to maintain device notification access for urgent dispatch runs and adhere to strict delivery timelines.",
    "Account Security: PIN-based sign-ins and forgot-PIN request workflows are monitored for security and compliance.",
  ] },
  { h: "5. Privacy, Data Protection, and Compliance (Ontario Standards)", p: [
    "LocalDash complies with applicable privacy laws in Ontario, Canada.",
    "PIPEDA Compliance: Personal information (names, phone numbers, delivery addresses) is collected solely for fulfilling delivery transactions and improving platform dispatch efficiency.",
    "PHIPA Safeguards: For specialized services such as scheduled pharmacy batch runs, all couriers and platform handlers strictly adhere to privacy standards aligned with Ontario's Personal Health Information Protection Act to protect patient confidentiality.",
  ] },
  { h: "6. Limitation of Liability", p: [
    "LocalDash is not liable for indirect, incidental, or consequential damages arising from delivery delays, third-party merchant product defects, or force majeure events beyond reasonable control.",
  ] },
];

export default function TermsOfService() {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  return (
    <View style={s.root} testID="terms-screen">
      <BrandHeader />
      <ScrollView contentContainerStyle={[s.wrap, { paddingBottom: insets.bottom + 32 }]}>
        <Kicker>Terms</Kicker>
        <Text style={s.title}>Terms of Service</Text>
        <Text style={s.updated}>Last updated {TERMS_UPDATED} · Customers, merchants and couriers</Text>
        {TERMS.map((sec) => (
          <View key={sec.h} style={s.section}>
            <Text style={s.h}>{sec.h}</Text>
            {sec.p.map((t, i) => <Text key={i} style={s.p}>{t}</Text>)}
          </View>
        ))}
        <BrandButton testID="terms-back" label="Back" variant="outline" onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))} />
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
