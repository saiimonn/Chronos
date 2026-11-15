import { StrictMode } from "react";
import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePeriodicSync } from "./usePeriodicSync";
import { publishTermChanged } from "../state/appEvents";

describe("usePeriodicSync", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("triggers initial sync with isBackground: false", async () => {
    const onSync = vi.fn();
    renderHook(() => usePeriodicSync(onSync));

    expect(onSync).toHaveBeenCalledTimes(1);
    expect(onSync).toHaveBeenCalledWith(
      expect.objectContaining({ isBackground: false }),
    );
  });

  it("periodically polls every 5000ms by default with isBackground: true", async () => {
    const onSync = vi.fn();
    renderHook(() => usePeriodicSync(onSync, { intervalMs: 5000 }));

    expect(onSync).toHaveBeenCalledTimes(1);

    // Fast-forward 5 seconds and flush async tasks
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(onSync).toHaveBeenCalledTimes(2);
    expect(onSync).toHaveBeenLastCalledWith(
      expect.objectContaining({ isBackground: true }),
    );

    // Fast-forward another 5 seconds
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(onSync).toHaveBeenCalledTimes(3);
  });

  it("pauses polling when the document is hidden and resumes when visible", async () => {
    const onSync = vi.fn();
    renderHook(() => usePeriodicSync(onSync, { intervalMs: 5000 }));
    expect(onSync).toHaveBeenCalledTimes(1);

    // Flush initial call
    await act(async () => {
      await Promise.resolve();
    });

    // Simulate switching away from the tab
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    // Advance 10 seconds while hidden; no new syncs should fire
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(onSync).toHaveBeenCalledTimes(1);

    // Simulate returning to the tab
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    // Should immediately fire a sync on returning
    expect(onSync).toHaveBeenCalledTimes(2);
    expect(onSync).toHaveBeenLastCalledWith(
      expect.objectContaining({ isBackground: true }),
    );

    // And timer resumes
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(onSync).toHaveBeenCalledTimes(3);
  });

  it("triggers sync on window focus when document is visible", async () => {
    const onSync = vi.fn();
    renderHook(() => usePeriodicSync(onSync));
    expect(onSync).toHaveBeenCalledTimes(1);

    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    expect(onSync).toHaveBeenCalledTimes(2);
    expect(onSync).toHaveBeenLastCalledWith(
      expect.objectContaining({ isBackground: true }),
    );
  });

  it("marks responses as stale if superseded by a newer execution", async () => {
    const staleResults: boolean[] = [];

    let resolveFirst: (() => void) | null = null;
    let resolveSecond: (() => void) | null = null;

    const onSync = vi.fn().mockImplementation(async ({ isStale }) => {
      if (!resolveFirst) {
        await new Promise<void>((res) => {
          resolveFirst = res;
        });
      } else {
        await new Promise<void>((res) => {
          resolveSecond = res;
        });
      }
      staleResults.push(isStale());
    });

    const { result } = renderHook(() => usePeriodicSync(onSync));

    // First call is in-flight
    expect(onSync).toHaveBeenCalledTimes(1);

    // Trigger second call manually
    act(() => {
      result.current.sync(false);
    });
    expect(onSync).toHaveBeenCalledTimes(2);

    // Resolve second call first
    await act(async () => {
      resolveSecond?.();
      await Promise.resolve();
    });
    // Resolve first call after second call
    await act(async () => {
      resolveFirst?.();
      await Promise.resolve();
    });

    expect(staleResults).toEqual([false, true]); // [second (not stale), first (stale)]
  });

  it("cleans up timers and marks in-flight requests as stale on unmount", async () => {
    let checkStale: (() => boolean) | null = null;

    const onSync = vi.fn().mockImplementation(({ isStale }) => {
      checkStale = isStale;
    });

    const { unmount } = renderHook(() =>
      usePeriodicSync(onSync, { intervalMs: 5000 }),
    );
    expect(onSync).toHaveBeenCalledTimes(1);
    expect(checkStale ? (checkStale as () => boolean)() : null).toBe(false);

    unmount();

    // In-flight check is now stale
    expect(checkStale ? (checkStale as () => boolean)() : null).toBe(true);

    // Advance timer: no more calls after unmount
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(onSync).toHaveBeenCalledTimes(1);
  });

  it("does not run polling or trigger syncs when enabled is false", async () => {
    const onSync = vi.fn();
    renderHook(() => usePeriodicSync(onSync, { enabled: false }));

    expect(onSync).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(onSync).not.toHaveBeenCalled();
  });

  it("triggers sync when listenTermChanged is true and a term change is published", async () => {
    const onSync = vi.fn();
    renderHook(() =>
      usePeriodicSync(onSync, { listenTermChanged: true }),
    );
    expect(onSync).toHaveBeenCalledTimes(1);

    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      publishTermChanged();
    });

    expect(onSync).toHaveBeenCalledTimes(2);
    expect(onSync).toHaveBeenLastCalledWith(
      expect.objectContaining({ isBackground: false }),
    );
  });

  it("does not spawn concurrent background polls when one is already in-flight", async () => {
    let resolveCall: (() => void) | null = null;

    const onSync = vi.fn().mockImplementation(async ({ isBackground }) => {
      if (isBackground) {
        await new Promise<void>((res) => {
          resolveCall = res;
        });
      }
    });

    renderHook(() => usePeriodicSync(onSync, { intervalMs: 5000 }));
    expect(onSync).toHaveBeenCalledTimes(1);

    await act(async () => {
      await Promise.resolve();
    });

    // First interval tick starts background sync
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onSync).toHaveBeenCalledTimes(2);

    // Second interval tick arrives while first background sync is still running
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    // Should NOT launch another background sync
    expect(onSync).toHaveBeenCalledTimes(2);

    // Finish first background sync
    await act(async () => {
      resolveCall?.();
      await Promise.resolve();
    });

    // Third tick arrives after first resolved
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onSync).toHaveBeenCalledTimes(3);
  });

  it("supersedes a pending read when an explicit term change is published", async () => {
    let finish: () => void = () => {};
    const onSync = vi.fn().mockImplementationOnce(
      () => new Promise<void>((resolve) => { finish = resolve; }),
    );
    renderHook(() => usePeriodicSync(onSync, { listenTermChanged: true }));
    const oldContext = onSync.mock.calls[0][0];

    await act(async () => publishTermChanged());

    expect(onSync).toHaveBeenCalledTimes(2);
    expect(oldContext.isStale()).toBe(true);
    expect(onSync.mock.calls[1][0].isStale()).toBe(false);
    await act(async () => finish());
  });

  // React 19 StrictMode mounts, unmounts, then remounts every component in
  // development. The four term-scoped views are the only consumers of this
  // hook, and all four hung on their loading placeholder because the remount's
  // sync never ran and the in-flight slot stayed occupied forever.
  describe("under StrictMode's simulated remount", () => {
    it("runs a non-background sync that is not already stale", async () => {
      const onSync = vi.fn();
      renderHook(() => usePeriodicSync(onSync), { wrapper: StrictMode });

      await act(async () => {
        await Promise.resolve();
      });

      const fresh = onSync.mock.calls.filter(
        ([context]) => context.isBackground === false && context.isStale() === false,
      );
      expect(fresh).toHaveLength(1);
    });

    it("keeps polling after an initial sync is stranded by the remount", async () => {
      let resolveFirst: (() => void) | undefined;
      const onSync = vi.fn().mockImplementationOnce(
        () => new Promise<void>((resolve) => { resolveFirst = resolve; }),
      );
      renderHook(() => usePeriodicSync(onSync), { wrapper: StrictMode });

      // The first mount's sync is superseded by the remount and settles late.
      await act(async () => {
        resolveFirst?.();
        await Promise.resolve();
      });
      const settled = onSync.mock.calls.length;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(15000);
      });
      expect(onSync.mock.calls.length).toBeGreaterThan(settled);
      expect(onSync).toHaveBeenLastCalledWith(
        expect.objectContaining({ isBackground: true }),
      );
    });
  });
});
