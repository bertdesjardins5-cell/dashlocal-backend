import React, { useState } from "react";
import { Linking, Platform, Pressable, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as ImagePicker from "expo-image-picker";
import { Image } from "expo-image";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { api, API, fileUrl, RouteDetail, Stop, windowLabel } from "@/src/api";
import { getToken } from "@/src/session";
import { uploadForm } from "@/src/utils/upload";
import SignaturePad from "@/src/components/SignaturePad";
import Svg, { Path } from "react-native-svg";
import { Btn, Display, ErrorBlock, Header, Loading, Mono, Pill, statusTone, useToast } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

const FAIL_REASONS = ["No one home", "Access denied", "Wrong address", "Refused"];
const SMS_PRESETS = ["I'm about 5 minutes away.", "I'm outside your door now.", "I left your parcel at the door.", "I can't find the entrance — can you help?"];

export default function StopDetail() {
  const { stopId } = useLocalSearchParams<{ stopId: string }>();
  const s = useStyles();
  const router = useRouter();
  const qc = useQueryClient();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const toast = useToast();
  const [camPerm, requestCamPerm] = ImagePicker.useCameraPermissions();
  const [photo, setPhoto] = useState<{ uri: string; path: string | null } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [signature, setSignature] = useState("");
  const [busy, setBusy] = useState(false);
  const [showFail, setShowFail] = useState(false);
  const [permPrompt, setPermPrompt] = useState(false);

  const q = useQuery({ queryKey: ["stop", stopId], queryFn: () => api<Stop>(`/stops/${stopId}`) });
  const stop = q.data;
  const routeQ = useQuery({ queryKey: ["route", stop?.route_id], queryFn: () => api<RouteDetail>(`/routes/${stop!.route_id}`), enabled: !!stop?.route_id });
  const driverId = routeQ.data?.driver?.id;
  const [showSms, setShowSms] = useState(false);
  const [contacting, setContacting] = useState<"call" | "sms" | null>(null);
  const [savingPhones, setSavingPhones] = useState(false);
  const [showPhones, setShowPhones] = useState(false);
  const [custPhone, setCustPhone] = useState("");
  const [drvPhone, setDrvPhone] = useState("");

  const refreshAll = () => {
    q.refetch();
    qc.invalidateQueries({ queryKey: ["driver-route"] });
  };

  const contact = async (channel: "call" | "sms", message?: string) => {
    if (contacting) return;
    setShowSms(false);
    setContacting(channel);
    try {
      const r = await api(`/stops/${stopId}/contact`, { method: "POST", body: { channel, message } });
      toast.show(channel === "call" ? `RINGING YOUR PHONE FROM ${r.proxy_number} — ANSWER TO CONNECT` : `TEXT SENT VIA ${r.proxy_number}`, "green");
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setContacting(null);
    }
  };

  const savePhones = async () => {
    const clean = (p: string) => p.replace(/[\s\-()]/g, "");
    const valid = (p: string) => /^\+[1-9]\d{7,14}$/.test(clean(p));
    if (!valid(custPhone) || (driverId && !valid(drvPhone))) return toast.show("USE +COUNTRYCODE FORMAT, E.G. +14155550123", "red");
    setSavingPhones(true);
    try {
      if (custPhone && custPhone !== stop?.customer_phone) await api(`/stops/${stopId}/phone`, { method: "PUT", body: { phone: custPhone } });
      if (driverId && drvPhone && drvPhone !== routeQ.data?.driver?.phone) await api(`/drivers/${driverId}/phone`, { method: "PUT", body: { phone: drvPhone } });
      toast.show("NUMBERS SAVED", "green");
      setShowPhones(false);
      q.refetch();
      routeQ.refetch();
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setSavingPhones(false);
    }
  };

  const doUpload = async (asset: ImagePicker.ImagePickerAsset) => {
    setPhoto({ uri: asset.uri, path: null });
    setUploading(true);
    try {
      const res = await uploadForm(`${API}/upload`, {
        files: { file: { uri: asset.uri, name: asset.fileName ?? "pod.jpg", mimeType: asset.mimeType ?? "image/jpeg" } },
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      if (!res.ok) throw new Error(res.status === 402 ? "Storage out of credits" : "Upload failed");
      const j = await res.json();
      setPhoto({ uri: asset.uri, path: j.path });
    } catch (e: any) {
      setPhoto(null);
      toast.show(e.message, "red");
    } finally {
      setUploading(false);
    }
  };

  const openCamera = async () => {
    setPermPrompt(false);
    if (Platform.OS === "web") {
      const r = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.5 });
      if (!r.canceled) doUpload(r.assets[0]);
      return;
    }
    let granted = camPerm?.granted;
    if (!granted) {
      if (camPerm && !camPerm.canAskAgain) return setPermPrompt(true);
      const res = await requestCamPerm();
      granted = res.granted;
      if (!granted) return setPermPrompt(true);
    }
    const r = await ImagePicker.launchCameraAsync({ mediaTypes: ["images"], quality: 0.5 });
    if (!r.canceled) doUpload(r.assets[0]);
  };

  const onPhotoPress = () => {
    if (Platform.OS !== "web" && !camPerm?.granted) setPermPrompt(true);
    else openCamera();
  };

  const act = async (fn: () => Promise<any>, msg: string, back = false) => {
    setBusy(true);
    try {
      await fn();
      toast.show(msg, "green");
      refreshAll();
      if (back) setTimeout(() => (router.canGoBack() ? router.back() : router.replace(driverId ? `/driver/${driverId}` : "/driver")), 900);
    } catch (e: any) {
      toast.show(e.message, "red");
    } finally {
      setBusy(false);
    }
  };

  const closed = stop && (stop.status === "delivered" || stop.status === "failed");
  const canComplete = !!(photo?.path || signature || stop?.pod_qr_verified) && !uploading;

  return (
    <View style={s.root} testID="stop-detail-screen">
      <Header title={stop ? `STOP #${stop.seq}` : "STOP"} subtitle={stop?.tracking_code} right={stop ? <Pill label={stop.status} tone={statusTone(stop.status)} /> : null} />
      {q.isLoading ? <Loading /> : q.error || !stop ? <ErrorBlock message={(q.error as Error)?.message ?? "Not found"} onRetry={q.refetch} /> : (
        <>
          <KeyboardAwareScrollView contentContainerStyle={{ paddingBottom: spacing.xl }} bottomOffset={80}>
            <View style={s.info}>
              <Display style={s.name} testID="stop-customer-name">{stop.customer_name}</Display>
              <Mono style={s.addr}>{stop.address}</Mono>
              <Mono style={s.meta}>{stop.postal_code} · ZONE {stop.zone.toUpperCase()}</Mono>
              {stop.notes ? <Mono style={s.note}>NOTE: {stop.notes}</Mono> : null}
              {stop.customer_note ? (
                <View style={s.custNote} testID="stop-customer-note">
                  <Mono style={s.custNoteKicker}>NOTE FROM CUSTOMER</Mono>
                  <Mono style={s.custNoteText}>{stop.customer_note}</Mono>
                </View>
              ) : null}
              {stop.arrival_method ? <Mono style={s.meta} testID="stop-arrival-method">ARRIVED VIA {stop.arrival_method.toUpperCase()}</Mono> : null}
              {stop.priority ? <Mono style={s.windowTag} testID="stop-priority">PRIORITY DELIVERY</Mono> : null}
              {windowLabel(stop) ? <Mono style={s.windowTag} testID="stop-window">DELIVER {windowLabel(stop)}</Mono> : null}
            </View>

            <View style={s.commRow}>
              <Pressable testID="call-customer-button" disabled={!!contacting} onPress={() => contact("call")} style={({ pressed }) => [s.commBtn, (pressed || contacting === "call") && { backgroundColor: colors.surfaceTertiary }, !!contacting && { opacity: 0.6 }]}>
                <Ionicons name="call" size={22} color={colors.onSurface} />
                <Display style={s.commText}>CALL</Display>
              </Pressable>
              <Pressable testID="text-customer-button" onPress={() => setShowSms((v) => !v)} style={({ pressed }) => [s.commBtn, { borderLeftWidth: 2, borderColor: colors.border }, (pressed || showSms) && { backgroundColor: colors.surfaceTertiary }]}>
                <Ionicons name="chatbox" size={22} color={colors.onSurface} />
                <Display style={s.commText}>TEXT</Display>
              </Pressable>
            </View>
            {showSms ? (
              <View style={s.smsBox} testID="sms-quick-replies">
                {SMS_PRESETS.map((m, i) => (
                  <Pressable key={m} testID={`sms-preset-${i}`} onPress={() => contact("sms", m)} style={({ pressed }) => [s.smsRow, pressed && { backgroundColor: colors.surfaceTertiary }]}>
                    <Mono style={{ fontSize: 14, flex: 1 }}>{m}</Mono>
                    <Ionicons name="send" size={18} color={colors.brandPrimary} />
                  </Pressable>
                ))}
              </View>
            ) : null}
            <View style={s.maskedRow}>
              <Mono style={s.masked}>NUMBERS MASKED VIA TWILIO LINE</Mono>
              <Pressable testID="edit-phones-button" onPress={() => { setCustPhone(stop.customer_phone); setDrvPhone(routeQ.data?.driver?.phone ?? ""); setShowPhones((v) => !v); }} style={{ minHeight: 44, justifyContent: "center", paddingHorizontal: spacing.lg }}>
                <Mono style={{ fontSize: 11, color: colors.brandPrimary, fontWeight: "700" }}>{showPhones ? "CLOSE" : "EDIT NUMBERS"}</Mono>
              </Pressable>
            </View>
            {showPhones ? (
              <View style={s.section} testID="phones-editor">
                <Mono style={s.meta}>YOUR PHONE (TWILIO RINGS THIS FIRST)</Mono>
                <TextInput testID="driver-phone-input" value={drvPhone} onChangeText={setDrvPhone} keyboardType="phone-pad" placeholder="+14155550123" placeholderTextColor={colors.muted} style={s.input} />
                <Mono style={s.meta}>CUSTOMER PHONE</Mono>
                <TextInput testID="customer-phone-input" value={custPhone} onChangeText={setCustPhone} keyboardType="phone-pad" placeholder="+14155550123" placeholderTextColor={colors.muted} style={s.input} />
                <Btn testID="save-phones-button" label="SAVE NUMBERS" variant="dark" loading={savingPhones} onPress={savePhones} />
              </View>
            ) : null}

            {closed ? (
              <View style={s.section} testID="pod-summary">
                <Display style={s.h2}>{stop.status === "delivered" ? "PROOF OF DELIVERY" : `FAILED: ${stop.fail_reason}`}</Display>
                {stop.pod_photo_path ? <Image testID="pod-photo-saved" source={fileUrl(stop.pod_photo_path)} style={s.photoImg} contentFit="cover" /> : null}
                {stop.pod_qr_verified ? <Mono style={s.qrOk} testID="pod-qr-saved">✓ PARCEL QR VERIFIED</Mono> : null}
                {stop.pod_signature ? <SignaturePreview d={stop.pod_signature} /> : null}
              </View>
            ) : (
              <View style={s.section}>
                <Display style={s.h2}>PROOF OF DELIVERY</Display>
                {permPrompt ? (
                  <View style={s.permBox} testID="camera-permission-box">
                    <Mono style={{ fontSize: 13 }}>Snap the parcel at the door so the customer sees exactly where it was left.</Mono>
                    {camPerm && !camPerm.granted && !camPerm.canAskAgain ? (
                      <Btn testID="camera-open-settings-button" label="OPEN SETTINGS" variant="dark" onPress={() => Linking.openSettings()} />
                    ) : (
                      <Btn testID="camera-allow-button" label="ALLOW CAMERA" variant="dark" onPress={openCamera} />
                    )}
                  </View>
                ) : null}
                <Pressable testID="pod-photo-box" onPress={onPhotoPress} style={s.photoBox}>
                  {photo ? (
                    <>
                      <Image source={photo.uri} style={s.photoImg} contentFit="cover" />
                      <View style={s.photoBadge}><Mono style={s.photoBadgeText}>{uploading ? "UPLOADING…" : "✓ UPLOADED · TAP TO RETAKE"}</Mono></View>
                    </>
                  ) : (
                    <>
                      <Ionicons name="camera" size={40} color={colors.onSurface} />
                      <Display style={s.commText}>TAKE PHOTO</Display>
                    </>
                  )}
                </Pressable>
                <Pressable
                  testID="scan-parcel-qr-button"
                  disabled={stop.pod_qr_verified}
                  onPress={() => router.push({ pathname: "/driver/scan/[stopId]", params: { stopId: stop.id, code: stop.tracking_code } })}
                  style={({ pressed }) => [s.qrBox, stop.pod_qr_verified && { backgroundColor: colors.success }, pressed && { backgroundColor: colors.surfaceTertiary }]}
                >
                  <Ionicons name={stop.pod_qr_verified ? "checkmark-circle" : "qr-code"} size={28} color={stop.pod_qr_verified ? colors.onSuccess : colors.onSurface} />
                  <Display style={[s.commText, stop.pod_qr_verified && { color: colors.onSuccess }]}>{stop.pod_qr_verified ? "PARCEL QR VERIFIED" : "SCAN PARCEL QR"}</Display>
                </Pressable>
                <SignaturePad value={signature} onChange={setSignature} />
                <Pressable testID="toggle-fail-button" onPress={() => setShowFail((v) => !v)} style={{ minHeight: 44, justifyContent: "center" }}>
                  <Mono style={{ color: colors.brandPrimary, fontWeight: "700" }}>{showFail ? "− CANCEL" : "+ REPORT FAILED ATTEMPT"}</Mono>
                </Pressable>
                {showFail ? (
                  <View style={{ gap: spacing.sm }}>
                    {FAIL_REASONS.map((r) => (
                      <Btn key={r} testID={`fail-reason-${r.toLowerCase().replace(/\s+/g, "-")}`} label={r.toUpperCase()} variant="outline" onPress={() => act(() => api(`/stops/${stopId}/fail`, { method: "POST", body: { reason: r } }), "MARKED FAILED", true)} />
                    ))}
                  </View>
                ) : null}
              </View>
            )}
          </KeyboardAwareScrollView>
          {!closed ? (
            <View style={[s.cta, { paddingBottom: insets.bottom + spacing.md }]}>
              {stop.status === "pending" ? (
                <Btn testID="mark-arrived-button" label="MARK ARRIVED" variant="dark" icon="flag" loading={busy} onPress={() => act(() => api(`/stops/${stopId}/arrive`, { method: "POST" }), "ARRIVED")} style={{ flex: 1 }} />
              ) : null}
              <Btn testID="complete-drop-button" label="COMPLETE DROP" icon="checkmark-done" disabled={!canComplete} loading={busy} onPress={() => act(() => api(`/stops/${stopId}/deliver`, { method: "POST", body: { photo_path: photo?.path ?? null, signature: signature || null } }), "DROP COMPLETE", true)} style={{ flex: 1 }} />
            </View>
          ) : null}
        </>
      )}
      {toast.node}
    </View>
  );
}

function SignaturePreview({ d }: { d: string }) {
  const { colors } = useTheme();
  return (
    <View testID="pod-signature-saved" style={{ height: 200, borderWidth: 2, borderColor: colors.border }}>
      <Svg width="100%" height="100%">
        <Path d={d} stroke={colors.onSurface} strokeWidth={3} fill="none" />
      </Svg>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  info: { padding: spacing.xl, gap: spacing.xs, borderBottomWidth: 2, borderColor: c.border },
  name: { fontSize: 32, letterSpacing: -1 },
  addr: { fontSize: 16, fontWeight: "700" },
  meta: { fontSize: 12, color: c.muted },
  note: { fontSize: 13, backgroundColor: c.warning, color: c.onWarning, padding: spacing.sm, marginTop: spacing.sm },
  custNote: { backgroundColor: c.brandPrimary, padding: spacing.sm, marginTop: spacing.sm, gap: 2, borderWidth: 2, borderColor: c.border },
  custNoteKicker: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: c.onBrandPrimary },
  custNoteText: { fontSize: 15, fontWeight: "700", color: c.onBrandPrimary },
  commRow: { flexDirection: "row", borderBottomWidth: 2, borderColor: c.border },
  commBtn: { flex: 1, minHeight: 64, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm },
  commText: { fontSize: 18 },
  masked: { fontSize: 12, color: c.onSurface, fontWeight: "700", paddingHorizontal: spacing.lg, letterSpacing: 1, flex: 1 },
  maskedRow: { flexDirection: "row", alignItems: "center", borderBottomWidth: 2, borderColor: c.border },
  smsBox: { borderBottomWidth: 2, borderColor: c.border },
  smsRow: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.lg, minHeight: 52, borderBottomWidth: 1, borderColor: c.divider },
  input: { height: 52, borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.md, fontFamily: fonts.mono, fontSize: 16, color: c.onSurface, backgroundColor: c.surface },
  section: { padding: spacing.lg, gap: spacing.md },
  h2: { fontSize: 20 },
  permBox: { padding: spacing.lg, gap: spacing.md, backgroundColor: c.warning, borderWidth: 2, borderColor: c.border },
  photoBox: { height: 200, borderWidth: 2, borderColor: c.border, alignItems: "center", justifyContent: "center", gap: spacing.sm, overflow: "hidden" },
  qrBox: { minHeight: 72, borderWidth: 2, borderColor: c.border, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.md },
  qrOk: { fontSize: 14, fontWeight: "700", backgroundColor: c.success, color: c.onSuccess, padding: spacing.md },
  windowTag: { fontSize: 13, fontWeight: "700", backgroundColor: c.surfaceInverse, color: c.onSurfaceInverse, paddingHorizontal: spacing.sm, paddingVertical: 2, alignSelf: "flex-start", marginTop: spacing.xs },
  photoImg: { width: "100%", height: 200 },
  photoBadge: { position: "absolute", bottom: 0, left: 0, right: 0, backgroundColor: c.surfaceInverse, padding: spacing.sm },
  photoBadgeText: { color: c.onSurfaceInverse, fontSize: 12, fontWeight: "700" },
  cta: { flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.lg, paddingTop: spacing.md, borderTopWidth: 2, borderColor: c.border, backgroundColor: c.surface },
}));
