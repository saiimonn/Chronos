import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import {
  ApiError,
  api,
  type BlockReplacementPreviewResponse,
  type GAJobMetrics,
  type ReplacementAssignmentPayload,
} from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { savePendingBatch } from "../utils/pendingBlockBatch";

// Block generation lives above the router, so a "Generate all" batch keeps
// running while the user visits other pages. The Master Schedule page only
// reads and drives it. Closing or reloading the tab still stops the queue;
// runs already submitted are recovered from storage on the next visit.

// Between status checks of a running generation.
const POLL_INTERVAL_MS = 1500;
// Consecutive failed status checks before a run is reported as lost.
const MAX_POLL_FAILURES = 5;
// Submission refusals that apply to every Block, so the batch stops rather
// than failing each remaining Block for the same reason.
const TERM_WIDE_CODES = new Set([
  "TERM_NOT_READY",
  "INVALID_GA_CONFIGURATION",
  "RULE_SET_INVALID",
  "GUIDANCE_CHANGED",
  "SCHEDULE_FINALIZED",
  "NO_ACTIVE_TERM",
]);

export type RunPhase = "waiting" | "submitting" | "queued" | "running" | "loading" | "review" | "approving" | "failed" | "rejected";

export interface BlockRun {
  phase: RunPhase;
  jobId?: string;
  error?: string;
  /** The run finished but its proposal could not be loaded: reload, don't regenerate. */
  previewFailed?: boolean;
  preview?: BlockReplacementPreviewResponse;
  metrics?: GAJobMetrics | null;
  /** Classes changed by hand in review. */
  editedClassIds?: string[];
  /**
   * Set when re-checking the proposal against the latest schedule and roster
   * was refused outright (e.g. it gives a class to a teacher deactivated
   * since). The proposal cannot be approved until it is edited or regenerated.
   */
  refreshError?: string;
  queuedAt?: number;
}

export type Runs = Record<string, BlockRun>;

export interface BatchState {
  queue: string[];
  done: number;
  current: string | null;
  cancelling: boolean;
}

export const ACTIVE_PHASES: RunPhase[] = ["waiting", "submitting", "queued", "running", "loading"];
export const HELD_PHASES: RunPhase[] = [...ACTIVE_PHASES, "review", "approving"];

const sleep = (ms: number) => new Promise<void>((resolve) => { window.setTimeout(resolve, ms); });

export function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback;
}

export function toPayload(preview: BlockReplacementPreviewResponse): ReplacementAssignmentPayload[] {
  return preview.proposed_assignments.map(({ class_id, faculty_id, room_code, meeting_days, time_slot_id }) => (
    { class_id, faculty_id, room_code, meeting_days, time_slot_id }
  ));
}

interface Owner {
  termId: string;
  scheduleId: string;
}

interface BlockGenerationValue {
  runs: Runs;
  runsRef: MutableRefObject<Runs>;
  batch: BatchState | null;
  /** Bumped whenever the runs are reset, so a loop from before stops writing. */
  sessionRef: MutableRefObject<number>;
  /** The master schedule the page last loaded. */
  sessionScheduleRef: MutableRefObject<string | null>;
  /** The master schedule whose stored runs were already recovered (or started fresh). */
  recoveredRef: MutableRefObject<string | null>;
  /** The term and master schedule the runs belong to, for storage. */
  setOwner: (owner: Owner) => void;
  notice: string;
  setNotice: (notice: string) => void;
  updateRun: (blockId: string, patch: Partial<BlockRun> | null) => void;
  resetRuns: () => void;
  loadProposal: (blockId: string, jobId: string, scheduleId: string, session: number, edits?: ReplacementAssignmentPayload[], editedClassIds?: string[]) => Promise<void>;
  followJob: (blockId: string, jobId: string, scheduleId: string, session: number, edits?: ReplacementAssignmentPayload[], editedClassIds?: string[]) => Promise<void>;
  generateBlocks: (blockIds: string[], termId: string, scheduleId: string) => Promise<void>;
  stopBatch: () => void;
}

const BlockGenerationContext = createContext<BlockGenerationValue | null>(null);

export function BlockGenerationProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const userId = user?.user_id ?? user?.username ?? "";
  // The ref is the source of truth for the async generation loop, so a
  // decision made mid-loop never reads a stale render.
  const [runs, setRuns] = useState<Runs>({});
  const runsRef = useRef<Runs>({});
  const [batch, setBatch] = useState<BatchState | null>(null);
  const batchRef = useRef<{ cancelled: boolean } | null>(null);
  const [notice, setNotice] = useState("");
  const sessionRef = useRef(0);
  const sessionScheduleRef = useRef<string | null>(null);
  const recoveredRef = useRef<string | null>(null);
  const [owner, setOwnerState] = useState<Owner | null>(null);

  const setOwner = useCallback((next: Owner) => {
    setOwnerState((current) => (
      current?.termId === next.termId && current.scheduleId === next.scheduleId ? current : next
    ));
  }, []);

  const updateRun = useCallback((blockId: string, patch: Partial<BlockRun> | null) => {
    const next = { ...runsRef.current };
    if (patch === null) delete next[blockId];
    else next[blockId] = { ...(next[blockId] ?? { phase: "waiting" }), ...patch };
    runsRef.current = next;
    setRuns(next);
  }, []);

  const resetRuns = useCallback(() => {
    sessionRef.current += 1;
    if (batchRef.current) batchRef.current.cancelled = true;
    batchRef.current = null;
    runsRef.current = {};
    setRuns({});
    setBatch(null);
  }, []);

  // A different user (or a sign-out) never sees another user's runs.
  const lastUserRef = useRef(userId);
  useEffect(() => {
    if (lastUserRef.current === userId) return;
    lastUserRef.current = userId;
    resetRuns();
    sessionScheduleRef.current = null;
    recoveredRef.current = null;
    setOwnerState(null);
    setNotice("");
  }, [userId, resetRuns]);

  // Keep what is waiting for review across reloads, wherever the user is.
  useEffect(() => {
    if (!userId || !owner || recoveredRef.current !== owner.scheduleId) return;
    savePendingBatch(userId, {
      termId: owner.termId,
      scheduleId: owner.scheduleId,
      runs: Object.entries(runs)
        .filter(([, run]) => run.jobId && HELD_PHASES.includes(run.phase))
        .map(([blockId, run]) => ({
          blockId,
          jobId: run.jobId as string,
          ...(run.editedClassIds?.length && run.preview
            ? { edits: toPayload(run.preview), editedClassIds: run.editedClassIds }
            : {}),
        })),
    });
  }, [runs, userId, owner]);

  /** Loads a finished run's proposal (or the review edits made to it). */
  const loadProposal = useCallback(async (
    blockId: string,
    jobId: string,
    scheduleId: string,
    session: number,
    edits?: ReplacementAssignmentPayload[],
    editedClassIds?: string[],
  ) => {
    try {
      const preview = await api.previewBlockReplacement(scheduleId, blockId, edits ? { assignments: edits } : { job_id: jobId });
      if (session !== sessionRef.current) return;
      updateRun(blockId, {
        phase: "review",
        preview,
        error: undefined,
        refreshError: undefined,
        previewFailed: false,
        // Stored edits hold the whole proposal; the stored ids say which
        // classes were changed by hand (older entries lack them).
        editedClassIds: edits ? editedClassIds ?? edits.map((item) => item.class_id) : undefined,
      });
    } catch (caught) {
      if (session !== sessionRef.current) return;
      updateRun(blockId, {
        phase: "failed",
        previewFailed: true,
        error: `The run finished, but its proposal could not be loaded. ${errorMessage(caught, "")}`.trim(),
      });
    }
  }, [updateRun]);

  /** Polls one job to its end, then loads its proposal. */
  const followJob = useCallback(async (
    blockId: string,
    jobId: string,
    scheduleId: string,
    session: number,
    edits?: ReplacementAssignmentPayload[],
    editedClassIds?: string[],
  ) => {
    let failures = 0;
    for (let attempt = 0; ; attempt += 1) {
      if (attempt > 0) await sleep(POLL_INTERVAL_MS);
      if (session !== sessionRef.current) return;
      let job;
      try {
        job = await api.getGAJob(jobId);
        failures = 0;
      } catch (caught) {
        if (session !== sessionRef.current) return;
        const refused = caught instanceof ApiError && caught.status >= 400 && caught.status < 500 && caught.status !== 401;
        failures += 1;
        if (refused || failures >= MAX_POLL_FAILURES) {
          updateRun(blockId, { phase: "failed", error: errorMessage(caught, "Could not check generation progress.") });
          return;
        }
        continue;
      }
      if (session !== sessionRef.current) return;
      if (job.status === "queued" || job.status === "running") {
        const current = runsRef.current[blockId];
        if (current?.phase !== job.status) {
          updateRun(blockId, { phase: job.status, queuedAt: job.status === "queued" ? current?.queuedAt ?? Date.now() : undefined });
        }
        continue;
      }
      if (job.status === "succeeded") {
        if (!job.result) {
          updateRun(blockId, { phase: "failed", error: "The generation job completed without a result." });
          return;
        }
        updateRun(blockId, { phase: "loading", metrics: job.result.metrics });
        await loadProposal(blockId, jobId, scheduleId, session, edits, editedClassIds);
        return;
      }
      updateRun(blockId, {
        phase: "failed",
        error: job.error?.message ?? (job.status === "timed_out" ? "Generation exceeded its time limit." : "Generation failed before producing a proposal."),
      });
      return;
    }
  }, [loadProposal, updateRun]);

  /** Submits one Block and follows it; "stop" ends the batch. */
  const runBlock = useCallback(async (blockId: string, termId: string, scheduleId: string, session: number): Promise<"next" | "stop"> => {
    updateRun(blockId, { phase: "submitting", error: undefined, refreshError: undefined, preview: undefined, metrics: undefined, previewFailed: false, editedClassIds: undefined, jobId: undefined });
    // Proposals still waiting for review are passed along, so this Block is
    // generated around them instead of on top of them.
    const contextRuns = Object.entries(runsRef.current)
      .filter(([id, run]) => id !== blockId && run.jobId && (run.phase === "review" || run.phase === "approving"))
      .map(([, run]) => run);
    const contextJobIds = contextRuns.map((run) => run.jobId as string);
    // A proposal changed by hand is held as it now stands, not as generated:
    // otherwise the slot a class was moved into looks free to this Block.
    const contextEdits = contextRuns
      .filter((run) => run.editedClassIds?.length && run.preview)
      .map((run) => ({ job_id: run.jobId as string, assignments: toPayload(run.preview as BlockReplacementPreviewResponse) }));
    let jobId: string;
    try {
      const accepted = await api.submitGAJob({
        term_id: termId,
        block_id: blockId,
        base_schedule_id: scheduleId,
        ...(contextJobIds.length > 0 ? { context_job_ids: contextJobIds } : {}),
        ...(contextEdits.length > 0 ? { context_edits: contextEdits } : {}),
      });
      jobId = accepted.job_id;
      if (session !== sessionRef.current) return "stop";
      updateRun(blockId, { phase: accepted.status === "running" ? "running" : "queued", jobId, queuedAt: Date.now() });
    } catch (caught) {
      if (session !== sessionRef.current) return "stop";
      updateRun(blockId, { phase: "failed", error: errorMessage(caught, "Could not start generation.") });
      return caught instanceof ApiError && TERM_WIDE_CODES.has(caught.code) ? "stop" : "next";
    }
    await followJob(blockId, jobId, scheduleId, session);
    return session === sessionRef.current ? "next" : "stop";
  }, [followJob, updateRun]);

  /** Generates the Blocks one after another; a failure never stops the rest. */
  const generateBlocks = useCallback(async (blockIds: string[], termId: string, scheduleId: string) => {
    if (batchRef.current || blockIds.length === 0) return;
    const session = sessionRef.current;
    const control = { cancelled: false };
    batchRef.current = control;
    setOwner({ termId, scheduleId });
    setNotice("");
    for (const blockId of blockIds) updateRun(blockId, { phase: "waiting", error: undefined, preview: undefined, jobId: undefined, previewFailed: false });
    setBatch({ queue: blockIds, done: 0, current: null, cancelling: false });
    let stoppedBy = "";
    for (let index = 0; index < blockIds.length; index += 1) {
      if (control.cancelled || session !== sessionRef.current) break;
      const blockId = blockIds[index];
      setBatch((current) => current && { ...current, current: blockId, done: index });
      const outcome = await runBlock(blockId, termId, scheduleId, session);
      if (outcome === "stop") {
        stoppedBy = runsRef.current[blockId]?.error ?? "";
        break;
      }
    }
    if (session !== sessionRef.current) return;
    const unstarted = blockIds.filter((blockId) => runsRef.current[blockId]?.phase === "waiting");
    for (const blockId of unstarted) updateRun(blockId, null);
    batchRef.current = null;
    setBatch(null);
    const outcomes = blockIds.map((blockId) => runsRef.current[blockId]?.phase);
    const ready = outcomes.filter((phase) => phase === "review" || phase === "approving").length;
    const failed = outcomes.filter((phase) => phase === "failed").length;
    const parts = [`${ready} ${ready === 1 ? "proposal is" : "proposals are"} ready to review`];
    if (failed > 0) parts.push(`${failed} failed`);
    if (unstarted.length > 0) parts.push(`${unstarted.length} not started${stoppedBy ? ` (${stoppedBy})` : control.cancelled ? " (stopped)" : ""}`);
    setNotice(`Generation finished: ${parts.join(", ")}.`);
  }, [runBlock, updateRun, setOwner]);

  const stopBatch = useCallback(() => {
    if (!batchRef.current) return;
    batchRef.current.cancelled = true;
    setBatch((current) => current && { ...current, cancelling: true });
  }, []);

  // Loops still waiting on a timer stop at their next check.
  useEffect(() => () => {
    sessionRef.current += 1;
    if (batchRef.current) batchRef.current.cancelled = true;
  }, []);

  const value = useMemo<BlockGenerationValue>(() => ({
    runs, runsRef, batch, sessionRef, sessionScheduleRef, recoveredRef, setOwner, notice, setNotice,
    updateRun, resetRuns, loadProposal, followJob, generateBlocks, stopBatch,
  }), [runs, batch, setOwner, notice, updateRun, resetRuns, loadProposal, followJob, generateBlocks, stopBatch]);

  return <BlockGenerationContext.Provider value={value}>{children}</BlockGenerationContext.Provider>;
}

export function useBlockGeneration(): BlockGenerationValue {
  const value = useContext(BlockGenerationContext);
  if (!value) throw new Error("useBlockGeneration must be used inside BlockGenerationProvider");
  return value;
}
