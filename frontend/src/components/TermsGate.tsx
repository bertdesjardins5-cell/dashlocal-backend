import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { api } from "@/src/api";
import { queryClient } from "@/src/query-client";
import { clearSession } from "@/src/session";
import TermsModal from "@/src/components/TermsModal";

const COPY = {
  merchant: {
    title: "DashLocal Terms of Service",
    sub: "We've updated our Terms of Service (October 2026), including how payments work. Please review and accept them to keep using your Merchant Portal.",
    agree: "I have read and agree to the DashLocal Terms of Service on behalf of my business.",
    accept: "Accept terms",
  },
  driver: {
    title: "Independent Courier Terms",
    sub: "Before your first shift, please read the DashLocal terms — section 4 covers your independent contractor relationship, expenses, taxes and service standards.",
    agree: "I have read and agree to the DashLocal Terms, and I understand I work as an independent contractor, not an employee.",
    accept: "Accept & start",
  },
};

// Store users and drivers must accept the current terms before using the app (shown on their next sign-in after an update).
export function useTermsStatus(kind: "merchant" | "driver") {
  return useQuery({ queryKey: ["terms-status", kind], queryFn: () => api<{ required: boolean; version: string }>("/auth/terms-status") });
}

export default function TermsGate({ kind, onNotify, onSignedOut }: { kind: "merchant" | "driver"; onNotify?: (m: string, tone?: "dark" | "red" | "green") => void; onSignedOut?: () => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const q = useTermsStatus(kind);
  const c = COPY[kind];

  const accept = async () => {
    setBusy(true);
    try {
      await api("/auth/accept-terms", { method: "POST" });
      await q.refetch();
      queryClient.invalidateQueries(); // anything the server blocked until acceptance loads now
      onNotify?.("THANKS · TERMS ACCEPTED", "green");
    } catch (e: any) {
      onNotify?.(e.message, "red");
    } finally {
      setBusy(false);
    }
  };
  const decline = async () => {
    await clearSession();
    if (onSignedOut) onSignedOut();
    else router.replace("/merchant");
  };

  return (
    <TermsModal visible={!!q.data?.required} onAgree={accept} onClose={decline} busy={busy} onOpenTerms={() => router.push("/terms")}
      title={c.title} sub={c.sub} agreeLabel={c.agree} cancelLabel="Sign out" acceptLabel={c.accept} />
  );
}
