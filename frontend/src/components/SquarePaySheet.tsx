import React, { useEffect } from "react";
import { Modal, Platform, Pressable, View } from "react-native";
import { WebView } from "react-native-webview";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { Display, Loading, Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

export type SquareResult = Record<string, any> & { kind: "card" | "invoices" | "order"; status: string };

// Square's secure card form (hosted by our backend with the Square Web Payments SDK). Card numbers go straight to Square;
// the page tells us when the charge / saved card succeeded.
export default function SquarePaySheet({ url, title, onClose, onDone }: { url: string | null; title: string; onClose: () => void; onDone: (r: SquareResult) => void }) {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const handle = (m: any) => {
    if (m && m.type === "dashlocal-square" && m.ok) onDone(m.result);
  };

  useEffect(() => {
    if (Platform.OS !== "web" || !url) return;
    const origin = new URL(url).origin;
    const fn = (e: MessageEvent) => e.origin === origin && handle(e.data);
    window.addEventListener("message", fn);
    return () => window.removeEventListener("message", fn);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  return (
    <Modal visible={!!url} animationType="slide" onRequestClose={onClose} presentationStyle="fullScreen">
      <View style={[s.root, { paddingTop: insets.top }]} testID="square-pay-sheet">
        <View style={s.head}>
          <View style={{ flex: 1, gap: 2 }}>
            <Display style={s.title} numberOfLines={1}>{title}</Display>
            <Mono style={s.sub}>SECURE CARD PAYMENT · SQUARE</Mono>
          </View>
          <Pressable testID="square-pay-close" onPress={onClose} style={s.close} accessibilityLabel="Close">
            <Ionicons name="close" size={24} color={colors.onSurfaceInverse} />
          </Pressable>
        </View>
        <View style={{ flex: 1, paddingBottom: insets.bottom }}>
          {url ? (
            Platform.OS === "web" ? (
              React.createElement("iframe", { src: url, title: "Square card payment", "data-testid": "square-pay-frame", style: { border: 0, width: "100%", height: "100%", background: colors.surface } })
            ) : (
              <WebView testID="square-pay-webview" source={{ uri: url }} originWhitelist={["https://*"]} startInLoadingState renderLoading={() => <Loading />}
                onMessage={(e) => { try { handle(JSON.parse(e.nativeEvent.data)); } catch {} }} javaScriptEnabled domStorageEnabled />
            )
          ) : null}
        </View>
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  head: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.lg, paddingVertical: spacing.md, backgroundColor: c.surfaceInverse },
  title: { fontSize: 18, color: c.onSurfaceInverse },
  sub: { fontSize: 11, color: c.onSurfaceInverse, opacity: 0.8 },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.onSurfaceInverse },
}));
