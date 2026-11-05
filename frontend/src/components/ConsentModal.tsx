import React, { useState } from "react";
import { Modal, NativeScrollEvent, NativeSyntheticEvent, Pressable, ScrollView, Text, View } from "react-native";
import Checkbox from "expo-checkbox";
import BrandButton from "@/src/components/brand/BrandButton";
import { fonts, makeStyles, useTheme } from "@/src/theme";

type Props = {
  visible: boolean;
  role: "merchant" | "driver";
  onAgree: (timestamp: string) => void;
  onClose: () => void;
  busy?: boolean;
  error?: string | null;
  onOpenPolicy?: () => void;
};

// PIPEDA / PHIPA consent: the person must scroll to the end of the terms, tick both boxes, then accept.
// The ISO timestamp goes to the backend, which also stamps its own time.
export default function ConsentModal({ visible, role, onAgree, onClose, busy, error, onOpenPolicy }: Props) {
  const s = useStyles();
  const { colors } = useTheme();
  const [read, setRead] = useState(false);
  const [data, setData] = useState(false);
  const [terms, setTerms] = useState(false);
  const ok = read && data && terms;

  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { layoutMeasurement, contentOffset, contentSize } = e.nativeEvent;
    if (layoutMeasurement.height + contentOffset.y >= contentSize.height - 20) setRead(true);
  };

  const box = (value: boolean, set: (v: boolean) => void, label: string, id: string) => (
    <Pressable testID={id} disabled={!read} onPress={() => set(!value)} style={s.row} accessibilityRole="checkbox" accessibilityState={{ checked: value, disabled: !read }}>
      <Checkbox value={value} onValueChange={set} disabled={!read} color={read ? colors.brandForest : colors.brandLine} />
      <Text style={[s.boxLabel, !read && { color: colors.brandMuted }]}>{label}</Text>
    </Pressable>
  );

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={s.overlay}>
        <View style={s.card} testID="consent-modal">
          <Text style={s.title}>{role === "merchant" ? "Merchant & Pharmacy Privacy Terms (PIPEDA / PHIPA)" : "Driver Privacy & Compliance (PIPEDA)"}</Text>
          <Text style={s.sub}>Please review and accept our data handling policy to proceed.</Text>
          <ScrollView style={s.scroll} contentContainerStyle={{ padding: 12 }} onScroll={onScroll} scrollEventThrottle={100}
            onContentSizeChange={(_, h) => h < 200 && setRead(true)} testID="consent-scroll">
            <Text style={s.h}>1. Collection of Personal & Health Information</Text>
            <Text style={s.p}>
              In compliance with the Personal Information Protection and Electronic Documents Act (PIPEDA) and Ontario&apos;s Personal Health Information Protection Act (PHIPA), LocalDash Inc. collects minimal necessary data (names, delivery addresses, contact details, and proof-of-delivery timestamps) to execute direct-fulfillment logistics. We never collect prescription names, diagnoses or other health details.
            </Text>
            <Text style={s.h}>2. Data Minimization & Role-Based Access</Text>
            <Text style={s.p}>
              {role === "merchant"
                ? "As a health information custodian partner, you confirm that all transmitted customer manifests comply with patient consent standards before handoff to LocalDash, and that order notes contain delivery instructions only — never medication or health details."
                : "As an independent driver, you acknowledge that delivery manifests contain confidential data. You agree strictly to data minimization: viewing only recipient names, street addresses and delivery notes required for drop execution, with zero disclosure of parcel contents."}
            </Text>
            <Text style={s.h}>3. Chain of Custody & Verification</Text>
            <Text style={s.p}>
              All medical and retail drop completions require digital verification (photo capture or signature). Unattended drops of prescriptions are strictly prohibited unless an explicit electronic waiver is documented. Notes, photos and signatures are encrypted at rest, every access is recorded in an audit log, and personal details are automatically erased after the retention period (2 years by default).
            </Text>
            {onOpenPolicy ? <Text style={s.link} onPress={onOpenPolicy} testID="consent-policy-link">Read the full Privacy Policy</Text> : null}
            <Text style={[s.p, { fontStyle: "italic", marginTop: 10 }]}>[Scroll to the bottom of this document to enable acceptance checkboxes.]</Text>
          </ScrollView>
          {box(data, setData, "I consent to the collection and secure processing of delivery information in compliance with PIPEDA/PHIPA.", "consent-data-checkbox")}
          {box(terms, setTerms, role === "merchant"
            ? "I agree to the Merchant Service Agreement and, for pharmacy partners, the Addendum A batch schedules (Afternoon: 1:30 PM cut-off, 2:00 PM pickup · Evening: 4:30 PM cut-off, 5:00 PM pickup)."
            : "I agree to the Independent Contractor Agreement and safety guidelines.", "consent-terms-checkbox")}
          {error ? <Text style={[s.boxLabel, { color: colors.brandPrimary, flex: 0 }]} testID="consent-error">{error}</Text> : null}
          <View style={s.actions}>
            <BrandButton testID="consent-cancel" label="Cancel" variant="outline" onPress={onClose} style={{ flex: 1 }} />
            <BrandButton testID="consent-accept" label="Accept & Continue" disabled={!ok} loading={busy} onPress={() => onAgree(new Date().toISOString())} style={{ flex: 2 }} />
          </View>
        </View>
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  overlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "center", padding: 20 },
  card: { backgroundColor: c.brandCream, borderRadius: 12, padding: 20, maxHeight: "88%", width: "100%", maxWidth: 560, alignSelf: "center", gap: 10 },
  title: { fontFamily: fonts.brandHeading, fontSize: 18, color: c.brandCharcoal },
  sub: { fontFamily: fonts.brandBody, fontSize: 13, color: c.brandMuted },
  scroll: { maxHeight: 230, borderWidth: 1, borderColor: c.brandLine, backgroundColor: c.surface, borderRadius: 8 },
  h: { fontFamily: fonts.brandHeadingMedium, fontSize: 13, color: c.brandCharcoal, marginTop: 8, marginBottom: 4 },
  p: { fontFamily: fonts.brandBody, fontSize: 12, color: c.brandCharcoal, lineHeight: 18 },
  link: { fontFamily: fonts.brandHeadingMedium, fontSize: 13, color: c.brandForest, marginTop: 10, textDecorationLine: "underline" },
  row: { flexDirection: "row", alignItems: "center", gap: 10, minHeight: 44 },
  boxLabel: { flex: 1, fontFamily: fonts.brandBody, fontSize: 12, color: c.brandCharcoal, lineHeight: 16 },
  actions: { flexDirection: "row", gap: 10, marginTop: 4 },
}));
