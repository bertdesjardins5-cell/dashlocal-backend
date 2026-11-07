import React, { useState } from "react";
import { TextInput, View } from "react-native";
import RouteMap, { MapMarker } from "@/src/components/RouteMap";
import { api, type Stop, type Zone } from "@/src/api";
import { Btn, Mono } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

export type FreeZone = Zone & { per_km: number; from: "pharmacy" | "store" | "depot"; address: string | null };

const R = 6371;
const kmBetween = (a: Zone | Stop, b: Zone | Stop) => {
  const dLat = ((b.lat - a.lat) * Math.PI) / 180, dLng = ((b.lng - a.lng) * Math.PI) / 180;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
};
const PIN = { pharmacy: "RX", store: "ST", depot: "HQ" } as const;

// Merchant: map circle of the included-distance zone around the store's pickup address (or the depot until one is saved),
// open orders inside (green) or outside (red), and the store's own pickup address editor.
export default function FreeZoneCard({ merchantId, zone, orders, onSaved, notify }: {
  merchantId: string; zone: FreeZone; orders: Stop[]; onSaved: () => void; notify: (m: string, tone?: "dark" | "red" | "green") => void;
}) {
  const s = useStyles();
  const { colors } = useTheme();
  const [addr, setAddr] = useState(zone.address ?? "");
  const [busy, setBusy] = useState(false);
  const all = orders.filter((o) => (o.status === "pending" || o.status === "arrived") && o.lat != null && o.lng != null);
  const open = [...all].sort((a, b) => kmBetween(zone, b) - kmBetween(zone, a)).slice(0, 30); // farthest first so red pins are never dropped
  // circle is straight-line; billing uses straight line × 1.3 for road distance, so the real free area sits inside it
  const radius = zone.km / 1.3;
  const outside = all.filter((o) => kmBetween(zone, o) > radius).length;
  const markers: MapMarker[] = [
    ...open.map((o, i) => ({ id: o.id, lat: o.lat, lng: o.lng, label: String(i + 1), tone: kmBetween(zone, o) > radius ? ("red" as const) : ("green" as const) })),
    { id: "origin", lat: zone.lat, lng: zone.lng, label: PIN[zone.from], tone: "dark" }, // last = drawn on top
  ];

  const save = async () => {
    setBusy(true);
    try {
      await api(`/merchants/${merchantId}/pickup-address`, { method: "PUT", body: { address: addr.trim() } });
      notify("PICKUP ADDRESS SAVED · NEW ORDERS ARE PRICED FROM HERE", "green");
      onSaved();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View testID="free-zone-card">
      <RouteMap testID="free-zone-map" markers={markers} zone={{ lat: zone.lat, lng: zone.lng, km: radius }} height={220} />
      <View style={s.block}>
        <Mono style={s.title}>FIRST {zone.km} KM BY ROAD INCLUDED · THEN CA${zone.per_km.toFixed(2)}/KM</Mono>
        <Mono style={s.meta} testID="free-zone-origin">
          {zone.from === "depot"
            ? "Measured from the DashLocal depot (HQ) until you save your store's pickup address below."
            : `Measured from your ${zone.from === "pharmacy" ? "pharmacy" : "store"} pickup (${PIN[zone.from]}): ${zone.address}.`} Addresses inside the circle have no extra distance charge.
        </Mono>
        {all.length ? (
          <Mono style={s.meta} testID="free-zone-count">
            {all.length - outside} OPEN ORDER{all.length - outside === 1 ? "" : "S"} INSIDE (GREEN) · {outside} OUTSIDE (RED, EXTRA DISTANCE APPLIES)
          </Mono>
        ) : null}
        <Mono style={[s.label, { marginTop: spacing.sm }]}>STORE PICKUP ADDRESS</Mono>
        <View style={s.row}>
          <TextInput testID="free-zone-address-input" value={addr} onChangeText={setAddr} placeholder="e.g. 200 King St W, Kitchener"
            placeholderTextColor={colors.muted} style={s.input} autoCapitalize="words" />
          <Btn testID="free-zone-address-save" label="SAVE" loading={busy} disabled={addr.trim().length < 4 || addr.trim() === (zone.address ?? "")} onPress={save} />
        </View>
        <Mono style={s.meta}>Changing it re-prices new orders only. Orders already placed keep their fee.</Mono>
      </View>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.xs, borderBottomWidth: 2, borderColor: c.border },
  title: { fontSize: 13, fontWeight: "700" },
  meta: { fontSize: 11, color: c.muted },
  label: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.muted },
  row: { flexDirection: "row", gap: spacing.sm, alignItems: "center" },
  input: { flex: 1, height: 48, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
}));
