import React, { useEffect, useState } from "react";
import { View } from "react-native";
import { fmtDepotTime } from "@/src/api";
import { Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Run = {
  key: string; drops_today: number; cutoff_at: string; today: boolean;
  pickup: { date?: string; picked_up_at: string; parcel_count: number; expected: number; on_time: boolean } | null;
};

const left = (ms: number) => {
  const m = Math.max(0, Math.round(ms / 60000));
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m} min`;
};

// Pharmacy portal: live "drops to batch rate" progress, cut-off countdown and the driver's pickup check-in.
export default function BatchRateTracker({ run, minDrops, inverse }: { run: Run; minDrops: number; inverse?: boolean }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);
  const n = run.drops_today;
  const pct = Math.min(1, n / minDrops);
  const ms = new Date(run.cutoff_at).getTime() - now;
  const fg = inverse ? colors.onSurfaceInverse : colors.onSurface;
  const reached = n >= minDrops;
  return (
    <View style={{ gap: 4, marginTop: spacing.xs }} testID={`batch-tracker-${run.key}`}>
      <View style={[s.bar, { borderColor: fg }]}>
        <View style={{ width: `${pct * 100}%`, height: "100%", backgroundColor: reached ? colors.brandForest : colors.brandPrimary }} />
      </View>
      <Mono style={[s.text, { color: fg }]} testID={`batch-tracker-${run.key}-text`}>
        {reached ? `BATCH RATE REACHED · ${n}/${minDrops}` : `${minDrops - n} TO BATCH RATE · ${n}/${minDrops}`}
        {run.pickup ? "" : ms > 0 ? ` · CUT-OFF IN ${left(ms)}` : " · CUT-OFF PASSED"}
      </Mono>
      {run.pickup ? (
        <Mono style={[s.text, { color: run.pickup.on_time ? colors.brandForest : colors.brandPrimary }]} testID={`batch-tracker-${run.key}-pickup`}>
          {run.pickup.date && run.pickup.date !== run.cutoff_at.slice(0, 10) ? "TODAY'S RUN " : ""}PICKED UP {fmtDepotTime(run.pickup.picked_up_at)} · {run.pickup.parcel_count}/{run.pickup.expected} PARCEL{run.pickup.expected === 1 ? "" : "S"} · {run.pickup.on_time ? "ON TIME" : "LATE"}
        </Mono>
      ) : null}
    </View>
  );
}

const useStyles = makeStyles(() => ({
  bar: { height: 10, borderWidth: 2, overflow: "hidden" },
  text: { fontSize: 11, fontWeight: "700", letterSpacing: 0.5 },
}));
