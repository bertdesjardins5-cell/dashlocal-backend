import React, { useState } from "react";
import { Linking, Platform, Pressable, ScrollView, View } from "react-native";
import { api, BACKEND_URL } from "@/src/api";
import { Btn, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

const RANGES = [
  { k: "today", label: "TODAY" },
  { k: "7d", label: "7 DAYS" },
  { k: "30d", label: "30 DAYS" },
  { k: "all", label: "ALL" },
];

// Merchant: download a CSV of their drops (status, times, proof links, ratings) for a date range.
export default function ReportDownload({ merchantId, notify }: { merchantId: string; notify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [range, setRange] = useState("7d");
  const [busy, setBusy] = useState(false);

  const download = async () => {
    // Safari blocks window.open after an await, so open the tab synchronously inside the tap
    const popup = Platform.OS === "web" ? window.open("", "_blank") : null;
    setBusy(true);
    try {
      const r = await api<{ path: string }>(`/merchants/${merchantId}/report-link`, { method: "POST", body: { range } });
      const url = `${BACKEND_URL}${r.path}`;
      if (Platform.OS === "web") {
        if (popup) popup.location.href = url;
        else window.location.href = url; // popup blocked: download in this tab (CSV doesn't navigate away)
      } else await Linking.openURL(url);
      notify("REPORT DOWNLOADING", "green");
    } catch (e: any) {
      popup?.close();
      notify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.block} testID="merchant-report">
      <Mono style={s.meta}>Status, delivery times, on-time, proof-of-delivery links (valid 30 days) and customer ratings. Opens in your spreadsheet app.</Mono>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0 }} contentContainerStyle={{ gap: spacing.sm }}>
        {RANGES.map((r) => (
          <Pressable key={r.k} testID={`report-range-${r.k}`} onPress={() => setRange(r.k)} style={[s.chip, range === r.k && { backgroundColor: colors.surfaceInverse }]}>
            <Mono style={[s.chipText, range === r.k && { color: colors.onSurfaceInverse }]}>{r.label}</Mono>
          </Pressable>
        ))}
      </ScrollView>
      <Btn testID="report-download-button" label="DOWNLOAD CSV" icon="document-text" variant="dark" loading={busy} onPress={download} />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  block: { padding: spacing.lg, gap: spacing.md, borderBottomWidth: 2, borderColor: c.border },
  meta: { fontSize: 11, color: c.muted },
  chip: { height: 44, flexShrink: 0, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 12, fontWeight: "700" },
}));
