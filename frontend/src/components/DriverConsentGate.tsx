import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { queryClient } from "@/src/query-client";
import { api } from "@/src/api";
import { clearSession } from "@/src/session";
import ConsentModal from "@/src/components/ConsentModal";

// Drivers (created by dispatch, no self sign-up) accept the privacy & compliance terms before their first route.
// Shown only after the courier terms are accepted (one legal pop-up at a time).
export function useDriverConsent() {
  return useQuery({ queryKey: ["my-consent"], queryFn: () => api<{ phipa_consent_given: boolean; role: string }>("/me/consent") });
}

export default function DriverConsentGate({ enabled = true }: { enabled?: boolean }) {
  const q = useDriverConsent();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const need = enabled && q.data?.role === "driver" && !q.data.phipa_consent_given;
  const agree = async (consent_timestamp: string) => {
    setBusy(true);
    setErr(null);
    try {
      await api("/me/consent", { method: "POST", body: { consent_timestamp } });
      await q.refetch();
      queryClient.invalidateQueries(); // manifest calls were blocked until consent
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return <ConsentModal visible={!!need} role="driver" busy={busy} error={err} onAgree={agree} onClose={() => clearSession()} />;
}
