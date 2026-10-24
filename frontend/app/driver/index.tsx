import React from "react";
import { FlatList, Pressable, RefreshControl, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, Driver } from "@/src/api";
import { Display, ErrorBlock, Header, Loading, Mono, Pill, statusTone } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type DriverRow = Driver & { route: { id: string; name: string; status: string } | null };

export default function DriverSelect() {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const q = useQuery({ queryKey: ["drivers"], queryFn: () => api<DriverRow[]>("/drivers") });

  return (
    <View style={s.root}>
      <Header title="WHO'S DRIVING?" subtitle="Select your driver profile" />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBlock message={(q.error as Error).message} onRetry={q.refetch} /> : (
        <FlatList
          data={q.data}
          keyExtractor={(d) => d.id}
          contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); }} />}
          renderItem={({ item }) => (
            <Pressable testID={`driver-select-${item.id}`} onPress={() => router.push(`/driver/${item.id}`)} style={({ pressed }) => [s.row, pressed && { backgroundColor: colors.surfaceTertiary }]}>
              <View style={s.avatar}><Display style={s.avatarText}>{item.name.split(" ").map((p) => p[0]).join("")}</Display></View>
              <View style={{ flex: 1, gap: spacing.xs }}>
                <Display style={s.name}>{item.name}</Display>
                <Mono style={s.meta}>{item.vehicle} · {item.route ? item.route.name : "NO ROUTE ASSIGNED"}</Mono>
                {item.route ? <Pill label={item.route.status} tone={statusTone(item.route.status)} /> : null}
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
  avatarText: { color: c.onSurfaceInverse, fontSize: 18 },
  name: { fontSize: 20 },
  meta: { fontSize: 12, color: c.muted },
}));
