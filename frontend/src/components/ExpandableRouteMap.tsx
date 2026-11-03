import React, { ComponentProps, useState } from "react";
import { Modal, Pressable, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import RouteMap from "@/src/components/RouteMap";
import { Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Props = ComponentProps<typeof RouteMap> & { title?: string };

// Route map with a fullscreen button. Fullscreen supports pinch-to-zoom and panning on iPhone and Android.
export default function ExpandableRouteMap({ title = "ROUTE MAP", testID = "route-map", ...map }: Props) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [full, setFull] = useState(false);

  return (
    <View>
      <RouteMap {...map} testID={testID} />
      <Pressable testID={`${testID}-expand`} accessibilityLabel="Open map fullscreen" onPress={() => setFull(true)} style={s.expand}>
        <Ionicons name="expand" size={22} color={colors.onSurfaceInverse} />
      </Pressable>
      {full ? (
        <Modal visible animationType="slide" onRequestClose={() => setFull(false)} statusBarTranslucent>
          <View style={[s.full, { paddingTop: insets.top }]} testID={`${testID}-fullscreen`}>
            <View style={s.bar}>
              <Mono style={s.title} numberOfLines={1}>{title} · PINCH TO ZOOM</Mono>
              <Pressable testID={`${testID}-fullscreen-close`} accessibilityLabel="Close fullscreen map" onPress={() => setFull(false)} style={s.close}>
                <Ionicons name="close" size={26} color={colors.onSurfaceInverse} />
              </Pressable>
            </View>
            <View style={{ flex: 1, paddingBottom: insets.bottom }}>
              <RouteMap {...map} height="100%" testID={`${testID}-full`} />
            </View>
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
}));
