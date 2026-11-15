import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, type Term } from "../api/client";
import { useActiveTerm } from "./useActiveTerm";
import * as appEvents from "../state/appEvents";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      getActiveTerm: vi.fn(),
    },
  };
});

const term1: Term = {
  term_id: "term-1",
  academic_year: "2026-2027",
  term_number: 1,
  is_active: true,
};

const term2: Term = {
  term_id: "term-2",
  academic_year: "2026-2027",
  term_number: 2,
  is_active: true,
};

describe("useActiveTerm", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("loads active term on mount and clears loading", async () => {
    vi.mocked(api.getActiveTerm).mockResolvedValue(term1);

    const { result } = renderHook(() => useActiveTerm());
    expect(result.current.loading).toBe(true);

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.term).toEqual(term1);
  });

  it("handles NO_ACTIVE_TERM without erroring", async () => {
    vi.mocked(api.getActiveTerm).mockRejectedValue(
      new ApiError(409, "NO_ACTIVE_TERM", "No active term selected."),
    );

    const { result } = renderHook(() => useActiveTerm());

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.term).toBeNull();
  });

  it("polls every 5000ms in background without flashing loading state", async () => {
    vi.mocked(api.getActiveTerm).mockResolvedValue(term1);

    const { result } = renderHook(() => useActiveTerm());

    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.term).toEqual(term1);
    expect(api.getActiveTerm).toHaveBeenCalledTimes(1);

    // Advance 5 seconds
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(api.getActiveTerm).toHaveBeenCalledTimes(2);
    // Background polling must not reset loading to true
    expect(result.current.loading).toBe(false);
  });

  it("broadcasts publishTermChanged when background poll detects an active term switch", async () => {
    const publishSpy = vi.spyOn(appEvents, "publishTermChanged");
    vi.mocked(api.getActiveTerm).mockResolvedValueOnce(term1);

    const { result } = renderHook(() => useActiveTerm());

    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.term).toEqual(term1);
    // Initial mount does not announce a change
    expect(publishSpy).not.toHaveBeenCalled();

    // The poll and the event-triggered refresh both see the new active term.
    vi.mocked(api.getActiveTerm).mockResolvedValue(term2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(result.current.term).toEqual(term2);
    expect(result.current.loading).toBe(false);
    expect(publishSpy).toHaveBeenCalledTimes(1);
  });

  it("does not broadcast publishTermChanged if the active term ID is unchanged", async () => {
    const publishSpy = vi.spyOn(appEvents, "publishTermChanged");
    vi.mocked(api.getActiveTerm).mockResolvedValue(term1);

    const { result } = renderHook(() => useActiveTerm());

    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.term).toEqual(term1);

    // Next poll returns same term
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(result.current.term).toEqual(term1);
    expect(publishSpy).not.toHaveBeenCalled();
  });
});
