import React, { useState } from "react";
import { Pressable, TextInput, View } from "react-native";
import Animated, { FadeInDown, ZoomIn } from "react-native-reanimated";
import * as Haptics from "expo-haptics";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, Stop } from "@/src/api";
import { Btn, Display, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

const POSITIVE = ["ON TIME", "FRIENDLY DRIVER", "HANDLED WITH CARE", "DISCREET", "EASY TO FOLLOW"];
const NEGATIVE = ["LATE", "HARD TO FIND PARCEL", "DAMAGED PARCEL", "NOT DISCREET", "RUDE DRIVER", "WRONG ADDRESS"];
const tagsFor = (stars: number) => (stars >= 4 ? POSITIVE : stars <= 2 ? NEGATIVE : [...NEGATIVE.slice(0, 2), ...POSITIVE.slice(0, 2)]);
const WORDS = ["", "POOR", "MEH", "OKAY", "GOOD", "GREAT"];

type Props = { stop: { id: string; rating?: number | null; apology_code?: string | null }; orderToken: string; onRated: (s: Stop) => void; onError: (msg: string) => void };

export default function RatingPrompt({ stop, orderToken, onRated, onError }: Props) {
  const s = useStyles();
  const { colors } = useTheme();
  const [stars, setStars] = useState(0);
  const [tags, setTags] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [comment, setComment] = useState("");

  if (stop.rating) {
    return (
      <View style={{ gap: spacing.sm }}>
        <View style={s.done} testID="rating-thanks">
          <View style={{ flexDirection: "row", gap: 2 }}>
            {[1, 2, 3, 4, 5].map((n) => (
              <Ionicons key={n} name={n <= stop.rating! ? "star" : "star-outline"} size={18} color={colors.brandPrimary} />
            ))}
          </View>
          <Mono style={s.label}>THANKS — YOU RATED THIS DELIVERY</Mono>
        </View>
        {stop.apology_code ? (
          <View style={s.apology} testID="apology-box">
            <Display style={{ fontSize: 16, color: colors.onBrandPrimary }}>WE&apos;RE SORRY.</Display>
            <Mono style={{ fontSize: 12, color: colors.onBrandPrimary }}>Here&apos;s 15% off your next delivery. Our team will follow up.</Mono>
            <Display style={s.code} testID="apology-code">{stop.apology_code}</Display>
          </View>
        ) : null}
      </View>
    );
  }

  const submit = async () => {
    setSaving(true);
    try {
      const st = await api<Stop>(`/stops/${stop.id}/rating`, { method: "POST", body: { stars, comment: comment.trim() || null, tags }, headers: { "X-Order-Token": orderToken } });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      onRated(st);
    } catch (e: any) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Animated.View entering={FadeInDown} style={s.box} testID="rating-prompt">
      <Display style={s.title}>HOW WAS YOUR DELIVERY?</Display>
      <View style={s.starsRow}>
        {[1, 2, 3, 4, 5].map((n) => (
          <Pressable
            key={n}
            testID={`rating-star-${n}`}
            hitSlop={4}
            onPress={() => {
              setStars(n);
              setTags((x) => x.filter((t) => tagsFor(n).includes(t)));
              Haptics.selectionAsync().catch(() => {});
            }}
            style={s.star}
          >
            {n <= stars ? (
              <Animated.View entering={ZoomIn}>
                <Ionicons name="star" size={36} color={colors.brandPrimary} />
              </Animated.View>
            ) : (
              <Ionicons name="star-outline" size={36} color={colors.onSurface} />
            )}
          </Pressable>
        ))}
        <Mono style={s.word}>{WORDS[stars]}</Mono>
      </View>
      {stars ? (
        <>
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: spacing.sm }}>
            {tagsFor(stars).map((t) => {
              const on = tags.includes(t);
              return (
                <Pressable key={t} testID={`rating-tag-${t.toLowerCase().replace(/\s+/g, "-")}`} onPress={() => setTags((x) => (on ? x.filter((y) => y !== t) : [...x, t]))} style={[s.chip, on && { backgroundColor: colors.surfaceInverse }]}>
                  <Mono style={[s.chipText, on && { color: colors.onSurfaceInverse }]}>{t}</Mono>
                </Pressable>
              );
            })}
          </View>
          <TextInput
            testID="rating-comment-input"
            value={comment}
            onChangeText={setComment}
            placeholder="Add a comment (optional)"
            placeholderTextColor={colors.muted}
            maxLength={300}
            multiline
            style={s.input}
          />
          <Mono style={s.count}>{comment.length}/300</Mono>
          <Btn testID="rating-submit-button" label="SEND RATING" icon="send" loading={saving} onPress={submit} />
        </>
      ) : null}
    </Animated.View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { gap: spacing.sm, borderWidth: 2, borderColor: c.border, padding: spacing.lg, marginTop: spacing.xs },
  title: { fontSize: 20 },
  starsRow: { flexDirection: "row", alignItems: "center", gap: spacing.xs },
  star: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  word: { marginLeft: spacing.sm, fontSize: 13, fontWeight: "700", color: c.brandPrimary },
  chip: { minHeight: 44, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 11, fontWeight: "700" },
  done: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderWidth: 2, borderColor: c.border, padding: spacing.md },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1 },
  input: { minHeight: 72, borderWidth: 2, borderColor: c.border, padding: spacing.sm, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface, backgroundColor: c.surface, textAlignVertical: "top" },
  count: { fontSize: 11, color: c.muted, alignSelf: "flex-end" },
  apology: { backgroundColor: c.brandPrimary, padding: spacing.md, gap: 4, borderWidth: 2, borderColor: c.border },
  code: { fontSize: 24, color: c.onBrandPrimary, letterSpacing: 2, backgroundColor: c.brandSecondary, alignSelf: "flex-start", paddingHorizontal: spacing.sm },
}));
