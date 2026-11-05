import React, { useEffect, useState } from "react";
import { Modal, NativeScrollEvent, NativeSyntheticEvent, Pressable, ScrollView, Text, View } from "react-native";
import Checkbox from "expo-checkbox";
import BrandButton from "@/src/components/brand/BrandButton";
import { TERMS } from "@/app/terms";
import { fonts, makeStyles, useTheme } from "@/src/theme";

type Props = { visible: boolean; onAgree: (timestamp: string) => void; onClose: () => void; busy?: boolean; onOpenTerms?: () => void;
  sub?: string; cancelLabel?: string; acceptLabel?: string; title?: string; agreeLabel?: string };

// Non-pharmacy sign-up (and re-acceptance after an update): scroll to the end of the Terms of Service, tick the box, then accept.
export default function TermsModal({ visible, onAgree, onClose, busy, onOpenTerms, sub = "Please review and accept our terms to send your request.",
  cancelLabel = "Cancel", acceptLabel = "Accept & Send", title = "DashLocal Terms of Service",
  agreeLabel = "I have read and agree to the DashLocal Terms of Service on behalf of my business." }: Props) {
  const s = useStyles();
  const { colors } = useTheme();
  const [read, setRead] = useState(false);
  const [agree, setAgree] = useState(false);
  useEffect(() => { if (visible) { setRead(false); setAgree(false); } }, [visible]); // fresh acceptance every time it opens

  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { layoutMeasurement, contentOffset, contentSize } = e.nativeEvent;
    if (layoutMeasurement.height + contentOffset.y >= contentSize.height - 20) setRead(true);
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={s.overlay}>
        <View style={s.card} testID="terms-modal">
          <Text style={s.title}>{title}</Text>
          <Text style={s.sub}>{sub}</Text>
          <ScrollView style={s.scroll} contentContainerStyle={{ padding: 12 }} onScroll={onScroll} scrollEventThrottle={100}
            onContentSizeChange={(_, h) => h < 200 && setRead(true)} testID="terms-scroll">
            {TERMS.map((sec) => (
              <View key={sec.h}>
                <Text style={s.h}>{sec.h}</Text>
                {sec.p.map((t, i) => <Text key={i} style={s.p}>{t}</Text>)}
              </View>
            ))}
            {onOpenTerms ? <Text style={s.link} onPress={onOpenTerms} testID="terms-full-link">Open the full Terms of Service</Text> : null}
          </ScrollView>
          {!read ? <Text style={s.sub}>Scroll to the end to enable the checkbox.</Text> : null}
          <Pressable testID="terms-agree-checkbox" disabled={!read} onPress={() => setAgree(!agree)} style={s.row}
            accessibilityRole="checkbox" accessibilityState={{ checked: agree, disabled: !read }}>
            <Checkbox value={agree} onValueChange={setAgree} disabled={!read} color={read ? colors.brandForest : colors.brandLine} />
            <Text style={[s.boxLabel, !read && { color: colors.brandMuted }]}>{agreeLabel}</Text>
          </Pressable>
          <View style={s.actions}>
            <BrandButton testID="terms-cancel" label={cancelLabel} variant="outline" onPress={onClose} style={{ flex: 1 }} />
            <BrandButton testID="terms-accept" label={acceptLabel} disabled={!(read && agree)} loading={busy} onPress={() => onAgree(new Date().toISOString())} style={{ flex: 2 }} />
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
  scroll: { maxHeight: 260, borderWidth: 1, borderColor: c.brandLine, backgroundColor: c.surface, borderRadius: 8 },
  h: { fontFamily: fonts.brandHeadingMedium, fontSize: 13, color: c.brandCharcoal, marginTop: 8, marginBottom: 4 },
  p: { fontFamily: fonts.brandBody, fontSize: 12, color: c.brandCharcoal, lineHeight: 18, marginBottom: 4 },
  link: { fontFamily: fonts.brandHeadingMedium, fontSize: 13, color: c.brandForest, marginTop: 10, textDecorationLine: "underline" },
  row: { flexDirection: "row", alignItems: "center", gap: 10, minHeight: 44 },
  boxLabel: { flex: 1, fontFamily: fonts.brandBody, fontSize: 12, color: c.brandCharcoal, lineHeight: 16 },
  actions: { flexDirection: "row", gap: 10, marginTop: 4 },
}));
