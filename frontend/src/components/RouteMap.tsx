import React, { useEffect, useRef } from "react";
import { View, Text } from "react-native";
import MapView, { Marker, Polyline, Circle } from "react-native-maps";
import { fonts, useTheme } from "@/src/theme";
import type { LatLng } from "@/src/api";

export type MapMarker = LatLng & { id: string; label: string; tone: "red" | "dark" | "green" | "light" };

type Props = { markers: MapMarker[]; path?: LatLng[]; driver?: LatLng | null; height?: number | `${number}%`; geofence?: LatLng | null; testID?: string;
  onMarkerPress?: (id: string) => void };

export default function RouteMap({ markers, path = [], driver, height = 260, geofence, testID = "route-map", onMarkerPress }: Props) {
  const { colors } = useTheme();
  const ref = useRef<MapView>(null);
  const all = [...markers, ...(driver ? [driver] : [])];
  const key = all.map((p) => `${p.lat.toFixed(3)},${p.lng.toFixed(3)}`).join("|");

  useEffect(() => {
    if (!all.length) return;
    ref.current?.fitToCoordinates(all.map((p) => ({ latitude: p.lat, longitude: p.lng })), {
      edgePadding: { top: 60, bottom: 60, left: 40, right: 40 },
      animated: true,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const toneColor = (t: MapMarker["tone"]) =>
    t === "red" ? colors.brandPrimary : t === "green" ? colors.success : t === "dark" ? colors.surfaceInverse : colors.muted;

  const first = all[0] ?? { lat: 37.7749, lng: -122.4194 };
  return (
    <View style={{ height, borderBottomWidth: 2, borderColor: colors.border }} testID={testID}>
      <MapView
        ref={ref}
        style={{ flex: 1 }}
        initialRegion={{ latitude: first.lat, longitude: first.lng, latitudeDelta: 0.05, longitudeDelta: 0.05 }}
      >
        {path.length > 1 ? (
          <Polyline coordinates={path.map((p) => ({ latitude: p.lat, longitude: p.lng }))} strokeColor={colors.surfaceInverse} strokeWidth={4} lineDashPattern={[8, 6]} />
        ) : null}
        {geofence ? (
          <Circle center={{ latitude: geofence.lat, longitude: geofence.lng }} radius={50} strokeColor={colors.brandPrimary} fillColor={colors.brandTertiary} strokeWidth={2} />
        ) : null}
        {markers.map((m) => (
          <Marker key={m.id} coordinate={{ latitude: m.lat, longitude: m.lng }} anchor={{ x: 0.5, y: 0.5 }} onPress={() => onMarkerPress?.(m.id)}>
            <View style={{ minWidth: 28, height: 28, paddingHorizontal: 4, backgroundColor: toneColor(m.tone), borderWidth: 2, borderColor: colors.surface, alignItems: "center", justifyContent: "center" }}>
              <Text style={{ color: colors.onBrandPrimary, fontFamily: fonts.mono, fontWeight: "700", fontSize: 12 }}>{m.label}</Text>
            </View>
          </Marker>
        ))}
        {driver ? (
          <Marker coordinate={{ latitude: driver.lat, longitude: driver.lng }} anchor={{ x: 0.5, y: 0.5 }}>
            <View style={{ width: 22, height: 22, backgroundColor: colors.brandPrimary, borderWidth: 4, borderColor: colors.surfaceInverse }} />
          </Marker>
        ) : null}
      </MapView>
    </View>
  );
}
