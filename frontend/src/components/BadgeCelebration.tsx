import React, { useEffect, useMemo } from "react";
import { Dimensions, Modal, View } from "react-native";
import Animated, { Easing, FadeIn, useAnimatedStyle, useSharedValue, withDelay, withRepeat, withSequence, withSpring, withTiming, ZoomIn } from "react-native-reanimated";
import * as Haptics from "expo-haptics";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import type { Badge } from "@/src/components/Badges";
import { Btn, Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

const ICONS: Record<string, React.ComponentProps<typeof Ionicons>["name"]> = { on_time: "time", top_rated: "star", speed: "flash", volume: "cube", five_star_month: "trophy" };
const BLURB: Record<string, string> = {
  on_time: "Best on-time rate of the week. Customers got their parcels inside their windows.",
  top_rated: "Highest customer rating of the week. People loved your deliveries.",
  speed: "Fastest pace of the week, measured in drops per hour.",
  volume: "Most drops delivered this week. Absolute machine.",
  five_star_month: "4.8★ or better from customers over the last 30 days. Keep it up!",
};
const fmt = (b: Badge) => (b.key === "on_time" ? `${b.value}%` : b.key === "top_rated" || b.key === "five_star_month" ? `${b.value}★` : b.key === "speed" ? `${b.value}/HR` : `${b.value} DROPS`);

function Confetti({ i, color }: { i: number; color: string }) {
  const { width, height } = Dimensions.get("window");
  const y = useSharedValue(-40);
  const r = useSharedValue(0);
  const x = useMemo(() => ((i * 53) % 100) / 100 * width, [i, width]);
  useEffect(() => {
    const dur = 2200 + ((i * 137) % 1400);
    y.value = withDelay((i * 70) % 900, withRepeat(withTiming(height + 40, { duration: dur, easing: Easing.linear }), -1, false));
    r.value = withRepeat(withTiming(360, { duration: 1200 + (i % 5) * 200 }), -1, false);
  }, [i, height, y, r]);
  const st = useAnimatedStyle(() => ({ transform: [{ translateY: y.value }, { rotate: `${r.value}deg` }] }));
  return <Animated.View pointerEvents="none" style={[{ position: "absolute", top: 0, left: x, width: 10, height: 16, backgroundColor: color }, st]} />;
}

// Full-screen celebration for newly earned weekly badges.
export default function BadgeCelebration({ badges, onDone }: { badges: Badge[]; onDone: () => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const pulse = useSharedValue(1);
  useEffect(() => {
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    pulse.value = withRepeat(withSequence(withSpring(1.08), withSpring(1)), -1, true);
  }, [pulse]);
  const pulseStyle = useAnimatedStyle(() => ({ transform: [{ rotate: "45deg" }, { scale: pulse.value }] }));
  const palette = [colors.brandPrimary, colors.warning, colors.onSurfaceInverse, colors.success];
  const main = badges[0];
  return (
    <Modal visible transparent animationType="fade" onRequestClose={onDone}>
      <Animated.View entering={FadeIn} style={s.root} testID="badge-celebration">
        {Array.from({ length: 28 }, (_, i) => <Confetti key={i} i={i} color={palette[i % palette.length]} />)}
        <Mono style={s.kicker}>NEW WEEKLY BADGE{badges.length > 1 ? "S" : ""} UNLOCKED</Mono>
        <Animated.View entering={ZoomIn.springify()} style={[s.medal, pulseStyle]}>
          <Ionicons name={ICONS[main.key] ?? "ribbon"} size={72} color={colors.onWarning} style={{ transform: [{ rotate: "-45deg" }] }} />
        </Animated.View>
        <Display style={s.title} testID="badge-celebration-title">{main.label}</Display>
        <Display style={s.value}>{fmt(main)}</Display>
        <Mono style={s.blurb}>{BLURB[main.key] ?? "You topped the board this week."}</Mono>
        {badges.length > 1 ? (
          <View style={s.more}>
            {badges.slice(1).map((b) => (
              <View key={b.key} style={s.chip} testID={`badge-celebration-extra-${b.key}`}>
                <Ionicons name={ICONS[b.key] ?? "ribbon"} size={14} color={colors.onWarning} />
                <Mono style={s.chipText}>{b.label} · {fmt(b)}</Mono>
              </View>
            ))}
          </View>
        ) : null}
        <Btn testID="badge-celebration-dismiss" label="KEEP IT UP" icon="flame" onPress={onDone} style={{ alignSelf: "stretch", marginTop: spacing.lg }} />
      </Animated.View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceInverse, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md, overflow: "hidden" },
  kicker: { color: c.warning, fontSize: 13, fontWeight: "700", letterSpacing: 2 },
  medal: { width: 150, height: 150, marginVertical: 32, backgroundColor: c.warning, borderWidth: 4, borderColor: c.onSurfaceInverse, alignItems: "center", justifyContent: "center" },
  title: { color: c.onSurfaceInverse, fontSize: 38, letterSpacing: -1, textAlign: "center" },
  value: { color: c.brandPrimary, fontSize: 28 },
  blurb: { color: c.onSurfaceInverse, fontSize: 13, textAlign: "center", lineHeight: 20 },
  more: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm, justifyContent: "center" },
  chip: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: c.warning, paddingHorizontal: spacing.sm, paddingVertical: 4 },
  chipText: { fontSize: 11, fontWeight: "700", color: c.onWarning },
}));
