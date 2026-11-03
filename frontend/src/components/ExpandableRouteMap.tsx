import React, { ComponentProps, useState } from "react";
import { Modal, Pressable, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import RouteMap from "@/src/components/RouteMap";
import { Display, Mono, Pill, statusTone } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

export type StopDetail = { title: string; address: string; status: string; extra?: string };
type Props = ComponentProps<typeof RouteMap> & { title?: string; details?: Record<string, StopDetail> };

// Route map with a fullscreen button. Fullscreen supports pinch-to-zoom; tapping a numbered stop shows its details.
export default function ExpandableRouteMap({ title = "ROUTE MAP", testID = "route-map", details, ...map }: Props) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [full, setFull] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const d = sel && details ? details[sel] : null;
  const label = sel ? map.markers.find((m) => m.id === sel)?.label : null;
  const close = () => { setFull(false); setSel(null); };

  return (
    <View>
      <RouteMap {...map} testID={testID} />
      <Pressable testID={`${testID}-expand`} accessibilityLabel="Open map fullscreen" onPress={() => setFull(true)} style={s.expand}>
        <Ionicons name="expand" size={22} color={colors.onSurfaceInverse} />
      </Pressable>
      {full ? (
        <Modal visible animationType="slide" onRequestClose={close} statusBarTranslucent>
          <View style={[s.full, { paddingTop: insets.top }]} testID={`${testID}-fullscreen`}>
            <View style={s.bar}>
              <Mono style={s.title} numberOfLines={1}>{title} · {details ? "TAP A STOP" : "PINCH TO ZOOM"}</Mono>
              <Pressable testID={`${testID}-fullscreen-close`} accessibilityLabel="Close fullscreen map" onPress={close} style={s.close}>
                <Ionicons name="close" size={26} color={colors.onSurfaceInverse} />
              </Pressable>
            </View>
            <View style={{ flex: 1 }}>
              <RouteMap {...map} height="100%" testID={`${testID}-full`} onMarkerPress={details ? setSel : undefined} />
            </View>
            {d ? (
              <View style={[s.card, { paddingBottom: insets.bottom + spacing.md }]} testID="map-stop-details">
                <View style={s.row}>
                  <Display style={{ fontSize: 20, flex: 1 }} numberOfLines={1}>{label ? `${label}. ` : ""}{d.title}</Display>
                  <Pressable testID="map-stop-details-close" accessibilityLabel="Hide stop details" onPress={() => setSel(null)} style={s.cardClose}>
                    <Ionicons name="close" size={22} color={colors.onSurface} />
                  </Pressable>
                </View>
                <Mono style={s.addr} testID="map-stop-details-address">{d.address}</Mono>
                <View style={s.row}>
                  <Pill label={d.status} tone={statusTone(d.status)} />
                  {d.extra ? <Mono style={s.meta}>{d.extra}</Mono> : null}
                </View>
              </View>
            ) : <View style={{ height: insets.bottom }} />}
          </View>
        </Modal>
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  expand: { position: "absolute", right: spacing.sm, bottom: spacing.sm, width: 44, height: 44, alignItems: "center", justifyContent: "center", backgroundColor: c.surfaceInverse },
  full: { flex: 1, backgroundColor: c.surfaceInverse },
  bar: { flexDirection: "row", alignItems: "center", paddingLeft: spacing.md, minHeight: 56 },
  title: { flex: 1, color: c.onSurfaceInverse, fontSize: 13, fontWeight: "700", letterSpacing: 1 },
  close: { width: 56, height: 56, alignItems: "center", justifyContent: "center" },
  card: { position: "absolute", left: 0, right: 0, bottom: 0, backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.md, gap: spacing.sm },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  cardClose: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  addr: { fontSize: 14, color: c.onSurface },
  meta: { fontSize: 12, color: c.muted, flexShrink: 1 },
}));
