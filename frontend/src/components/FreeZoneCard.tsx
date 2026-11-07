import React from "react";
import { View } from "react-native";
import RouteMap, { MapMarker } from "@/src/components/RouteMap";
import type { Stop, Zone } from "@/src/api";
import { Mono } from "@/src/ui";
import { makeStyles, spacing } from "@/src/theme";

export type FreeZone = Zone & { per_km: number; from: "pharmacy" | "depot" };

const R = 6371;
const kmBetween = (a: Zone | Stop, b: Zone | Stop) => {
  const dLat = ((b.lat - a.lat) * Math.PI) / 180, dLng = ((b.lng - a.lng) * Math.PI) / 180;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
};

// Merchant: map circle of the included-distance zone, with open orders inside (green) or outside (red).
export default function FreeZoneCard({ zone, orders }: { zone: FreeZone; orders: Stop[] }) {
  const s = useStyles();
  const all = orders.filter((o) => (o.status === "pending" || o.status === "arrived") && o.lat != null && o.lng != null);
  const open = [...all].sort((a, b) => kmBetween(zone, b) - kmBetween(zone, a)).slice(0, 30); // farthest first so red pins are never dropped
  // circle is straight-line; billing uses straight line × 1.3 for road distance, so the real free area sits inside it
  const radius = zone.km / 1.3;
  const outside = all.filter((o) => kmBetween(zone, o) > radius).length;
  const markers: MapMarker[] = [
    ...open.map((o, i) => ({ id: o.id, lat: o.lat, lng: o.lng, label: String(i + 1), tone: kmBetween(zone, o) > radius ? ("red" as const) : ("green" as const) })),
    { id: "origin", lat: zone.lat, lng: zone.lng, label: zone.from === "pharmacy" ? "RX" : "HQ", tone: "dark" }, // last = drawn on top
  ];
  return (
    <View testID="free-zone-card">
      <RouteMap testID="free-zone-map" markers={markers} zone={{ lat: zone.lat, lng: zone.lng, km: radius }} height={220} />
      <View style={s.block}>
        <Mono style={s.title}>FIRST {zone.km} KM BY ROAD INCLUDED · THEN CA${zone.per_km.toFixed(2)}/KM</Mono>
        <Mono style={s.meta}>
          Measured from {zone.from === "pharmacy" ? "your pharmacy pickup (RX)" : "the DashLocal depot (HQ)"}. Addresses inside the circle have no extra distance charge.
        </Mono>
        {all.length ? (
          <Mono style={s.meta} testID="free-zone-count">
            {all.length - outside} OPEN ORDER{all.length - outside === 1 ? "" : "S"} INSIDE (GREEN) · {outside} OUTSIDE (RED, EXTRA DISTANCE APPLIES)
          </Mono>
        ) : null}
      </View>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.xs, borderBottomWidth: 2, borderColor: c.border },
  title: { fontSize: 13, fontWeight: "700" },
  meta: { fontSize: 11, color: c.muted },
}));
