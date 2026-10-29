import React, { useState } from "react";
import { View } from "react-native";
import { api } from "@/src/api";
import { money } from "@/src/components/BillCard";
import SquarePaySheet from "@/src/components/SquarePaySheet";
import { Btn, Display, Mono } from "@/src/ui";
import { makeStyles, spacing } from "@/src/theme";

// After delivery: the verified customer can tip their driver ($2 / $3 / $5 / custom, picked on Square's secure page).
export default function TipDriver({ code, driver, tipsTotal, orderToken, onTipped, onError }: {
  code: string; driver?: string | null; tipsTotal: number; orderToken: string; onTipped: (amount: number) => void; onError: (m: string) => void;
}) {
  const s = useStyles();
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const who = driver ? driver.split(" ")[0] : "your driver";

  const start = async () => {
    setBusy(true);
    try {
      setUrl((await api<{ url: string }>(`/track/${code}/tip-link`, { method: "POST", headers: { "X-Order-Token": orderToken } })).url);
    } catch (e: any) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.box} testID="tip-driver">
      <Display style={s.title}>{tipsTotal > 0 ? `THANKS FOR TIPPING ${who.toUpperCase()}` : `TIP ${who.toUpperCase()}`}</Display>
      <Mono style={s.meta} testID="tip-driver-total">
        {tipsTotal > 0 ? `YOU TIPPED ${money(tipsTotal)} · 100% GOES TO ${who.toUpperCase()}` : "$2, $3, $5 OR YOUR OWN AMOUNT · 100% GOES TO YOUR DRIVER · NO HST"}
      </Mono>
      <Btn testID="tip-driver-button" label={tipsTotal > 0 ? "ADD ANOTHER TIP" : "TIP YOUR DRIVER"} icon="heart" variant={tipsTotal > 0 ? "outline" : "primary"} loading={busy} onPress={start} />
      <SquarePaySheet url={url} title={`TIP · ORDER ${code}`} onClose={() => setUrl(null)} onDone={(r) => {
        setUrl(null);
        onTipped(Number(r.amount) || 0);
      }} />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { borderWidth: 2, borderColor: c.border, padding: spacing.md, gap: spacing.sm },
  title: { fontSize: 18 },
  meta: { fontSize: 12, color: c.muted },
}));
