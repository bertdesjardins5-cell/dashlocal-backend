import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, Text, TextProps, View, StyleProp, ViewStyle } from "react-native";
import Animated, { FadeInDown, FadeOutUp } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

export function Display({ style, ...p }: TextProps) {
  const s = useStyles();
  return <Text {...p} style={[s.display, style]} />;
}
export function Mono({ style, ...p }: TextProps) {
  const s = useStyles();
  return <Text {...p} style={[s.mono, style]} />;
}

type BtnProps = {
  label: string;
  onPress: () => void;
  variant?: "primary" | "dark" | "outline" | "success";
  icon?: React.ComponentProps<typeof Ionicons>["name"];
  loading?: boolean;
  disabled?: boolean;
  testID: string;
  style?: StyleProp<ViewStyle>;
};
export function Btn({ label, onPress, variant = "primary", icon, loading, disabled, testID, style }: BtnProps) {
  const s = useStyles();
  const { colors } = useTheme();
  const bg = { primary: colors.brandPrimary, dark: colors.surfaceInverse, outline: colors.surface, success: colors.success }[variant];
  const fg = variant === "outline" ? colors.onSurface : colors.onBrandPrimary;
  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [s.btn, { backgroundColor: bg, opacity: disabled ? 0.4 : pressed ? 0.8 : 1, transform: [{ scale: pressed ? 0.98 : 1 }] }, style]}
    >
      {loading ? (
        <ActivityIndicator color={fg} />
      ) : (
        <>
          {icon ? <Ionicons name={icon} size={20} color={fg} /> : null}
          <Text style={[s.btnText, { color: fg }]}>{label}</Text>
        </>
      )}
    </Pressable>
  );
}

export function Header({ title, subtitle, right, testID }: { title: string; subtitle?: string; right?: React.ReactNode; testID?: string }) {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { colors } = useTheme();
  return (
    <View style={[s.header, { paddingTop: insets.top + spacing.sm }]} testID={testID}>
      <Pressable testID="header-back-button" onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))} style={s.back} hitSlop={8}>
        <Ionicons name="arrow-back" size={24} color={colors.onSurfaceInverse} />
      </Pressable>
      <View style={{ flex: 1 }}>
        <Display style={s.headerTitle} numberOfLines={1}>{title}</Display>
        {subtitle ? <Mono style={s.headerSub} numberOfLines={1}>{subtitle}</Mono> : null}
      </View>
      {right}
    </View>
  );
}

export function Loading({ testID = "loading" }: { testID?: string }) {
  const { colors } = useTheme();
  return (
    <View testID={testID} style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl }}>
      <ActivityIndicator size="large" color={colors.onSurface} />
    </View>
  );
}

export function ErrorBlock({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const s = useStyles();
  return (
    <View style={s.errorBlock} testID="error-block">
      <Mono style={s.errorText}>ERR: {message}</Mono>
      {onRetry ? <Btn testID="error-retry-button" label="RETRY" variant="primary" onPress={onRetry} /> : null}
    </View>
  );
}

export function Pill({ label, tone = "dark" }: { label: string; tone?: "dark" | "red" | "green" | "yellow" | "light" }) {
  const { colors } = useTheme();
  const map = {
    dark: [colors.surfaceInverse, colors.onSurfaceInverse],
    red: [colors.brandPrimary, colors.onBrandPrimary],
    green: [colors.success, colors.onSuccess],
    yellow: [colors.warning, colors.onWarning],
    light: [colors.surfaceTertiary, colors.onSurfaceTertiary],
  }[tone];
  return (
    <View style={{ backgroundColor: map[0], paddingHorizontal: spacing.sm, paddingVertical: 2, alignSelf: "flex-start" }}>
      <Text style={{ color: map[1], fontFamily: fonts.mono, fontSize: 11, fontWeight: "700", letterSpacing: 0.5 }}>{label.toUpperCase()}</Text>
    </View>
  );
}

export const statusTone = (st: string) =>
  (({ delivered: "green", failed: "red", arrived: "yellow", pending: "light", active: "red", planned: "light", completed: "green" }) as const)[
    st as "pending"
  ] ?? "light";

export function useToast() {
  const [msg, setMsg] = useState<{ text: string; tone: "dark" | "red" | "green" } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const show = useCallback((text: string, tone: "dark" | "red" | "green" = "dark") => {
    setMsg({ text, tone });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setMsg(null), 3200);
  }, []);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const node = msg ? <ToastView text={msg.text} tone={msg.tone} /> : null;
  return { show, node };
}

function ToastView({ text, tone }: { text: string; tone: "dark" | "red" | "green" }) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const bg = tone === "red" ? colors.brandPrimary : tone === "green" ? colors.success : colors.surfaceInverse;
  return (
    <Animated.View
      entering={FadeInDown}
      exiting={FadeOutUp}
      testID="toast"
      style={{ position: "absolute", left: spacing.lg, right: spacing.lg, top: insets.top + 72, backgroundColor: bg, padding: spacing.lg, borderWidth: 2, borderColor: colors.border }}
    >
      <Text style={{ color: colors.onBrandPrimary, fontFamily: fonts.mono, fontWeight: "700", fontSize: 14 }}>{text}</Text>
    </Animated.View>
  );
}

const useStyles = makeStyles((c) => ({
  display: { fontFamily: fonts.display, color: c.onSurface, fontWeight: "700" },
  mono: { fontFamily: fonts.mono, color: c.onSurface },
  btn: {
    minHeight: 56,
    paddingHorizontal: spacing.lg,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    borderWidth: 2,
    borderColor: c.border,
  },
  btnText: { fontFamily: fonts.display, fontWeight: "700", fontSize: 16, letterSpacing: 1 },
  header: {
    backgroundColor: c.surfaceInverse,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.md,
  },
  back: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.onSurfaceInverse },
  headerTitle: { color: c.onSurfaceInverse, fontSize: 22, letterSpacing: -0.5 },
  headerSub: { color: c.onSurfaceInverse, fontSize: 12, opacity: 0.8 },
  errorBlock: { backgroundColor: c.surfaceInverse, padding: spacing.lg, gap: spacing.md, margin: spacing.lg },
  errorText: { color: c.onSurfaceInverse, fontSize: 13 },
}));
