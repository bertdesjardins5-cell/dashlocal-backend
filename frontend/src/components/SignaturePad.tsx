import React, { useState } from "react";
import { Pressable, Text, View } from "react-native";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Svg, { Path } from "react-native-svg";
import { fonts, spacing, useTheme } from "@/src/theme";

// Captures strokes as an SVG path string ("M x y L x y ...").
export default function SignaturePad({ value, onChange }: { value: string; onChange: (d: string) => void }) {
  const { colors } = useTheme();
  const [current, setCurrent] = useState("");
  // The gesture is rebuilt every render, so `current` in onFinalize is the latest stroke.
  const pan = Gesture.Pan()
    .runOnJS(true)
    .minDistance(0)
    .onBegin((e) => setCurrent(`M${e.x.toFixed(1)} ${e.y.toFixed(1)}`))
    .onUpdate((e) => setCurrent((c) => `${c} L${e.x.toFixed(1)} ${e.y.toFixed(1)}`))
    .onFinalize(() => {
      if (current) onChange(`${value} ${current}`.trim());
      setCurrent("");
    });

  return (
    <View>
      <GestureDetector gesture={pan}>
        <View testID="signature-pad" style={{ height: 200, backgroundColor: colors.surface, borderWidth: 2, borderColor: colors.border }} collapsable={false}>
          <Svg width="100%" height="100%">
            <Path d={`${value} ${current}`.trim() || "M0 0"} stroke={colors.onSurface} strokeWidth={3} fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </Svg>
          {!value && !current ? (
            <Text pointerEvents="none" style={{ position: "absolute", bottom: spacing.md, left: spacing.md, fontFamily: fonts.mono, color: colors.muted }}>
              ✕ ____ SIGN HERE
            </Text>
          ) : null}
        </View>
      </GestureDetector>
      {value ? (
        <Pressable testID="signature-clear-button" onPress={() => onChange("")} style={{ alignSelf: "flex-end", padding: spacing.sm, minHeight: 44, justifyContent: "center" }}>
          <Text style={{ fontFamily: fonts.mono, color: colors.brandPrimary, fontWeight: "700" }}>CLEAR SIGNATURE</Text>
        </Pressable>
      ) : null}
    </View>
  );
}
