import React from "react";
import { ScrollView, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import BrandHeader, { Kicker } from "@/src/components/brand/BrandHeader";
import BrandButton from "@/src/components/brand/BrandButton";
import { fonts, makeStyles } from "@/src/theme";

// Public Terms of Service for stores, law offices, flower shops and other non-pharmacy businesses.
// DRAFT — edit freely and have a lawyer review before launch.
export const TERMS_UPDATED = "June 2026";

export const TERMS: { h: string; p: string[] }[] = [
  { h: "1. About these terms", p: [
    "These Terms of Service (\"Terms\") are an agreement between DashLocal Inc. (\"DashLocal\", \"we\") and the business that signs up for a DashLocal account (\"you\"). They apply to stores, law offices, flower shops and other businesses using DashLocal for same-day local delivery in Kitchener-Waterloo, Ontario. Pharmacies sign separate privacy and health-information terms.",
    "By requesting an account and using DashLocal, you agree to these Terms.",
  ] },
  { h: "2. The service", p: [
    "DashLocal picks up parcels from your business and delivers them to your customers, usually the same day. You send orders through the merchant portal (one at a time or by CSV upload). Your customers can track their delivery with a link and tracking code.",
    "Delivery times and ETAs are estimates. Weather, traffic and access problems at the address can cause delays.",
  ] },
  { h: "3. Your account", p: [
    "Accounts are approved by DashLocal. Keep your password private. You are responsible for orders placed from your account, and you should tell us right away if you think someone else has used it.",
  ] },
  { h: "4. What you can send", p: [
    "Parcels must be packed securely, clearly labelled and safe to carry in a car. You must not send anything illegal, dangerous or hazardous (for example weapons, explosives, flammable goods or controlled substances), live animals, cash, or items worth more than $500 unless we agree in writing first.",
    "For legal documents, tell us if a signature is required at delivery. We can collect a signature or a doorstep photo as proof of delivery.",
    "You must have your customer's permission to share their name, address and phone number with us for the delivery.",
  ] },
  { h: "5. Fees and billing", p: [
    "You pay the per-delivery fees shown in your portal, including any distance or rush fees, plus HST. Prices may change with 30 days' notice by email.",
    "We send invoices regularly and you can pay them online by card. Invoices are due when issued. If an invoice stays unpaid, we may pause new orders until it is paid.",
  ] },
  { h: "6. Delivery, failed attempts and returns", p: [
    "If nobody is available and the parcel can't be left safely, the driver will mark the attempt and may try again or return the parcel to you. Failed attempts and returns may be charged as a delivery.",
    "Report a missing or damaged parcel within 48 hours of the delivery time.",
  ] },
  { h: "7. Liability", p: [
    "We take reasonable care with every parcel. If a parcel is lost or damaged while we have it, and the cause is our fault, our liability is limited to the lesser of the parcel's value and $100 per delivery, unless we agreed to a higher amount in writing. We are not responsible for indirect losses such as lost profits, or for delays caused by events outside our control.",
  ] },
  { h: "8. Privacy", p: [
    "We handle your customers' personal information as described in our Privacy Policy, under Canada's PIPEDA. We only use it to complete and support deliveries, and we never sell it.",
  ] },
  { h: "9. Ending the service", p: [
    "You can stop using DashLocal at any time by contacting us. We may suspend or close an account that breaks these Terms or leaves invoices unpaid. Any fees owed up to that point remain payable.",
  ] },
  { h: "10. Changes and governing law", p: [
    "We may update these Terms. If a change is important, we will email you before it takes effect. These Terms are governed by the laws of Ontario and the federal laws of Canada that apply there.",
  ] },
  { h: "11. Contact", p: [
    "Questions about these Terms? Email us at hello@dashlocal.app.",
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
        <Text style={s.updated}>Last updated {TERMS_UPDATED} · Stores, law offices, flower shops and other businesses</Text>
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
