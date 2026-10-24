import React, { useState } from "react";
import { Modal, Pressable, View } from "react-native";
import { Image } from "expo-image";
import Svg, { Path } from "react-native-svg";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { fileUrl, fmtDepotTime, Stop } from "@/src/api";
import { Mono } from "@/src/ui";
import { makeStyles, spacing, useTheme } from "@/src/theme";

// Customer-facing proof of delivery: photo thumbnail (tap to enlarge), signature, QR check.
export default function DeliveryProof({ stop }: { stop: Stop }) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [zoom, setZoom] = useState(false);
  const hasAny = stop.pod_photo_path || stop.pod_signature || stop.pod_qr_verified;
  if (!hasAny) return null;
  return (
    <View style={s.box} testID="delivery-proof">
      <Mono style={s.label}>PROOF OF DELIVERY · {fmtDepotTime(stop.delivered_at)}</Mono>
      <View style={s.row}>
        {stop.pod_photo_path ? (
          <Pressable testID="delivery-proof-photo" onPress={() => setZoom(true)} style={s.tile}>
            <Image source={fileUrl(stop.pod_photo_path)} style={{ flex: 1 }} contentFit="cover" />
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
      {zoom && stop.pod_photo_path ? (
        <Modal visible transparent animationType="fade" onRequestClose={() => setZoom(false)}>
          <View style={[s.zoomWrap, { paddingTop: insets.top + spacing.lg, paddingBottom: insets.bottom + spacing.lg }]}>
            <Image source={fileUrl(stop.pod_photo_path)} style={{ flex: 1 }} contentFit="contain" testID="delivery-proof-photo-full" />
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
  zoomWrap: { flex: 1, backgroundColor: c.surfaceInverse, paddingHorizontal: spacing.lg },
  zoomClose: { position: "absolute", right: spacing.lg, width: 48, height: 48, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.onSurfaceInverse },
}));
