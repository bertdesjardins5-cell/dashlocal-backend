import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { api } from "@/src/api";
import { clearSession } from "@/src/session";
import TermsModal from "@/src/components/TermsModal";

// Store users must accept the current Terms of Service version before using the portal (shown after an update).
export default function TermsGate({ onNotify }: { onNotify: (m: string, tone?: "dark" | "red" | "green") => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const q = useQuery({ queryKey: ["terms-status"], queryFn: () => api<{ required: boolean; version: string }>("/auth/terms-status") });

  const accept = async () => {
    setBusy(true);
    try {
      await api("/auth/accept-terms", { method: "POST" });
      await q.refetch();
      onNotify("THANKS · UPDATED TERMS ACCEPTED", "green");
    } catch (e: any) {
      onNotify(e.message, "red");
    } finally {
      setBusy(false);
    }
  };
  const decline = async () => {
    await clearSession();
    router.replace("/merchant");
  };

  return (
    <TermsModal visible={!!q.data?.required} onAgree={accept} onClose={decline} busy={busy} onOpenTerms={() => router.push("/terms")}
      sub="We've updated our Terms of Service (October 2026), including how payments work. Please review and accept them to keep using your Merchant Portal."
      cancelLabel="Sign out" acceptLabel="Accept terms" />
  );
}
