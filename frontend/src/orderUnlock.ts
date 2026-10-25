import { Platform } from "react-native";
import * as SecureStore from "expo-secure-store";
import AsyncStorage from "@react-native-async-storage/async-storage";
import type { OrderPrivate } from "@/src/api";

// Remembers a customer's order unlock on this device (the server ends it 7 days after delivery).
export type Verified = OrderPrivate & { token: string };

const key = (code: string) => `dashlocal_order_${code.toUpperCase().replace(/[^A-Z0-9]/g, "")}`;
const mem = new Map<string, Verified>();

export async function loadUnlock(code: string): Promise<Verified | null> {
  if (mem.has(code)) return mem.get(code)!;
  try {
    const raw = Platform.OS === "web" ? await AsyncStorage.getItem(key(code)) : await SecureStore.getItemAsync(key(code));
    const v = raw ? (JSON.parse(raw) as Verified) : null;
    if (v) mem.set(code, v);
    return v;
  } catch {
    return null;
  }
}

export async function saveUnlock(code: string, v: Verified | null) {
  if (v) mem.set(code, v);
  else mem.delete(code);
  try {
    if (Platform.OS === "web") await (v ? AsyncStorage.setItem(key(code), JSON.stringify(v)) : AsyncStorage.removeItem(key(code)));
    else await (v ? SecureStore.setItemAsync(key(code), JSON.stringify(v)) : SecureStore.deleteItemAsync(key(code)));
  } catch {}
}
