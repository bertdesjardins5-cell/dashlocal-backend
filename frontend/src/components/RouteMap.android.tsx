// Android: real street map via OpenStreetMap (Leaflet in a WebView). Google's native map needs a Maps API key
// and crashes the app on open without one; this needs no key. iOS keeps Apple Maps (RouteMap.tsx).
import React, { useEffect, useMemo, useRef, useState } from "react";
import { View } from "react-native";
import { WebView } from "react-native-webview";
import { useTheme } from "@/src/theme";
import type { LatLng } from "@/src/api";

export type MapMarker = LatLng & { id: string; label: string; tone: "red" | "dark" | "green" | "light" };
type Props = { markers: MapMarker[]; path?: LatLng[]; driver?: LatLng | null; height?: number | `${number}%`; geofence?: LatLng | null; testID?: string;
  onMarkerPress?: (id: string) => void };

const HTML = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#m{margin:0;height:100%;background:#eee}.pin{display:flex;align-items:center;justify-content:center;font:700 12px monospace;color:#fff;border:2px solid #fff;box-sizing:border-box}</style>
</head><body><div id="m"></div><script>
var map=L.map('m',{zoomControl:false,attributionControl:true}).setView([43.45,-80.49],12);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
var layer=L.layerGroup().addTo(map),lastKey='';
window.render=function(d){layer.clearLayers();var pts=[];
 if(d.path.length>1)L.polyline(d.path,{color:d.c.line,weight:4,dashArray:'8 6'}).addTo(layer);
 if(d.geo)L.circle(d.geo,{radius:50,color:d.c.red,weight:2,fillColor:d.c.geoFill,fillOpacity:0.4}).addTo(layer);
 d.markers.forEach(function(m){pts.push([m.lat,m.lng]);var w=Math.max(28,10+m.label.length*8);
  L.marker([m.lat,m.lng],{icon:L.divIcon({className:'',iconSize:[w,28],iconAnchor:[w/2,14],
   html:'<div class="pin" style="width:'+w+'px;height:28px;background:'+m.color+'">'+m.label.replace(/</g,'&lt;')+'</div>'})}).on('click',function(){
   if(window.ReactNativeWebView)window.ReactNativeWebView.postMessage(m.id)}).addTo(layer)});
 if(d.driver){pts.push(d.driver);L.marker(d.driver,{icon:L.divIcon({className:'',iconSize:[22,22],iconAnchor:[11,11],
   html:'<div style="width:22px;height:22px;box-sizing:border-box;background:'+d.c.red+';border:4px solid '+d.c.line+'"></div>'})}).addTo(layer)}
 if(pts.length&&d.key!==lastKey){lastKey=d.key;if(pts.length===1)map.setView(pts[0],15);else map.fitBounds(pts,{padding:[40,40]})}};
true;</script></body></html>`;

export default function RouteMap({ markers, path = [], driver, height = 260, geofence, testID = "route-map", onMarkerPress }: Props) {
  const { colors } = useTheme();
  const ref = useRef<WebView>(null);
  const [ready, setReady] = useState(false);
  const toneColor = (t: MapMarker["tone"]) =>
    t === "red" ? colors.brandPrimary : t === "green" ? colors.success : t === "dark" ? colors.surfaceInverse : colors.muted;
  const all = [...markers, ...(driver ? [driver] : [])];
  const data = JSON.stringify({
    key: all.map((p) => `${p.lat.toFixed(3)},${p.lng.toFixed(3)}`).join("|"),
    markers: markers.map((m) => ({ id: m.id, lat: m.lat, lng: m.lng, label: m.label, color: toneColor(m.tone) })),
    path: path.map((p) => [p.lat, p.lng]),
    driver: driver ? [driver.lat, driver.lng] : null,
    geo: geofence ? [geofence.lat, geofence.lng] : null,
    c: { line: colors.surfaceInverse, red: colors.brandPrimary, geoFill: colors.brandTertiary },
  });
  const source = useMemo(() => ({ html: HTML, baseUrl: "https://arrivewise.app/" }), []);

  useEffect(() => {
    if (ready) ref.current?.injectJavaScript(`window.render && window.render(${data}); true;`);
  }, [ready, data]);

  return (
    <View style={{ height, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary }} testID={testID}>
      <WebView
        ref={ref}
        source={source}
        originWhitelist={["*"]}
        onLoadEnd={() => setReady(true)}
        onMessage={(e: { nativeEvent: { data: string } }) => onMarkerPress?.(e.nativeEvent.data)}
        javaScriptEnabled
        scrollEnabled={false}
        nestedScrollEnabled
        setSupportMultipleWindows={false}
        style={{ flex: 1, backgroundColor: "transparent" }}
      />
    </View>
  );
}
