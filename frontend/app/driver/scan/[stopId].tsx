import React, { useRef, useState } from "react";
import { Linking, Pressable, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { CameraView, useCameraPermissions } from "expo-camera";
import { useQueryClient } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import { api } from "@/src/api";
import { Btn, Display, Header, Mono, useToast } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

export default function ScanParcel() {
  const { stopId, code } = useLocalSearchParams<{ stopId: string; code: string }>();
  const s = useStyles();
  const router = useRouter();
  const qc = useQueryClient();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const [perm, requestPerm] = useCameraPermissions();
  const [manual, setManual] = useState("");
  const [status, setStatus] = useState<"idle" | "checking" | "ok" | "bad">("idle");
  const locked = useRef(false);

  const verify = async (value: string, manualEntry = false) => {
    if ((locked.current && !manualEntry) || status === "checking" || !value.trim()) return;
    locked.current = true;
    setStatus("checking");
    try {
      await api(`/stops/${stopId}/verify-qr`, { method: "POST", body: { code: value } });
      setStatus("ok");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      qc.invalidateQueries({ queryKey: ["stop", stopId] });
      toast.show("PARCEL VERIFIED", "green");
      setTimeout(() => router.back(), 700);
    } catch (e: any) {
      setStatus("bad");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
      toast.show(e.message.toUpperCase(), "red");
      setTimeout(() => {
        locked.current = false;
        setStatus("idle");
      }, 2000);
    }
  };

  const frameColor = status === "ok" ? colors.success : status === "bad" ? colors.brandPrimary : colors.onSurfaceInverse;

  return (
    <View style={s.root} testID="scan-parcel-screen">
      <Header title="SCAN PARCEL" subtitle={`EXPECTING ${code ?? ""}`} />
      <KeyboardAwareScrollView contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xl }} keyboardShouldPersistTaps="handled" bottomOffset={24}>
        <View style={s.camWrap}>
          {perm?.granted ? (
            <>
              <CameraView
                testID="qr-camera"
                style={{ flex: 1 }}
                facing="back"
                barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
                onBarcodeScanned={status === "idle" ? (r) => verify(r.data) : undefined}
              />
              <View pointerEvents="none" style={[s.frame, { borderColor: frameColor }]} />
            </>
          ) : (
            <View style={s.permBox} testID="qr-camera-permission">
              <Display style={{ fontSize: 20, color: colors.onSurfaceInverse }}>CAMERA NEEDED</Display>
              <Mono style={{ color: colors.onSurfaceInverse, fontSize: 13 }}>Scan the parcel label to prove the right package reached the right door.</Mono>
              {perm && !perm.canAskAgain ? (
                <Btn testID="qr-open-settings-button" label="OPEN SETTINGS" icon="settings" onPress={() => Linking.openSettings()} />
              ) : (
                <Btn testID="qr-allow-camera-button" label="ALLOW CAMERA" icon="camera" onPress={requestPerm} />
              )}
            </View>
          )}
        </View>
        <Mono style={s.hint}>POINT AT THE QR ON THE PARCEL LABEL</Mono>
        <View style={s.manual}>
          <Mono style={s.label}>LABEL DAMAGED? ENTER CODE</Mono>
          <TextInput
            testID="qr-manual-input"
            value={manual}
            onChangeText={setManual}
            placeholder={code ?? "MD1001"}
            placeholderTextColor={colors.muted}
            autoCapitalize="characters"
            returnKeyType="done"
            onSubmitEditing={() => verify(manual, true)}
            style={s.input}
          />
          <Btn testID="qr-manual-submit-button" label="VERIFY CODE" variant="dark" icon="checkmark" loading={status === "checking"} disabled={!manual.trim()} onPress={() => verify(manual, true)} />
        </View>
        <Pressable testID="qr-cancel-button" onPress={() => router.back()} style={{ minHeight: 48, alignItems: "center", justifyContent: "center" }}>
          <Mono style={{ fontWeight: "700" }}>CANCEL</Mono>
        </Pressable>
      </KeyboardAwareScrollView>
      {toast.node}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  camWrap: { height: 360, backgroundColor: c.surfaceInverse, borderBottomWidth: 2, borderColor: c.border },
  frame: { position: "absolute", top: 70, bottom: 70, left: "18%", right: "18%", borderWidth: 4 },
  permBox: { flex: 1, padding: spacing.xl, gap: spacing.md, justifyContent: "center" },
  hint: { padding: spacing.lg, fontSize: 12, fontWeight: "700", letterSpacing: 1, borderBottomWidth: 2, borderColor: c.border },
  manual: { padding: spacing.lg, gap: spacing.md },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 1.5 },
  input: { height: 56, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 20, fontWeight: "700", color: c.onSurface, backgroundColor: c.surface },
}));
