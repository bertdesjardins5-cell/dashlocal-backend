import React, { useState } from "react";
import { TextInput, View } from "react-native";
import { api, fmtDepotTime, RouteDetail } from "@/src/api";
import { Btn, Display, Mono, Pill } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

type Pickup = NonNullable<RouteDetail["pickups"]>[number];

// Driver: confirm each pharmacy pickup (time + parcel count) so the pharmacy sees its batch leave.
export default function PickupCheckIn({ routeId, pickups, onDone, notify }: {
  routeId: string; pickups: Pickup[]; onDone: () => void; notify: (m: string, tone?: "dark" | "red" | "green") => void;
}) {
  const s = useStyles();
  const { colors } = useTheme();
  const [open, setOpen] = useState<string | null>(null);
  const [count, setCount] = useState("");
  const [busy, setBusy] = useState(false);
  if (!pickups.length) return null;

  const confirm = async (p: Pickup) => {
    const n = parseInt(count, 10);
    if (!(n >= 0)) return notify("ENTER HOW MANY PARCELS YOU COLLECTED", "red");
    setBusy(true);
    try {
      await api(`/routes/${routeId}/pickups/${encodeURIComponent(p.manifest_id)}/check-in`, { method: "POST", body: { parcel_count: n } });
      notify(n === p.drops ? "PICKUP CHECKED IN" : `PICKUP CHECKED IN · ${n} OF ${p.drops} PARCELS`, n === p.drops ? "green" : "red");
      setOpen(null);
      onDone();
    } catch (e: any) {
      notify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View testID="pickup-checkin-card">
      <Mono style={s.section}>PHARMACY PICKUPS</Mono>
      {pickups.map((p) => (
        <View key={p.manifest_id} style={s.row} testID={`pickup-${p.run}`}>
          <View style={s.top}>
            <View style={{ flex: 1, gap: 2 }}>
              <Display style={{ fontSize: 17 }}>{(p.pharmacy ?? "PHARMACY").toUpperCase()}</Display>
              <Mono style={s.meta}>{p.label.toUpperCase()} · PICKUP {p.pickup_label} · {p.drops} PARCELS</Mono>
              {p.pickup_address ? <Mono style={s.meta}>{p.pickup_address}</Mono> : null}
            </View>
            {p.picked_up_at ? <Pill label={p.on_time ? "PICKED UP" : "PICKED UP LATE"} tone={p.on_time ? "green" : "yellow"} /> : null}
          </View>
          {p.picked_up_at ? (
            <Mono style={s.meta} testID={`pickup-${p.run}-done`}>{fmtDepotTime(p.picked_up_at)} · {p.parcel_count} parcel{p.parcel_count === 1 ? "" : "s"} collected</Mono>
          ) : open === p.manifest_id ? (
            <View style={{ gap: spacing.sm }}>
              <Mono style={s.meta}>HOW MANY PARCELS DID YOU COLLECT?</Mono>
              <View style={s.top}>
                <TextInput testID="pickup-count-input" value={count} onChangeText={setCount} keyboardType="number-pad" style={s.input} placeholderTextColor={colors.muted} />
                <Btn testID="pickup-confirm-button" label="CONFIRM" loading={busy} onPress={() => confirm(p)} style={{ flex: 1 }} />
              </View>
            </View>
          ) : (
            <Btn testID={`pickup-${p.run}-button`} label="PICKED UP" icon="cube" variant="dark" onPress={() => { setOpen(p.manifest_id); setCount(String(p.drops)); }} />
          )}
        </View>
      ))}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  section: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 2, borderBottomWidth: 2, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  row: { padding: spacing.lg, gap: spacing.sm, borderBottomWidth: 2, borderColor: c.border },
  top: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  meta: { fontSize: 12, color: c.muted },
  input: { width: 90, height: 52, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 18, color: c.onSurface },
}));
