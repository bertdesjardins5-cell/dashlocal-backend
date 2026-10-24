// Web preview fallback: schematic projection (react-native-maps is native only).
import React, { useState } from "react";
import { View, Text } from "react-native";
import Svg, { Polyline, Line, Circle } from "react-native-svg";
import { fonts, useTheme } from "@/src/theme";
import type { LatLng } from "@/src/api";

export type MapMarker = LatLng & { id: string; label: string; tone: "red" | "dark" | "green" | "light" };
type Props = { markers: MapMarker[]; path?: LatLng[]; driver?: LatLng | null; height?: number | `${number}%`; geofence?: LatLng | null; testID?: string };

export default function RouteMap({ markers, path = [], driver, height = 260, testID = "route-map" }: Props) {
  const { colors } = useTheme();
  const [size, setSize] = useState({ w: 0, h: 0 });
  const all = [...markers, ...path, ...(driver ? [driver] : [])];
  const lats = all.map((p) => p.lat);
  const lngs = all.map((p) => p.lng);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats), minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
  const pad = 32;
  const proj = (p: LatLng) => ({
    x: pad + ((p.lng - minLng) / (maxLng - minLng || 1)) * (size.w - pad * 2),
    y: pad + (1 - (p.lat - minLat) / (maxLat - minLat || 1)) * (size.h - pad * 2),
  });
  const toneColor = (t: MapMarker["tone"]) =>
    t === "red" ? colors.brandPrimary : t === "green" ? colors.success : t === "dark" ? colors.surfaceInverse : colors.muted;
  const grid = Array.from({ length: 8 }, (_, i) => i);

  return (
    <View testID={testID} style={{ height, backgroundColor: colors.surfaceSecondary, borderBottomWidth: 2, borderColor: colors.border, overflow: "hidden" }} onLayout={(e) => setSize({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height })}>
      {size.w > 0 && all.length ? (
        <>
          <Svg width={size.w} height={size.h} style={{ position: "absolute" }}>
            {grid.map((i) => (
              <Line key={`h${i}`} x1={0} x2={size.w} y1={(size.h / 8) * i} y2={(size.h / 8) * i} stroke={colors.surfaceTertiary} strokeWidth={1} />
            ))}
            {grid.map((i) => (
              <Line key={`v${i}`} y1={0} y2={size.h} x1={(size.w / 8) * i} x2={(size.w / 8) * i} stroke={colors.surfaceTertiary} strokeWidth={1} />
            ))}
            {path.length > 1 ? (
              <Polyline points={path.map((p) => { const q = proj(p); return `${q.x},${q.y}`; }).join(" ")} fill="none" stroke={colors.surfaceInverse} strokeWidth={3} strokeDasharray="8 6" />
            ) : null}
            {driver ? <Circle cx={proj(driver).x} cy={proj(driver).y} r={18} fill={colors.brandTertiary} /> : null}
          </Svg>
          {markers.map((m) => {
            const q = proj(m);
            return (
              <View key={m.id} style={{ position: "absolute", left: q.x - 14, top: q.y - 14, minWidth: 28, height: 28, paddingHorizontal: 4, backgroundColor: toneColor(m.tone), borderWidth: 2, borderColor: colors.surface, alignItems: "center", justifyContent: "center" }}>
                <Text style={{ color: colors.onBrandPrimary, fontFamily: fonts.mono, fontWeight: "700", fontSize: 12 }}>{m.label}</Text>
              </View>
            );
          })}
          {driver ? (
            <View testID="map-driver-marker" style={{ position: "absolute", left: proj(driver).x - 11, top: proj(driver).y - 11, width: 22, height: 22, backgroundColor: colors.brandPrimary, borderWidth: 4, borderColor: colors.surfaceInverse }} />
          ) : null}
        </>
      ) : null}
    </View>
  );
}
