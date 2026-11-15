import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadPendingBatch, savePendingBatch } from "./pendingBlockBatch";

// Node 26 ships its own global localStorage that shadows jsdom's, so the test
// supplies an in-memory one.
const store = new Map<string, string>();
vi.stubGlobal("localStorage", {
  getItem: (key: string) => store.get(key) ?? null,
  setItem: (key: string, value: string) => void store.set(key, value),
  removeItem: (key: string) => void store.delete(key),
});

const edit = (classId: string) => ({
  class_id: classId, faculty_id: "f-1", room_code: "LB305", meeting_days: "MW", time_slot_id: "slot-1",
});

describe("pending Block batch storage", () => {
  beforeEach(() => store.clear());

  it("keeps which classes were edited by hand alongside the full edited proposal", () => {
    savePendingBatch("user-1", {
      termId: "term-1",
      scheduleId: "schedule-1",
      runs: [{ blockId: "block-1", jobId: "job-1", edits: [edit("c-1"), edit("c-2")], editedClassIds: ["c-2"] }],
    });

    expect(loadPendingBatch("user-1")?.runs).toEqual([
      { blockId: "block-1", jobId: "job-1", edits: [edit("c-1"), edit("c-2")], editedClassIds: ["c-2"] },
    ]);
  });

  it("still reads an older entry without edited ids, and rejects malformed ids", () => {
    store.set("chronos.pendingBlockBatch.v1:user-1", JSON.stringify({
      termId: "term-1", scheduleId: "schedule-1", runs: [{ blockId: "block-1", jobId: "job-1", edits: [edit("c-1")] }],
    }));
    expect(loadPendingBatch("user-1")?.runs).toEqual([{ blockId: "block-1", jobId: "job-1", edits: [edit("c-1")] }]);

    store.set("chronos.pendingBlockBatch.v1:user-1", JSON.stringify({
      termId: "term-1", scheduleId: "schedule-1", runs: [{ blockId: "block-1", jobId: "job-1", edits: [edit("c-1")], editedClassIds: [1] }],
    }));
    expect(loadPendingBatch("user-1")).toBeNull();
  });
});
