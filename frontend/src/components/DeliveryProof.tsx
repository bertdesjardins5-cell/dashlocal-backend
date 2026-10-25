import React, { useState } from "react";
import { Modal, Platform, Pressable, Share, View } from "react-native";
import { Image } from "expo-image";
import Svg, { Path } from "react-native-svg";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, BACKEND_URL, fmtDepotTime } from "@/src/api";
import { Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

type Proof = { id: string; tracking_code: string; delivered_at: string | null; pod_qr_verified: boolean; pod_signature: string | null; photo_url: string | null };

// Customer-facing proof of delivery: photo thumbnail (tap to enlarge), signature, QR check.
export default function DeliveryProof({ stop, orderToken, onShared, onError }: { stop: Proof; orderToken: string; onShared?: (url: string) => void; onError?: (m: string) => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [zoom, setZoom] = useState(false);
  const [sharing, setSharing] = useState(false);
  const photo = stop.photo_url ? `${BACKEND_URL}${stop.photo_url}` : null;
  const share = async () => {
    setSharing(true);
    try {
      const r = await api<{ path: string }>(`/stops/${stop.id}/share`, { method: "POST", headers: { "X-Order-Token": orderToken } });
      const url = `${BACKEND_URL}${r.path}`;
      const message = `Proof of delivery for ${stop.tracking_code}: ${url}`;
      if (Platform.OS === "web") {
        const nav: any = typeof navigator !== "undefined" ? navigator : null;
        if (nav?.share) await nav.share({ title: "Proof of delivery", text: message, url });
        else if (!window.open(url, "_blank")) {
          await nav?.clipboard?.writeText(url).catch(() => {});
          onError?.(`POP-UP BLOCKED · LINK COPIED: ${url}`);
          return;
        }
      } else {
        await Share.share(Platform.OS === "ios" ? { message: `Proof of delivery for ${stop.tracking_code}`, url } : { message });
      }
      onShared?.(url);
    } catch (e: any) {
      if (e?.name !== "AbortError") onError?.(e.message ?? "Could not share");
    } finally {
      setSharing(false);
    }
  };
  const hasAny = photo || stop.pod_signature || stop.pod_qr_verified;
  if (!hasAny) return null;
  return (
    <View style={s.box} testID="delivery-proof">
      <Mono style={s.label}>PROOF OF DELIVERY · {fmtDepotTime(stop.delivered_at)}</Mono>
      <View style={s.row}>
        {photo ? (
          <Pressable testID="delivery-proof-photo" onPress={() => setZoom(true)} style={s.tile}>
            <Image source={photo} style={{ flex: 1 }} contentFit="cover" />
            <View style={s.tag}><Ionicons name="expand" size={12} color={colors.onSurfaceInverse} /></View>
          </Pressable>
        ) : null}
        {stop.pod_signature ? (
          <View style={[s.tile, { flex: 1 }]} testID="delivery-proof-signature">
            <Svg width="100%" height="100%" viewBox="0 0 360 200" preserveAspectRatio="xMidYMid meet">
              <Path d={stop.pod_signature} stroke={colors.onSurface} strokeWidth={4} fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </Svg>
            <Mono style={s.sigLabel}>SIGNED</Mono>
          </View>
        ) : null}
        {stop.pod_qr_verified ? (
          <View style={[s.tile, s.qr]} testID="delivery-proof-qr">
            <Ionicons name="qr-code" size={28} color={colors.onSuccess} />
            <Mono style={s.qrText}>PARCEL{"\n"}SCANNED</Mono>
          </View>
        ) : null}
      </View>
      <Pressable testID="delivery-proof-share" disabled={sharing} onPress={share} style={({ pressed }) => [s.share, pressed && { opacity: 0.8 }]}>
        <Ionicons name="share-social" size={18} color={colors.onSurfaceInverse} />
        <Mono style={s.shareText}>{sharing ? "PREPARING LINK…" : "SHARE PROOF LINK"}</Mono>
      </Pressable>
      {zoom && photo ? (
        <Modal visible transparent animationType="fade" onRequestClose={() => setZoom(false)}>
          <View style={[s.zoomWrap, { paddingTop: insets.top + spacing.lg, paddingBottom: insets.bottom + spacing.lg }]}>
            <Image source={photo} style={{ flex: 1 }} contentFit="contain" testID="delivery-proof-photo-full" />
            <Pressable testID="delivery-proof-close" onPress={() => setZoom(false)} style={[s.zoomClose, { top: insets.top + spacing.lg }]}>
              <Ionicons name="close" size={28} color={colors.onSurfaceInverse} />
            </Pressable>
          </View>
        </Modal>
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { gap: spacing.sm },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  row: { flexDirection: "row", gap: spacing.sm, height: 96 },
  tile: { width: 96, borderWidth: 2, borderColor: c.border, overflow: "hidden", backgroundColor: c.surface },
  tag: { position: "absolute", right: 0, bottom: 0, backgroundColor: c.surfaceInverse, padding: 4 },
  sigLabel: { position: "absolute", left: 4, bottom: 2, fontSize: 9, fontWeight: "700", color: c.muted },
  qr: { backgroundColor: c.success, alignItems: "center", justifyContent: "center", gap: 4 },
  qrText: { fontSize: 9, fontWeight: "700", color: c.onSuccess, textAlign: "center" },
  share: { minHeight: 48, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm, backgroundColor: c.surfaceInverse },
  shareText: { color: c.onSurfaceInverse, fontWeight: "700", fontSize: 13, letterSpacing: 1 },
  zoomWrap: { flex: 1, backgroundColor: c.surfaceInverse, paddingHorizontal: spacing.lg },
  zoomClose: { position: "absolute", right: spacing.lg, width: 48, height: 48, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.onSurfaceInverse },
}));
