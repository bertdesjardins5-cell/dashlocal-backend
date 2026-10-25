import { useEffect, useState } from "react";
import { Platform } from "react-native";
import * as SecureStore from "expo-secure-store";
import AsyncStorage from "@react-native-async-storage/async-storage";

// Signed-in admin / merchant session. Kept in memory for api() and persisted on the device.
export type SessionUser = {
  id: string; email: string | null; role: "admin" | "merchant" | "driver"; merchant_id: string | null; merchant_name?: string | null;
  driver_id?: string | null; driver_name?: string | null;
};
type Session = { token: string; user: SessionUser } | null;

const KEY = "multidrop_session";
let current: Session = null;
let ready = false;
let loading: Promise<void> | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

const store = {
  get: () => (Platform.OS === "web" ? AsyncStorage.getItem(KEY) : SecureStore.getItemAsync(KEY)),
  set: (v: string) => (Platform.OS === "web" ? AsyncStorage.setItem(KEY, v) : SecureStore.setItemAsync(KEY, v)),
  del: () => (Platform.OS === "web" ? AsyncStorage.removeItem(KEY) : SecureStore.deleteItemAsync(KEY)),
};

export const getToken = () => current?.token ?? null;

function load() {
  if (!loading) {
    loading = (async () => {
      try {
        const raw = await store.get();
        current = raw ? JSON.parse(raw) : null;
      } catch {
        current = null;
      }
      ready = true;
      emit();
    })();
  }
  return loading;
}

export async function setSession(token: string, user: SessionUser) {
  current = { token, user };
  emit();
  await store.set(JSON.stringify(current));
}

export async function clearSession() {
  current = null;
  emit();
  await store.del();
}

export function useSession() {
  const [, tick] = useState(0);
  useEffect(() => {
    const l = () => tick((n) => n + 1);
    listeners.add(l);
    load();
    return () => { listeners.delete(l); };
  }, []);
  return { ready, user: current?.user ?? null };
}
