import { useCallback, useEffect, useRef } from "react";
import { subscribeTermChanged } from "../state/appEvents";

export interface PeriodicSyncContext {
  /**
   * True for periodic timer ticks, visibility restores, and focus checks.
   * False for the initial mount, manual sync(), and explicit term-change events.
   */
  isBackground: boolean;
  /**
   * Returns true if a newer sync execution was triggered, if the hook was
   * disabled, or if the component has unmounted. Callers must check `isStale()`
   * across `await` boundaries before applying state updates.
   */
  isStale: () => boolean;
}

export interface UsePeriodicSyncOptions {
  /** Polling interval in milliseconds. Defaults to 5000 (5 seconds SLA). */
  intervalMs?: number;
  /** Whether polling and event listeners are active. Defaults to true. */
  enabled?: boolean;
  /** If true, automatically trigger a sync when `publishTermChanged()` is broadcast. Defaults to false. */
  listenTermChanged?: boolean;
  /** Optional dependency list: whenever any dependency changes, triggers a fresh (non-background) sync. */
  deps?: React.DependencyList;
}

const DEFAULT_SYNC_INTERVAL_MS = 5000;

/**
 * Periodically synchronizes term-scoped views with stale-response protection
 * and window visibility awareness to satisfy the 5-second cross-session SLA.
 */
export function usePeriodicSync(
  syncCallback: (context: PeriodicSyncContext) => Promise<void> | void,
  options: UsePeriodicSyncOptions = {},
) {
  const {
    intervalMs = DEFAULT_SYNC_INTERVAL_MS,
    enabled = true,
    listenTermChanged = false,
    deps = [],
  } = options;

  const callbackRef = useRef(syncCallback);
  callbackRef.current = syncCallback;

  const mountedRef = useRef(true);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const seqRef = useRef(0);
  // Holds the sequence number of the run that currently owns the in-flight
  // slot, rather than a bare boolean. A newer run takes ownership by
  // overwriting it, while a bare `seqRef` bump (which unmount performs) leaves
  // ownership untouched, so the slot cannot be stranded and wedge polling.
  const inFlightSeqRef = useRef<number | null>(null);

  const runSync = useCallback(
    async (isBackground: boolean) => {
      if (!mountedRef.current || !enabledRef.current) return;
      if (typeof document !== "undefined" && isBackground && document.visibilityState === "hidden") {
        return;
      }
      if (isBackground && inFlightSeqRef.current !== null) {
        return;
      }

      const currentSeq = ++seqRef.current;
      inFlightSeqRef.current = currentSeq;

      const isStale = () =>
        !mountedRef.current || !enabledRef.current || currentSeq !== seqRef.current;

      try {
        await callbackRef.current({ isBackground, isStale });
      } finally {
        if (inFlightSeqRef.current === currentSeq) {
          inFlightSeqRef.current = null;
        }
      }
    },
    [],
  );

  // Manual trigger exposed to consumers
  const sync = useCallback(
    (isBackground = false) => {
      runSync(isBackground);
    },
    [runSync],
  );

  // Re-run on dependency changes or initial mount
  useEffect(() => {
    // StrictMode simulates an unmount/remount, and the unmount cleanup clears
    // `mountedRef`. That cleanup is declared below this effect, so on the
    // remount this effect runs first and would see a stale `false` and skip the
    // only non-background sync the view ever gets. Restore the flag here.
    mountedRef.current = true;
    runSync(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runSync, enabled, ...deps]);

  // Timer, visibility, focus, and app event subscriptions
  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) return;

    let timerId: ReturnType<typeof setInterval> | null = null;

    const startTimer = () => {
      if (timerId !== null) clearInterval(timerId);
      timerId = setInterval(() => {
        runSync(true);
      }, intervalMs);
    };

    const stopTimer = () => {
      if (timerId !== null) {
        clearInterval(timerId);
        timerId = null;
      }
    };

    // Only run timer if currently visible
    if (typeof document === "undefined" || document.visibilityState !== "hidden") {
      startTimer();
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        runSync(true);
        startTimer();
      } else {
        stopTimer();
      }
    };

    const handleFocus = () => {
      if (document.visibilityState === "visible") {
        runSync(true);
      }
    };

    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", handleVisibilityChange);
    }
    if (typeof window !== "undefined") {
      window.addEventListener("focus", handleFocus);
    }

    let unsubscribeTerm: (() => void) | null = null;
    if (listenTermChanged) {
      unsubscribeTerm = subscribeTermChanged(() => {
        // An identity change must supersede pending reads, not be coalesced away.
        runSync(false);
      });
    }

    return () => {
      stopTimer();
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", handleVisibilityChange);
      }
      if (typeof window !== "undefined") {
        window.removeEventListener("focus", handleFocus);
      }
      if (unsubscribeTerm) {
        unsubscribeTerm();
      }
    };
  }, [enabled, intervalMs, listenTermChanged, runSync]);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      seqRef.current++;
    };
  }, []);

  return { sync };
}
