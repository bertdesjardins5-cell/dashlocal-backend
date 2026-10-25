import React from "react";
import { Pressable, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import Logo from "@/src/components/brand/Logo";
import { fonts, makeStyles, useTheme } from "@/src/theme";

// Light top bar for brand screens: back (or home) + logo, optional right slot.
export default function BrandHeader({ back = true, right }: { back?: boolean; right?: React.ReactNode }) {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const goBack = () => (router.canGoBack() ? router.back() : router.replace("/"));
  return (
    <View style={[s.bar, { paddingTop: insets.top + 12 }]}>
      <View style={s.inner}>
        {back ? (
          <Pressable testID="brand-back-button" onPress={goBack} style={s.back} hitSlop={6} accessibilityLabel="Back">
            <Ionicons name="arrow-back" size={22} color={colors.brandCharcoal} />
          </Pressable>
        ) : null}
        <Pressable onPress={() => router.replace("/")} testID="brand-home-logo">
          <Logo size={36} />
        </Pressable>
        <View style={{ flex: 1 }} />
        {right}
      </View>
    </View>
  );
}

export const useBrandText = makeStyles((c) => ({
  link: { fontFamily: fonts.brandHeadingMedium, fontSize: 15, color: c.brandCharcoal },
}));

const useStyles = makeStyles((c) => ({
  bar: { backgroundColor: c.surface, borderBottomWidth: 1, borderColor: c.brandLine, paddingBottom: 12, paddingHorizontal: 24 },
  inner: { width: "100%", maxWidth: 1120, alignSelf: "center", flexDirection: "row", alignItems: "center", gap: 12 },
  back: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center", backgroundColor: c.brandSoftGray },
}));

export function Kicker({ children }: { children: string }) {
  const s = useKicker();
  return <Text style={s.k}>{children}</Text>;
}
const useKicker = makeStyles((c) => ({ k: { fontFamily: fonts.brandHeadingMedium, fontSize: 13, letterSpacing: 1.5, color: c.brandForest, textTransform: "uppercase" } }));
