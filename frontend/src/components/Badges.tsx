import React from "react";
import { View } from "react-native";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

export type Badge = { key: string; label: string; value: number };

const ICONS: Record<string, React.ComponentProps<typeof Ionicons>["name"]> = {
  on_time: "time",
  top_rated: "star",
  speed: "flash",
  volume: "cube",
};

export default function Badges({ badges, testIDPrefix = "badge" }: { badges: Badge[]; testIDPrefix?: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  if (!badges.length) return null;
  return (
    <View style={s.row}>
      {badges.map((b) => (
        <View key={b.key} style={s.badge} testID={`${testIDPrefix}-${b.key}`}>
          <Ionicons name={ICONS[b.key] ?? "ribbon"} size={12} color={colors.onWarning} />
          <Mono style={s.text}>{b.label}</Mono>
        </View>
      ))}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  row: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs },
  badge: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: c.warning, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.sm, paddingVertical: 2 },
  text: { fontSize: 10, fontWeight: "700", color: c.onWarning, letterSpacing: 0.5 },
}));
