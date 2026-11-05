import React from "react";
import { Modal, Pressable, ScrollView, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { Btn, Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

const POINTS: { icon: any; title: string; body: string }[] = [
  { icon: "location", title: "What we collect", body: "Your phone's location (GPS), every few seconds." },
  { icon: "phone-portrait", title: "Even when the app is closed", body: "LocalDash collects your location in the background, including when the app is closed or your phone is locked, but only while you have an active route." },
  { icon: "flag", title: "Why", body: "So each stop marks ARRIVED automatically when you're within 50 m, and customers get an accurate live ETA." },
  { icon: "people", title: "Who sees it", body: "Dispatch, and each customer only while their own delivery is on the way. It is never sold or used for ads." },
  { icon: "stop-circle", title: "When it stops", body: "As soon as your route ends or you sign out. A notification shows whenever it's on, and you can turn it off anytime in your phone's Settings." },
];

// Prominent disclosure shown BEFORE the system "Allow all the time / Always" prompt (Google Play + App Store requirement).
export default function BackgroundLocationDisclosure({ visible, onAllow, onDecline }: { visible: boolean; onAllow: () => void; onDecline: () => void }) {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onDecline}>
      <View style={s.backdrop}>
        <View style={[s.sheet, { paddingBottom: insets.bottom + spacing.lg }]} testID="bg-location-disclosure">
          <ScrollView contentContainerStyle={{ gap: spacing.md }}>
            <Mono style={s.kicker}>LOCATION WHILE ON A ROUTE</Mono>
            <Display style={s.title}>Allow location &quot;All the time&quot;?</Display>
            {POINTS.map((p) => (
              <View key={p.title} style={s.point}>
                <Ionicons name={p.icon} size={22} color={colors.onSurface} />
                <View style={{ flex: 1, gap: 2 }}>
                  <Mono style={s.pTitle}>{p.title.toUpperCase()}</Mono>
                  <Mono style={s.pBody}>{p.body}</Mono>
                </View>
              </View>
            ))}
            <Mono style={s.note}>On the next screen choose &quot;Allow all the time&quot; (Android) or &quot;Change to Always Allow&quot; (iPhone).</Mono>
          </ScrollView>
          <Btn testID="bg-location-allow" label="CONTINUE" icon="navigate" onPress={onAllow} />
          <Pressable testID="bg-location-decline" onPress={onDecline} style={s.decline}>
            <Mono style={s.declineText}>NO THANKS · ONLY WHILE THE APP IS OPEN</Mono>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.5)" },
  sheet: { maxHeight: "92%", backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.lg, gap: spacing.md },
  kicker: { fontSize: 11, fontWeight: "700", letterSpacing: 2, color: c.muted },
  title: { fontSize: 24 },
  point: { flexDirection: "row", gap: spacing.md, alignItems: "flex-start", borderWidth: 2, borderColor: c.border, padding: spacing.md },
  pTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1 },
  pBody: { fontSize: 13, lineHeight: 19 },
  note: { fontSize: 12, color: c.muted },
  decline: { minHeight: 44, alignItems: "center", justifyContent: "center" },
  declineText: { fontSize: 12, fontWeight: "700", textDecorationLine: "underline" },
}));
