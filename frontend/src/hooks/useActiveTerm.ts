import { useRef, useState } from "react";
import { api, type Term } from "../api/client";
import { publishTermChanged } from "../state/appEvents";
import { usePeriodicSync } from "./usePeriodicSync";

export function useActiveTerm() {
  const [term, setTerm] = useState<Term | null>(null);
  const [loading, setLoading] = useState(true);
  const lastTermIdRef = useRef<string | null | undefined>(undefined);

  const { sync } = usePeriodicSync(
    async ({ isBackground, isStale }) => {
      if (!isBackground) {
        setLoading(true);
      }
      try {
        const result = await api.getActiveTerm();
        if (isStale()) return;
        setTerm(result);
        const newId = result ? result.term_id : null;
        if (
          lastTermIdRef.current !== undefined &&
          lastTermIdRef.current !== newId
        ) {
          publishTermChanged();
        }
        lastTermIdRef.current = newId;
      } catch {
        if (isStale()) return;
        setTerm(null);
        if (
          lastTermIdRef.current !== undefined &&
          lastTermIdRef.current !== null
        ) {
          publishTermChanged();
        }
        lastTermIdRef.current = null;
      } finally {
        if (!isStale() && !isBackground) {
          setLoading(false);
        }
      }
    },
    {
      intervalMs: 5000,
      listenTermChanged: true,
    },
  );

  return { term, loading, refresh: () => sync(false) };
}
