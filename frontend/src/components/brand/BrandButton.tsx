import React from "react";
import { ActivityIndicator, Pressable, Text, ViewStyle } from "react-native";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { fonts, makeStyles, useTheme } from "@/src/theme";

type Variant = "primary" | "dark" | "outline" | "gold";
type Props = { label: string; onPress: () => void; variant?: Variant; icon?: React.ComponentProps<typeof Ionicons>["name"]; loading?: boolean; disabled?: boolean; testID?: string; style?: ViewStyle };

// Rounded ArriveWise brand button for the landing and sign-in screens.
export default function BrandButton({ label, onPress, variant = "primary", icon, loading, disabled, testID, style }: Props) {
  const s = useStyles();
  const { colors } = useTheme();
  const bg = { primary: colors.brandForest, dark: colors.brandCharcoal, outline: "transparent", gold: colors.brandGold }[variant];
  const fg = { primary: colors.onBrandForest, dark: colors.onBrandCharcoal, outline: colors.brandCharcoal, gold: colors.onBrandGold }[variant];
  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [s.btn, { backgroundColor: bg }, variant === "outline" && s.outline, (disabled || loading) && { opacity: 0.5 }, pressed && { opacity: 0.85 }, style]}
    >
      {loading ? <ActivityIndicator color={fg} /> : (
        <>
          <Text style={[s.label, { color: fg }]}>{label}</Text>
          {icon ? <Ionicons name={icon} size={18} color={fg} /> : null}
        </>
      )}
    </Pressable>
  );
}

const useStyles = makeStyles((c) => ({
  btn: { minHeight: 52, borderRadius: 12, paddingHorizontal: 20, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 10 },
  outline: { borderWidth: 1.5, borderColor: c.brandCharcoal },
  label: { fontFamily: fonts.brandHeading, fontSize: 16 },
}));
