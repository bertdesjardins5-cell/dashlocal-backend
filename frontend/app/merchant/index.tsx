import React from "react";
import { FlatList, Pressable, RefreshControl, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api } from "@/src/api";
import { Display, ErrorBlock, Header, Loading, Mono, Pill } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type MerchantRow = { id: string; name: string; contact: string; orders: number; delivered: number; flagged: number };

export default function MerchantSelect() {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["merchants"], queryFn: () => api<MerchantRow[]>("/merchants") });

  return (
    <View style={s.root} testID="merchant-select-screen">
      <Header title="MERCHANTS" subtitle="Select your store" />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : (
        <FlatList
          data={q.data}
          keyExtractor={(m) => m.id}
          contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); }} />}
          renderItem={({ item }) => (
            <Pressable testID={`merchant-select-${item.id}`} onPress={() => router.push(`/merchant/${item.id}`)} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }]}>
              <View style={s.avatar}><Ionicons name="storefront" size={24} color={colors.onSurfaceInverse} /></View>
              <View style={{ flex: 1, gap: spacing.xs }}>
                <Display style={s.name}>{item.name}</Display>
                <Mono style={s.meta}>{item.orders} ORDERS · {item.delivered} DELIVERED</Mono>
                {item.flagged ? <Pill label={`${item.flagged} to fix`} tone="red" /> : null}
              </View>
              <Ionicons name="chevron-forward" size={24} color={colors.onSurface} />
            </Pressable>
          )}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.lg, padding: spacing.lg, borderBottomWidth: 2, borderColor: c.border, minHeight: 88 },
  avatar: { width: 52, height: 52, backgroundColor: c.surfaceInverse, alignItems: "center", justifyContent: "center" },
  name: { fontSize: 20 },
  meta: { fontSize: 11, color: c.muted },
}));
