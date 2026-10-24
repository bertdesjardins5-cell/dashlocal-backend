import React from "react";
import { Pressable, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import Animated, { FadeInDown } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

const ROLES = [
  { key: "driver", n: "01", title: "DRIVER", desc: "Manifest, next stop, geofenced arrival, proof of delivery.", icon: "car-sport" as const, href: "/driver" },
  { key: "customer", n: "02", title: "CUSTOMER", desc: "Live tracking, dynamic ETA, stops-away alerts.", icon: "location" as const, href: "/customer" },
  { key: "admin", n: "03", title: "ADMIN", desc: "Batch by zone, re-sequence drops, route analytics.", icon: "grid" as const, href: "/admin" },
  { key: "merchant", n: "04", title: "MERCHANT", desc: "Upload orders from a CSV, fix flagged rows, follow every drop.", icon: "storefront" as const, href: "/merchant" },
];

export default function RolePicker() {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  return (
    <ScrollView style={s.root} contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} testID="role-picker-screen">
      <View style={s.hero}>
        <Image source="https://images.pexels.com/photos/12970764/pexels-photo-12970764.jpeg?auto=compress&w=900" style={StyleSheetAbs} contentFit="cover" />
        <LinearGradient colors={["transparent", colors.surfaceInverse]} style={StyleSheetAbs} />
        <View style={[s.heroInner, { paddingTop: insets.top + spacing.xl }]}>
          <Mono style={s.kicker}>MULTIDROP / OPS v1</Mono>
          <Display style={s.title}>EVERY DROP.{"\n"}IN ORDER.</Display>
        </View>
      </View>
      <Mono style={s.section}>SELECT CONSOLE</Mono>
      {ROLES.map((r, i) => (
        <Animated.View key={r.key} entering={FadeInDown.delay(80 * i)}>
          <Pressable
            testID={`role-${r.key}-button`}
            onPress={() => router.push(r.href as any)}
            style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.brandPrimary }]}
          >
            {({ pressed }) => (
              <>
                <Mono style={[s.num, pressed && { color: colors.onBrandPrimary }]}>{r.n}</Mono>
                <View style={{ flex: 1, gap: spacing.xs }}>
                  <Display style={[s.rowTitle, pressed && { color: colors.onBrandPrimary }]}>{r.title}</Display>
                  <Mono style={[s.desc, pressed && { color: colors.onBrandPrimary }]}>{r.desc}</Mono>
                </View>
                <Ionicons name={r.icon} size={28} color={pressed ? colors.onBrandPrimary : colors.onSurface} />
              </>
            )}
          </Pressable>
        </Animated.View>
      ))}
    </ScrollView>
  );
}

const StyleSheetAbs = { position: "absolute" as const, top: 0, left: 0, right: 0, bottom: 0 };

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  hero: { height: 300, backgroundColor: c.surfaceInverse, justifyContent: "flex-end" },
  heroInner: { padding: spacing.xl, gap: spacing.sm },
  kicker: { color: c.brandPrimary, fontSize: 12, fontWeight: "700", letterSpacing: 2 },
  title: { color: c.onSurfaceInverse, fontSize: 44, lineHeight: 46, letterSpacing: -1.5 },
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.lg, padding: spacing.xl, borderBottomWidth: 2, borderColor: c.border, minHeight: 110 },
  num: { fontSize: 28, fontWeight: "700", color: c.brandPrimary },
  rowTitle: { fontSize: 28, letterSpacing: -0.5 },
  desc: { fontSize: 12, color: c.muted, lineHeight: 18 },
}));
