import React, { ComponentProps, useState } from "react";
import { Modal, Pressable, TextInput, View } from "react-native";
import { KeyboardStickyView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import RouteMap from "@/src/components/RouteMap";
import { Btn, Display, Mono, Pill, statusTone } from "@/src/ui";
import { fonts, makeStyles, spacing, useTheme } from "@/src/theme";

export type StopDetail = { title: string; address: string; status: string; extra?: string };
type Props = ComponentProps<typeof RouteMap> & { title?: string; details?: Record<string, StopDetail>; onCall?: (id: string) => Promise<string>;
  onText?: (id: string, message: string) => Promise<string> };

const QUICK = ["Your driver is on the way.", "Running about 15 minutes late — sorry!", "Please call us back about your delivery."];

// Route map with a fullscreen button. Fullscreen supports pinch-to-zoom; tapping a numbered stop shows its details.
export default function ExpandableRouteMap({ title = "ROUTE MAP", testID = "route-map", details, onCall, onText, ...map }: Props) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [full, setFull] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [calling, setCalling] = useState(false);
  const [callMsg, setCallMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const d = sel && details ? details[sel] : null;
  const label = sel ? map.markers.find((m) => m.id === sel)?.label : null;
  const close = () => { setFull(false); setSel(null); setCallMsg(null); setTexting(false); };
  const pick = (id: string) => { setSel(id); setCallMsg(null); setTexting(false); setMsg(""); };
  const [texting, setTexting] = useState(false);
  const [msg, setMsg] = useState("");
  const send = async () => {
    if (!sel || !onText || calling) return;
    if (!msg.trim()) return setCallMsg({ text: "TYPE A MESSAGE FIRST", ok: false });
    setCalling(true);
    setCallMsg(null);
    try {
      setCallMsg({ text: await onText(sel, msg.trim()), ok: true });
      setTexting(false);
      setMsg("");
    } catch (e: any) {
      setCallMsg({ text: e.message, ok: false });
    } finally {
      setCalling(false);
    }
  };
  const call = async () => {
    if (!sel || !onCall || calling) return;
    setCalling(true);
    setCallMsg(null);
    try {
      setCallMsg({ text: await onCall(sel), ok: true });
    } catch (e: any) {
      setCallMsg({ text: e.message, ok: false });
    } finally {
      setCalling(false);
    }
  };

  return (
    <View>
      <RouteMap {...map} testID={testID} />
      <Pressable testID={`${testID}-expand`} accessibilityLabel="Open map fullscreen" onPress={() => setFull(true)} style={s.expand}>
        <Ionicons name="expand" size={22} color={colors.onSurfaceInverse} />
      </Pressable>
      {full ? (
        <Modal visible animationType="slide" onRequestClose={close} statusBarTranslucent>
          <View style={[s.full, { paddingTop: insets.top }]} testID={`${testID}-fullscreen`}>
            <View style={s.bar}>
              <Mono style={s.title} numberOfLines={1}>{title} · {details ? "TAP A STOP" : "PINCH TO ZOOM"}</Mono>
              <Pressable testID={`${testID}-fullscreen-close`} accessibilityLabel="Close fullscreen map" onPress={close} style={s.close}>
                <Ionicons name="close" size={26} color={colors.onSurfaceInverse} />
              </Pressable>
            </View>
            <View style={{ flex: 1 }}>
              <RouteMap {...map} height="100%" testID={`${testID}-full`} onMarkerPress={details ? pick : undefined} />
            </View>
            {d ? (
              <KeyboardStickyView style={[s.card, { paddingBottom: insets.bottom + spacing.md }]} testID="map-stop-details">
                <View style={s.row}>
                  <Display style={{ fontSize: 20, flex: 1 }} numberOfLines={1}>{label ? `${label}. ` : ""}{d.title}</Display>
                  <Pressable testID="map-stop-details-close" accessibilityLabel="Hide stop details" onPress={() => setSel(null)} style={s.cardClose}>
                    <Ionicons name="close" size={22} color={colors.onSurface} />
                  </Pressable>
                </View>
                <Mono style={s.addr} testID="map-stop-details-address">{d.address}</Mono>
                <View style={s.row}>
                  <Pill label={d.status} tone={statusTone(d.status)} />
                  {d.extra ? <Mono style={s.meta}>{d.extra}</Mono> : null}
                </View>
                {onCall || onText ? (
                  <>
                    <View style={s.row}>
                      {onCall ? <Btn testID="map-stop-call-button" label="CALL" icon="call" loading={calling && !texting} disabled={calling} onPress={call} style={{ flex: 1 }} /> : null}
                      {onText ? <Btn testID="map-stop-text-button" label="TEXT" icon="chatbubble" variant={texting ? "dark" : "outline"} disabled={calling}
                        onPress={() => { setTexting(!texting); setCallMsg(null); }} style={{ flex: 1 }} /> : null}
                    </View>
                    {texting ? (
                      <View style={{ gap: spacing.sm }} testID="map-stop-text-form">
                        <View style={s.chips}>
                          {QUICK.map((q) => (
                            <Pressable key={q} onPress={() => setMsg(q)} style={s.chip} accessibilityRole="button"><Mono style={s.chipText}>{q}</Mono></Pressable>
                          ))}
                        </View>
                        <TextInput testID="map-stop-text-input" value={msg} onChangeText={setMsg} placeholder="Message to the customer" maxLength={480} multiline
                          placeholderTextColor={colors.muted} style={s.input} />
                        <Btn testID="map-stop-text-send" label="SEND TEXT (MASKED)" icon="send" loading={calling} disabled={calling} onPress={send} />
                      </View>
                    ) : null}
                    {callMsg ? <Mono testID="map-stop-call-status" style={[s.meta, { color: callMsg.ok ? colors.success : colors.brandPrimary }]}>{callMsg.text}</Mono> : null}
                  </>
                ) : null}
              </KeyboardStickyView>
            ) : <View style={{ height: insets.bottom }} />}
          </View>
        </Modal>
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  expand: { position: "absolute", right: spacing.sm, bottom: spacing.sm, width: 44, height: 44, alignItems: "center", justifyContent: "center", backgroundColor: c.surfaceInverse },
  full: { flex: 1, backgroundColor: c.surfaceInverse },
  bar: { flexDirection: "row", alignItems: "center", paddingLeft: spacing.md, minHeight: 56 },
  title: { flex: 1, color: c.onSurfaceInverse, fontSize: 13, fontWeight: "700", letterSpacing: 1 },
  close: { width: 56, height: 56, alignItems: "center", justifyContent: "center" },
  card: { position: "absolute", left: 0, right: 0, bottom: 0, backgroundColor: c.surface, borderTopWidth: 2, borderColor: c.border, padding: spacing.md, gap: spacing.sm },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  cardClose: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  addr: { fontSize: 14, color: c.onSurface },
  meta: { fontSize: 12, color: c.muted, flexShrink: 1 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs },
  chip: { borderWidth: 2, borderColor: c.border, paddingHorizontal: spacing.sm, minHeight: 36, justifyContent: "center" },
  chipText: { fontSize: 11 },
  input: { minHeight: 64, maxHeight: 120, borderWidth: 2, borderColor: c.border, padding: spacing.sm, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface, textAlignVertical: "top" },
}));
