import React from "react";
import { Pressable, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import Animated, { FadeInDown, FadeOut } from "react-native-reanimated";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { Display, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Shoutout = { id: string; tags: string[]; created_at: string };

// Driver manifest: thank-you card when a patient leaves 5★ with a compliment. Tap to dismiss.
export default function ShoutoutBanner({ driverId }: { driverId: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  const q = useQuery({
    queryKey: ["shoutouts", driverId],
    queryFn: () => api<{ shoutouts: Shoutout[] }>(`/drivers/${driverId}/shoutouts/new`),
    refetchInterval: 30000,
  });
  const list = q.data?.shoutouts ?? [];
  if (!list.length) return null;
  const tags = Array.from(new Set(list.flatMap((x) => x.tags))).map((t) => t.toLowerCase());
  const dismiss = async () => {
    try {
      await api(`/drivers/${driverId}/shoutouts/seen`, { method: "POST", body: { ids: list.map((x) => x.id) } });
    } catch {
      // keep the banner; it will be dismissable again on the next tap
    } finally {
      q.refetch();
    }
  };
  return (
    <Animated.View entering={FadeInDown} exiting={FadeOut}>
      <Pressable testID="shoutout-banner" onPress={dismiss} style={s.card} accessibilityRole="button" accessibilityLabel="Dismiss thank-you">
        <Ionicons name="heart" size={28} color={colors.onSurfaceInverse} />
        <View style={{ flex: 1, gap: 2 }}>
          <Display style={s.title}>{list.length > 1 ? `${list.length} PATIENTS SAID THANK YOU!` : "A PATIENT SAID THANK YOU!"}</Display>
          <Mono style={s.text} testID="shoutout-text">5★ · {tags.join(" · ")}</Mono>
          <Mono style={s.text}>Tap to dismiss</Mono>
        </View>
      </Pressable>
    </Animated.View>
  );
}

const useStyles = makeStyles((c) => ({
  card: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.lg, backgroundColor: c.surfaceInverse, minHeight: 64 },
  title: { fontSize: 16, color: c.onSurfaceInverse },
  text: { fontSize: 12, color: c.onSurfaceInverse, fontWeight: "700" },
}));
