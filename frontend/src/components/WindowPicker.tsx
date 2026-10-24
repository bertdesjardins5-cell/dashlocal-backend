import React from "react";
import { Pressable, ScrollView } from "react-native";
import { Mono } from "@/src/ui";
import { DEPOT_TZ } from "@/src/api";
import { makeStyles, spacing, useTheme } from "@/src/theme";

function depotNowHHMM(): string {
  try {
    return new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: DEPOT_TZ });
  } catch {
    return "00:00";
  }
}

// Two-hour delivery slots in depot local time; "ANY" clears the window.
export const SLOTS: { label: string; start: string | null; end: string | null }[] = [
  { label: "ANY TIME", start: null, end: null },
  ...[8, 10, 12, 14, 16, 18, 20].map((h) => ({
    label: `${String(h).padStart(2, "0")}–${String(h + 2).padStart(2, "0")}`,
    start: `${String(h).padStart(2, "0")}:00`,
    end: `${String(h + 2).padStart(2, "0")}:00`,
  })),
];

type Props = { start: string | null; end: string | null; onSelect: (start: string | null, end: string | null) => void; disabled?: boolean; testIDPrefix?: string };

export default function WindowPicker({ start, end, onSelect, disabled, testIDPrefix = "window" }: Props) {
  const s = useStyles();
  const { colors } = useTheme();
  const nowHHMM = depotNowHHMM();
  const isCustom = !!start && !SLOTS.some((x) => x.start === start && x.end === end);
  const slots = isCustom ? [...SLOTS, { label: `${start}–${end}`, start, end }] : SLOTS;
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={s.row} contentContainerStyle={s.content}>
      {slots.map((x) => {
        const sel = x.start === start && x.end === end;
        const past = !!x.end && x.end <= nowHHMM && !sel;
        return (
          <Pressable
            key={x.label}
            testID={`${testIDPrefix}-slot-${x.start ? x.start.replace(":", "") : "any"}`}
            disabled={disabled || past}
            hitSlop={{ top: 4, bottom: 4 }}
            onPress={() => onSelect(x.start, x.end)}
            style={[s.chip, sel && { backgroundColor: colors.surfaceInverse }, (disabled || past) && { opacity: 0.35 }]}
          >
            <Mono style={[s.text, sel && { color: colors.onSurfaceInverse }]}>{x.label}</Mono>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

const useStyles = makeStyles((c) => ({
  row: { height: 56, flexGrow: 0 },
  content: { gap: spacing.sm, alignItems: "center", paddingHorizontal: spacing.lg },
  chip: { height: 36, flexShrink: 0, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center", backgroundColor: c.surface },
  text: { fontSize: 12, fontWeight: "700" },
}));
