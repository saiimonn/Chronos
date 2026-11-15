import type { ReplacementAssignmentPayload } from "../api/client";

// The generation runs still waiting for review, kept so a reload or a visit to
// another page does not lose them. Only references (and any review edits) are
// stored; each proposal is re-previewed, and so revalidated, by the server.
export interface PendingBlockRun {
  blockId: string;
  jobId: string;
  /** The proposal as edited in review, when it was edited. */
  edits?: ReplacementAssignmentPayload[];
  /** Which classes in `edits` were changed by hand. */
  editedClassIds?: string[];
}

export interface PendingBlockBatch {
  termId: string;
  scheduleId: string;
  runs: PendingBlockRun[];
}

const KEY_PREFIX = "chronos.pendingBlockBatch.v1:";
const keyFor = (userId: string) => `${KEY_PREFIX}${userId}`;

export function savePendingBatch(userId: string, batch: PendingBlockBatch): void {
  try {
    if (batch.runs.length === 0) localStorage.removeItem(keyFor(userId));
    else localStorage.setItem(keyFor(userId), JSON.stringify(batch));
  } catch {
    // Storage may be blocked; recovery is then simply unavailable.
  }
}

export function clearPendingBatch(userId: string): void {
  try {
    localStorage.removeItem(keyFor(userId));
  } catch {
    // Nothing to clear when storage is unavailable.
  }
}

function isPayload(value: unknown): value is ReplacementAssignmentPayload {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return ["class_id", "faculty_id", "room_code", "meeting_days", "time_slot_id"].every((key) => typeof item[key] === "string");
}

/** Null for a missing, unreadable or malformed entry. */
export function loadPendingBatch(userId: string): PendingBlockBatch | null {
  try {
    const raw = localStorage.getItem(keyFor(userId));
    if (raw === null) return null;
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return null;
    const { termId, scheduleId, runs } = value as Record<string, unknown>;
    if (typeof termId !== "string" || typeof scheduleId !== "string" || !Array.isArray(runs)) return null;
    const parsed: PendingBlockRun[] = [];
    for (const run of runs) {
      if (typeof run !== "object" || run === null) return null;
      const { blockId, jobId, edits, editedClassIds } = run as Record<string, unknown>;
      if (typeof blockId !== "string" || typeof jobId !== "string") return null;
      if (edits !== undefined && !(Array.isArray(edits) && edits.every(isPayload))) return null;
      if (editedClassIds !== undefined && !(Array.isArray(editedClassIds) && editedClassIds.every((id) => typeof id === "string"))) return null;
      parsed.push(
        edits === undefined
          ? { blockId, jobId }
          : {
            blockId,
            jobId,
            edits: edits as ReplacementAssignmentPayload[],
            ...(editedClassIds === undefined ? {} : { editedClassIds: editedClassIds as string[] }),
          },
      );
    }
    return { termId, scheduleId, runs: parsed };
  } catch {
    return null;
  }
}
