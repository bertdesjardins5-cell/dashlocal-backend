import React from "react";
import { Text, View } from "react-native";
import Svg, { Path } from "react-native-svg";
import { fonts, makeStyles, useTheme } from "@/src/theme";

// ArriveWise mark: a forest-green D whose stem is a red arrow pointing up, next to the stacked ARRIVE / WISE wordmark.
export function LogoMark({ size = 40 }: { size?: number }) {
  const { colors } = useTheme();
  return (
    <Svg width={size} height={size} viewBox="0 0 100 100" accessibilityLabel="ArriveWise">
      <Path d="M36 14 H54 A36 36 0 0 1 54 86 H36 V67 H53 A17 17 0 0 0 53 33 H36 Z" fill={colors.brandForest} />
      <Path d="M14 90 H31 V46 H43 L22.5 10 L2 46 H14 Z" fill={colors.brandRed} />
    </Svg>
  );
}

export default function Logo({ size = 40, inverse = false }: { size?: number; inverse?: boolean }) {
  const s = useStyles();
  const { colors } = useTheme();
  const fs = size * 0.42;
  return (
    <View style={s.row} testID="dashlocal-logo">
      <LogoMark size={size} />
      <View>
        <Text style={[s.word, { fontSize: fs, lineHeight: fs * 1.02, color: inverse ? colors.onBrandCharcoal : colors.brandCharcoal }]}>ARRIVE</Text>
        <Text style={[s.word, { fontSize: fs, lineHeight: fs * 1.02, color: colors.brandRed }]}>WISE</Text>
      </View>
    </View>
  );
}

const useStyles = makeStyles(() => ({
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  word: { fontFamily: fonts.brandHeading, letterSpacing: 0.5 },
}));
