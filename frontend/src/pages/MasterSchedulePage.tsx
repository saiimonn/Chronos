import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { formatCompleteness } from "../utils/formatCompleteness";
import {
  AlertTriangle,
  ArrowRight,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Clock3,
  ListChecks,
  Lock,
  LockOpen,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Square,
  Users,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  ApiError,
  api,
  conflictLabel,
  maxWorkloadFor,
  meetingEndTime,
  parseMeetingDays,
  type AssignmentUpdate,
  type BlockReplacementPreviewResponse,
  type BlockSection,
  type Faculty,
  type Readiness,
  type Schedule,
  type ScheduleAssignment,
  type ScheduleConflict,
  type ScheduleLifecycleResponse,
  type ScheduleValidation,
  type Term,
} from "../api/client";
import ConfirmDeleteDialog from "../components/modals/ConfirmDeleteDialog";
import Modal from "../components/modals/Modal";
import Sidebar from "../components/layout/Sidebar";
import BlockerFixLink from "../components/readiness/BlockerFixLink";
import ReadinessWarnings from "../components/readiness/ReadinessWarnings";
import TopBar from "../components/layout/TopBar";
import WeekScheduleGrid, { type WeekGridEntry } from "../components/schedule-grid/WeekScheduleGrid";
import { toMinutes } from "../components/schedule-grid/time";
import { useAuth } from "../hooks/useAuth";
import { usePeriodicSync } from "../hooks/usePeriodicSync";
import ClassEditor, { type ClassPlacement, type OccupiedMeeting } from "./master-schedule/ClassEditor";
import { compareProgramCodes, EMPTY_CATALOG, loadCatalog, type Catalog } from "./master-schedule/catalog";
import GenerationOrderControls from "./master-schedule/GenerationOrderControls";
import { loadGenerationOrder, orderBlockIds, saveGenerationOrder, type GenerationOrder } from "./master-schedule/generationOrder";
import { clearPendingBatch, loadPendingBatch } from "../utils/pendingBlockBatch";
import { ACTIVE_PHASES, HELD_PHASES, errorMessage, toPayload, useBlockGeneration, type BlockRun, type RunPhase } from "../state/BlockGeneration";

const BTN_PRIMARY = "inline-flex items-center justify-center gap-2 min-h-11 md:min-h-10 px-5 rounded-lg bg-primary text-on-primary font-semibold hover:bg-on-primary-fixed-variant focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed";
const BTN_SECONDARY = "inline-flex items-center justify-center gap-2 min-h-11 md:min-h-10 px-4 rounded-lg border border-outline-variant bg-surface-container-lowest text-on-surface text-sm hover:bg-surface-container-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50 disabled:cursor-not-allowed";
const CARD = "border border-outline-variant rounded-xl bg-surface-container-lowest";

// A queued run nobody has claimed for this long almost always means the job
// worker process is not running.
const WORKER_HINT_SECONDS = 30;
type ReviewView = "proposal" | "compare" | "approved";

type Editing =
  | { kind: "proposal"; blockId: string; classId: string; subjectCode: string; initial: ClassPlacement; isNew: boolean }
  | { kind: "approved"; assignment: ScheduleAssignment };

/** Classes the proposal leaves unplaced and hard conflicts touching its classes. */
function proposalProblems(preview: BlockReplacementPreviewResponse) {
  const classIds = new Set(preview.changed_values.map((item) => item.class_id));
  return {
    unplaced: preview.changed_values.filter((item) => item.proposed_assignment == null),
    conflicts: preview.validation.conflicts.filter((conflict) => conflict.class_ids.some((id) => classIds.has(id))),
  };
}

function proposalIsValid(preview: BlockReplacementPreviewResponse): boolean {
  const { unplaced, conflicts } = proposalProblems(preview);
  return unplaced.length === 0 && conflicts.length === 0;
}

/** A proposal that can be approved: valid, and not refused on its last re-check. */
function runIsValid(run: BlockRun | undefined | null): boolean {
  return Boolean(run?.preview && !run.refreshError && proposalIsValid(run.preview));
}

/**
 * The section a Block is paired with: A-B, C-D, E-F (backend
 * partner_section_letter). Paired Blocks of one program and year share every
 * LEC session and take each LAB at the same time, so neither half of a pair
 * can move those without the other.
 */
function partnerLetter(letter: string): string | null {
  if (letter.length !== 1 || letter < "A" || letter > "Z") return null;
  return String.fromCharCode(65 + ((letter.charCodeAt(0) - 65) ^ 1));
}

function findPartner(block: BlockSection, blocks: BlockSection[]): BlockSection | null {
  const letter = partnerLetter(block.section_letter);
  if (!letter) return null;
  return blocks.find((other) => other.term_id === block.term_id && other.program_code === block.program_code
    && other.year_level === block.year_level && other.section_letter === letter) ?? null;
}

/** Units per instructor, once per offering: a combined session is one load. */
function unitsByFaculty(
  items: { faculty_id: string; offering_id?: string; class_id: string; subject_code: string }[],
  unitsOf: (code: string) => number,
): Map<string, number> {
  const sessions = new Map<string, Map<string, number>>();
  for (const item of items) {
    const perOffering = sessions.get(item.faculty_id) ?? new Map<string, number>();
    perOffering.set(item.offering_id ?? item.class_id, unitsOf(item.subject_code));
    sessions.set(item.faculty_id, perOffering);
  }
  return new Map([...sessions].map(([facultyId, perOffering]) => [facultyId, [...perOffering.values()].reduce((sum, units) => sum + units, 0)]));
}

function conflictKey(conflict: ScheduleConflict): string {
  return `${conflict.code}|${conflict.resource_key.join(",")}|${conflict.class_ids.join(",")}|${conflict.time_slot_ids.join(",")}`;
}

function firstConflictByClass(conflicts: ScheduleConflict[]): Map<string, string> {
  const index = new Map<string, string>();
  for (const conflict of conflicts) {
    for (const classId of conflict.class_ids) {
      if (!index.has(classId)) index.set(classId, `${conflictLabel(conflict.code)}: ${conflict.message}`);
    }
  }
  return index;
}

export default function MasterSchedulePage() {
  const { user } = useAuth();
  const userId = user?.user_id ?? user?.username ?? "";
  const [term, setTerm] = useState<Term | null>(null);
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [validation, setValidation] = useState<ScheduleValidation | null>(null);
  const [catalog, setCatalog] = useState<Catalog>(EMPTY_CATALOG);
  const [loading, setLoading] = useState(true);
  const [needsActiveTerm, setNeedsActiveTerm] = useState(false);
  const [error, setError] = useState("");
  const [selectedBlockId, setSelectedBlockId] = useState("");
  const selectedBlockRef = useRef("");
  selectedBlockRef.current = selectedBlockId;
  const [view, setView] = useState<ReviewView>("proposal");
  const [highlightClassId, setHighlightClassId] = useState("");
  const [editing, setEditing] = useState<Editing | null>(null);
  const [rejectArmed, setRejectArmed] = useState<string | null>(null);
  // Blocks ticked in the list. One selection serves every bulk action: each
  // acts on the ticked Blocks it applies to (generate the ones that can be
  // generated, approve the valid proposals, discard proposals).
  const [bulkSelection, setBulkSelection] = useState<Set<string>>(() => new Set());
  const [bulkApproving, setBulkApproving] = useState<{ done: number; total: number } | null>(null);
  const [bulkRejectArmed, setBulkRejectArmed] = useState(false);
  // Narrow the Blocks list (and what "Select all" ticks) by program and year.
  const [programFilter, setProgramFilter] = useState("");
  const [yearFilter, setYearFilter] = useState("");
  // Which program and year a batch generates first (remembered per browser).
  const [generationOrder, setGenerationOrderState] = useState<GenerationOrder>(loadGenerationOrder);
  const setGenerationOrder = useCallback((next: GenerationOrder) => {
    setGenerationOrderState(next);
    saveGenerationOrder(next);
  }, []);
  const [finalizing, setFinalizing] = useState(false);
  const [finalizationOpen, setFinalizationOpen] = useState(false);
  const [finalizationError, setFinalizationError] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [classCounts, setClassCounts] = useState<Map<string, number>>(() => new Map());
  const [now, setNow] = useState(() => Date.now());
  // A lock, unlock or clear in flight, by Block, and the last refusal.
  const [lockBusy, setLockBusy] = useState<string | null>(null);
  const [lockError, setLockError] = useState<{ blockId: string; message: string } | null>(null);
  // "Regenerate pair" waiting for confirmation, by Block.
  const [pairArmed, setPairArmed] = useState<string | null>(null);
  const [loadsOpen, setLoadsOpen] = useState(false);

  const termRef = useRef<Term | null>(null);
  termRef.current = term;
  const scheduleRef = useRef<Schedule | null>(null);
  scheduleRef.current = schedule;
  const scheduleMutationRef = useRef(0);
  const facultyByIdRef = useRef<Map<string, Faculty>>(new Map());
  // Generation runs live above the router, so a batch keeps going while the
  // user is on another page.
  const {
    runs, runsRef, batch, sessionRef, sessionScheduleRef, recoveredRef, setOwner, notice, setNotice,
    updateRun, resetRuns: resetGenerationRuns, loadProposal, followJob, generateBlocks: startGeneration, stopBatch,
  } = useBlockGeneration();

  const resetRuns = useCallback(() => {
    resetGenerationRuns();
    setBulkSelection(new Set());
    setBulkApproving(null);
    setBulkRejectArmed(false);
    setEditing((current) => (current?.kind === "proposal" ? null : current));
  }, [resetGenerationRuns]);

  const generateBlocks = useCallback(async (blockIds: string[]) => {
    const activeTerm = termRef.current;
    const master = scheduleRef.current;
    if (!activeTerm || !master) return;
    // Generated Blocks leave the selection, so their proposals start unticked.
    setBulkSelection((current) => new Set([...current].filter((id) => !blockIds.includes(id))));
    await startGeneration(blockIds, activeTerm.term_id, master.schedule_id);
  }, [startGeneration]);

  const { sync } = usePeriodicSync(
    async ({ isBackground, isStale }) => {
      const scheduleMutation = scheduleMutationRef.current;
      if (!isBackground) {
        setLoading(true);
        setError("");
      }
      try {
        const activeTerm = await api.getActiveTerm();
        if (isStale()) return;
        // A term with no master schedule yet is a normal starting state.
        const [master, termReadiness] = await Promise.all([
          api.getMasterSchedule(activeTerm.term_id).catch((caught: unknown) => {
            if (caught instanceof ApiError && caught.status === 404) return null;
            throw caught;
          }),
          api.getReadiness(activeTerm.term_id),
        ]);
        if (isStale()) return;
        const [termCatalog, masterValidation] = await Promise.all([
          loadCatalog(activeTerm.term_id),
          master ? api.validateSchedule(master.schedule_id) : Promise.resolve(null),
        ]);
        if (isStale() || scheduleMutation !== scheduleMutationRef.current) return;
        // Another term or master schedule: runs of the old one no longer apply.
        const masterId = master?.schedule_id ?? null;
        if (sessionScheduleRef.current !== null && sessionScheduleRef.current !== masterId && Object.keys(runsRef.current).length > 0) {
          resetRuns();
          setNotice("The active term or working schedule changed. Proposals for the previous one are no longer shown.");
        } else if (master?.finalized_at && Object.keys(runsRef.current).length > 0) {
          resetRuns();
          if (userId) clearPendingBatch(userId);
          setNotice("This master schedule was finalized elsewhere. Its pending proposals were cleared.");
        }
        sessionScheduleRef.current = masterId;
        setTerm(activeTerm);
        setSchedule(master);
        setReadiness(termReadiness);
        setCatalog(termCatalog);
        setValidation(masterValidation);
        setNeedsActiveTerm(false);
        setError("");
        setSelectedBlockId((current) => (
          termCatalog.blocks.some((block) => block.block_id === current) ? current : termCatalog.blocks[0]?.block_id ?? ""
        ));
      } catch (caught: unknown) {
        if (isStale()) return;
        if (caught instanceof ApiError && caught.code === "NO_ACTIVE_TERM") {
          resetRuns();
          sessionScheduleRef.current = null;
          setTerm(null);
          setSchedule(null);
          setReadiness(null);
          setValidation(null);
          setCatalog(EMPTY_CATALOG);
          setSelectedBlockId("");
          setNeedsActiveTerm(true);
          setError("");
          return;
        }
        if (!isBackground) setError(errorMessage(caught, "Could not load the master schedule."));
      } finally {
        if (!isStale() && !isBackground) setLoading(false);
      }
    },
    { intervalMs: 5000, listenTermChanged: true },
  );

  // Classes per Block, re-read when the Block list or the required total
  // changes, so an empty Block is never offered for generation.
  const blockIdsKey = catalog.blocks.map((block) => block.block_id).join(",");
  const requiredClassCount = validation?.required_class_count;
  useEffect(() => {
    let stale = false;
    const blockIds = blockIdsKey ? blockIdsKey.split(",") : [];
    void (async () => {
      try {
        const entries = await Promise.all(blockIds.map(async (blockId) =>
          [blockId, (await api.listBlockClasses(blockId)).items.length] as const));
        if (!stale) setClassCounts(new Map(entries));
      } catch {
        // Unknown counts are treated as having classes, the safe default.
        if (!stale) setClassCounts(new Map());
      }
    })();
    return () => { stale = true; };
  }, [blockIdsKey, requiredClassCount]);

  // ---- Generation ---------------------------------------------------------

  // Recover runs waiting for review after a reload or navigation. Each is
  // polled (if still running) and previewed again, so the server revalidates it.
  useEffect(() => {
    if (!userId || !term || !schedule) return;
    setOwner({ termId: term.term_id, scheduleId: schedule.schedule_id });
    if (recoveredRef.current === schedule.schedule_id) return;
    recoveredRef.current = schedule.schedule_id;
    const stored = loadPendingBatch(userId);
    if (!stored || stored.termId !== term.term_id || stored.scheduleId !== schedule.schedule_id) return;
    if (schedule.finalized_at) {
      clearPendingBatch(userId);
      return;
    }
    const session = sessionRef.current;
    for (const run of stored.runs) {
      updateRun(run.blockId, { phase: "loading", jobId: run.jobId });
      void followJob(run.blockId, run.jobId, schedule.schedule_id, session, run.edits, run.editedClassIds);
    }
    setSelectedBlockId(stored.runs[0]?.blockId ?? "");
    setNotice(`Restored ${stored.runs.length} ${stored.runs.length === 1 ? "proposal" : "proposals"} from your last visit.`);
  }, [userId, term, schedule, followJob, updateRun, setOwner, recoveredRef, sessionRef, setNotice]);

  // A one-second clock only while a run waits in the queue, for the worker hint.
  const anyQueued = Object.values(runs).some((run) => run.phase === "queued");
  useEffect(() => {
    if (!anyQueued) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [anyQueued]);

  // ---- Review -------------------------------------------------------------

  /** Re-checks every other proposal against the master schedule as it is now. */
  const refreshProposals = useCallback(async (scheduleId: string) => {
    const session = sessionRef.current;
    await Promise.all(Object.entries(runsRef.current).map(async ([blockId, run]) => {
      if (run.phase !== "review" || !run.preview) return;
      try {
        const preview = await api.previewBlockReplacement(scheduleId, blockId, { assignments: toPayload(run.preview) });
        if (session === sessionRef.current && runsRef.current[blockId]?.phase === "review") updateRun(blockId, { preview, refreshError: undefined });
      } catch (caught) {
        // A refusal (e.g. a teacher deactivated since the run) blocks approval
        // until the proposal is changed; a network blip only delays the news,
        // and approval re-checks anyway.
        if (session !== sessionRef.current || runsRef.current[blockId]?.phase !== "review") return;
        if (caught instanceof ApiError && caught.status === 409) {
          const facultyId = typeof caught.details?.faculty_id === "string" ? caught.details.faculty_id : "";
          const name = facultyByIdRef.current.get(facultyId)?.full_name;
          updateRun(blockId, {
            refreshError: caught.code === "FACULTY_INACTIVE"
              ? `${name ?? "An instructor in this proposal"} was marked as not currently teaching. Generate the Block again, or reassign their classes.`
              : caught.message,
          });
        }
      }
    }));
  }, [updateRun]);

  const blocksById = useMemo(() => new Map(catalog.blocks.map((block) => [block.block_id, block])), [catalog.blocks]);
  const blockName = (blockId: string) => blocksById.get(blockId)?.name ?? "The Block";

  /**
   * Approves one proposal: re-checks it against the master schedule as it is
   * now (another Block may have been approved or edited since it was loaded),
   * then confirms it. Leaves the Block in review with the reason on refusal.
   */
  async function approveBlock(blockId: string): Promise<"approved" | "refused" | "stale"> {
    const run = runsRef.current[blockId];
    const master = scheduleRef.current;
    if (!run?.preview || !master || run.phase !== "review") return "refused";
    const session = sessionRef.current;
    updateRun(blockId, { phase: "approving", error: undefined });
    try {
      const fresh = await api.previewBlockReplacement(master.schedule_id, blockId, { assignments: toPayload(run.preview) });
      if (session !== sessionRef.current) return "stale";
      if (!proposalIsValid(fresh)) {
        updateRun(blockId, {
          phase: "review",
          preview: fresh,
          error: "Not approved. Checked against the latest master schedule, this proposal now has a conflict or an unplaced class. Edit the marked classes or generate the Block again.",
        });
        return "refused";
      }
      const confirmed = await api.confirmBlockReplacement(master.schedule_id, blockId, { preview_token: fresh.preview_token });
      if (session !== sessionRef.current) return "stale";
      scheduleMutationRef.current += 1;
      scheduleRef.current = confirmed.schedule;
      setSchedule(confirmed.schedule);
      setValidation(confirmed.validation);
      updateRun(blockId, null);
      return "approved";
    } catch (caught) {
      if (session !== sessionRef.current) return "stale";
      updateRun(blockId, { phase: "review", error: errorMessage(caught, "Could not approve this proposal.") });
      return "refused";
    }
  }

  /** The next proposal waiting for review after `blockId`, in Block order. */
  function nextInReview(blockId: string) {
    const order = catalog.blocks.map((block) => block.block_id);
    const start = order.indexOf(blockId);
    const rotated = [...order.slice(start + 1), ...order.slice(0, start)];
    return rotated.find((id) => runsRef.current[id]?.phase === "review");
  }

  async function approve(blockId: string) {
    setNotice("");
    const outcome = await approveBlock(blockId);
    if (outcome !== "approved") return;
    setRejectArmed(null);
    setBulkSelection((current) => {
      const next = new Set(current);
      next.delete(blockId);
      return next;
    });
    // Approve-and-continue: open the next proposal waiting for review.
    const next = nextInReview(blockId);
    if (next) {
      setSelectedBlockId(next);
      setView("proposal");
    }
    setNotice(`${blockName(blockId)} was approved and added to the master schedule.${next ? ` Now reviewing ${blockName(next)}.` : ""}`);
    if (scheduleRef.current) void refreshProposals(scheduleRef.current.schedule_id);
  }

  /**
   * Approves the selected proposals one after another. Each is re-checked
   * against the master as the earlier ones left it, so a proposal that now
   * clashes with one approved moments before is held back, not saved.
   */
  async function approveSelected(blockIds: string[]) {
    if (blockIds.length === 0 || bulkApproving) return;
    if (Object.values(runsRef.current).some((run) => run.phase === "approving")) return;
    const session = sessionRef.current;
    setNotice("");
    setRejectArmed(null);
    setBulkApproving({ done: 0, total: blockIds.length });
    const approved: string[] = [];
    const held: string[] = [];
    for (const blockId of blockIds) {
      const outcome = await approveBlock(blockId);
      if (outcome === "stale" || session !== sessionRef.current) {
        setBulkApproving(null);
        return;
      }
      (outcome === "approved" ? approved : held).push(blockId);
      setBulkApproving({ done: approved.length + held.length, total: blockIds.length });
    }
    setBulkApproving(null);
    // Keep the held-back ones (and any ticked ones that still need fixes) selected.
    setBulkSelection((current) => new Set([...held, ...[...current].filter((id) => !blockIds.includes(id))]));
    const parts: string[] = [];
    if (approved.length > 0) {
      parts.push(`${approved.length} ${approved.length === 1 ? "Block was" : "Blocks were"} approved and added to the master schedule.`);
    }
    if (held.length > 0) {
      parts.push(`${held.map(blockName).join(", ")} ${held.length === 1 ? "was" : "were"} not approved: checked against the latest master schedule, ${held.length === 1 ? "it" : "each"} now has a conflict or could not be saved. ${held.length === 1 ? "It stays" : "They stay"} selected for review.`);
      setSelectedBlockId(held[0]);
      setView("proposal");
    } else {
      const current = runsRef.current[selectedBlockRef.current];
      if (!current || current.phase !== "review") {
        const next = nextInReview(blockIds[blockIds.length - 1]);
        if (next) {
          setSelectedBlockId(next);
          setView("proposal");
        }
      }
    }
    setNotice(parts.join(" "));
    if (approved.length > 0 && scheduleRef.current) void refreshProposals(scheduleRef.current.schedule_id);
  }

  function discardProposal(blockId: string) {
    updateRun(blockId, { phase: "rejected", preview: undefined, jobId: undefined, error: undefined, refreshError: undefined, metrics: undefined, editedClassIds: undefined });
  }

  /** Paired Blocks whose proposals are still in review after discarding these. */
  function partnersLeftInReview(blockIds: string[]): string[] {
    return blockIds.flatMap((id) => {
      const partnerId = rowsById.get(id)?.partner?.block_id;
      const phase = partnerId ? runsRef.current[partnerId]?.phase : undefined;
      return partnerId && !blockIds.includes(partnerId) && (phase === "review" || phase === "approving") ? [partnerId] : [];
    });
  }

  function reject(blockId: string, withPartner = false) {
    const partnerId = rowsById.get(blockId)?.partner?.block_id;
    const ids = withPartner && partnerId ? [blockId, partnerId] : [blockId];
    for (const id of ids) discardProposal(id);
    setRejectArmed(null);
    const left = partnersLeftInReview(ids);
    setNotice(
      `${ids.length === 1 ? `The proposal for ${blockName(blockId)} was` : `The proposals for ${ids.map(blockName).join(" and ")} were`} not approved. Nothing in the master schedule changed.`
      + (left.length > 0
        ? ` ${blockName(left[0])} is still in review and shares lectures and lab times with it, so generating ${blockName(blockId)} alone fits it around ${blockName(left[0])}'s proposal. Use Regenerate pair to move them together.`
        : ids.length > 1 ? " Use Regenerate pair to generate both afresh." : ""),
    );
  }

  function rejectSelected(blockIds: string[]) {
    if (blockIds.length === 0 || bulkApproving) return;
    for (const blockId of blockIds) discardProposal(blockId);
    setBulkRejectArmed(false);
    setRejectArmed(null);
    setBulkSelection((current) => new Set([...current].filter((id) => !blockIds.includes(id))));
    const left = partnersLeftInReview(blockIds);
    setNotice(
      `${blockIds.length} ${blockIds.length === 1 ? "proposal was" : "proposals were"} not approved (${blockIds.map(blockName).join(", ")}). `
      + "Nothing in the master schedule changed. Use Regenerate not approved to generate them again."
      + (left.length > 0
        ? ` Their paired ${left.length === 1 ? "Block" : "Blocks"} ${left.map(blockName).join(", ")} ${left.length === 1 ? "is" : "are"} still in review and hold the shared lecture and lab times; discard ${left.length === 1 ? "it" : "them"} too, or use Regenerate pair, to move those times.`
        : ""),
    );
  }

  async function saveProposalEdit(blockId: string, classId: string, value: ClassPlacement) {
    const run = runsRef.current[blockId];
    const master = scheduleRef.current;
    if (!run?.preview || !master) throw new Error("The proposal is no longer open.");
    const current = toPayload(run.preview);
    const edited = { class_id: classId, ...value };
    const assignments = current.some((item) => item.class_id === classId)
      ? current.map((item) => (item.class_id === classId ? edited : item))
      : [...current, edited];
    // The server's validator refuses the change if it lands in any hard
    // conflict; the error (with its reasons) is shown by the editor.
    // The paired Block's proposal follows in the same step: a shared LEC is
    // one session (same Offering), so it takes every field; a paired LAB
    // takes only the days and time (its teacher and room differ by design).
    const mirror = pairedProposalEdit(blockId, classId, value);
    // A partner already approved holds the shared session in the master
    // schedule instead: move it there first (the approved edit moves the whole
    // pair), so this proposal is checked against the moved session.
    const approvedMove = mirror ? null : approvedPairedMove(blockId, classId, value);
    const partnerFailed = (partnerId: string) => (caught: unknown) => {
      const reason = caught instanceof Error ? caught.message : "it could not be checked";
      throw caught instanceof ApiError
        ? new ApiError(caught.status, caught.code, `${blockName(partnerId)} shares this session and would have to move too, but ${reason.charAt(0).toLowerCase()}${reason.slice(1)}`, caught.details)
        : new Error(`${blockName(partnerId)} shares this session and would have to move too, but ${reason}`);
    };
    if (approvedMove) {
      applyLifecycle(await api.updateAssignment(master.schedule_id, approvedMove.record.record_id, approvedMove.update)
        .catch(partnerFailed(approvedMove.record.block_id)));
    }
    let preview: BlockReplacementPreviewResponse;
    let partnerPreview: typeof preview | null;
    try {
      preview = await api.previewBlockReplacement(master.schedule_id, blockId, { assignments, strict_class_ids: [classId] });
      partnerPreview = mirror
        ? await api.previewBlockReplacement(master.schedule_id, mirror.blockId, { assignments: mirror.assignments, strict_class_ids: [mirror.classId] })
          .catch(partnerFailed(mirror.blockId))
        : null;
      if (runsRef.current[blockId]?.phase !== "review" || (mirror && runsRef.current[mirror.blockId]?.phase !== "review")) {
        throw new Error("This proposal was approved or discarded while you were editing, so the change was not applied.");
      }
    } catch (caught) {
      // Put the approved partner back, so a refused edit changes nothing.
      if (approvedMove) {
        await api.updateAssignment(master.schedule_id, approvedMove.record.record_id, approvedMove.original)
          .then(applyLifecycle)
          .catch(() => sync(true));
      }
      throw caught;
    }
    updateRun(blockId, {
      preview,
      error: undefined,
      refreshError: undefined,
      editedClassIds: [...new Set([...(runsRef.current[blockId]?.editedClassIds ?? []), classId])],
    });
    if (mirror && partnerPreview) {
      updateRun(mirror.blockId, {
        preview: partnerPreview,
        error: undefined,
        refreshError: undefined,
        editedClassIds: [...new Set([...(runsRef.current[mirror.blockId]?.editedClassIds ?? []), mirror.classId])],
      });
    }
    setEditing(null);
    setHighlightClassId(classId);
    setNotice(
      "The change was checked against the approved schedule and the rest of this proposal, and applied to the proposal."
      + (mirror ? ` ${blockName(mirror.blockId)}'s proposal moved with it, since the pair shares this session.` : "")
      + (approvedMove ? ` ${blockName(approvedMove.record.block_id)}'s approved session moved with it, since the pair shares it.` : "")
      + " Approve the Block to save it.",
    );
    if (approvedMove) void refreshProposals(master.schedule_id);
  }

  /** The editor's note on a paired Block whose proposal moves with this class. */
  function proposalPairNote(blockId: string, classId: string): string | undefined {
    const item = runsRef.current[blockId]?.preview?.proposed_assignments.find((candidate) => candidate.class_id === classId);
    if (!item) return undefined;
    // Any change to the shared fields makes the partner follow; probe with one.
    const probeValue = { ...item, time_slot_id: `${item.time_slot_id}~` };
    const probe = pairedProposalEdit(blockId, classId, probeValue);
    const isLab = subjectsByCode.get(item.subject_code)?.subject_type === "LAB";
    if (!probe) {
      const approved = approvedPairedMove(blockId, classId, probeValue);
      if (!approved) return undefined;
      const name = blockName(approved.record.block_id);
      const locked = rowsById.get(approved.record.block_id)?.locked ? ` ${name} is locked, so unlock it first.` : "";
      return isLab
        ? `${name} (approved) takes this lab at the same time: changing the days or time window moves its approved lab too.${locked}`
        : `${name} (approved) shares this lecture: changing the days, time window, instructor or room moves its approved session too.${locked}`;
    }
    return isLab
      ? `${blockName(probe.blockId)} takes this lab at the same time: changing the days or time window moves its proposal too.`
      : `${blockName(probe.blockId)} shares this lecture: changing the days, time window, instructor or room moves its proposal too.`;
  }

  /** The editor's note on the paired Block an approved edit also moves. */
  function approvedPairNote(assignment: ScheduleAssignment): string | undefined {
    const partnerId = rowsById.get(assignment.block_id)?.partner?.block_id;
    if (!partnerId) return undefined;
    const isLab = subjectsByCode.get(assignment.subject_code)?.subject_type === "LAB";
    const shared = assignments.some((item) => item.block_id === partnerId && (isLab
      ? item.subject_code === assignment.subject_code
      : assignment.offering_id !== undefined && item.offering_id === assignment.offering_id));
    if (!shared) return undefined;
    return isLab
      ? `${blockName(partnerId)} takes this lab at the same time: changing the days or time window moves it too, so it must be unlocked as well.`
      : `${blockName(partnerId)} shares this lecture: saving moves it too, so it must be unlocked as well.`;
  }

  /**
   * The paired Block's proposal with `classId`'s shared session moved to
   * `value`, or null when the partner has no proposal in review holding it or
   * nothing would change.
   */
  function pairedProposalEdit(blockId: string, classId: string, value: ClassPlacement) {
    const partnerId = rowsById.get(blockId)?.partner?.block_id;
    const run = runsRef.current[blockId];
    const partnerRun = partnerId ? runsRef.current[partnerId] : undefined;
    if (!partnerId || !partnerRun?.preview || partnerRun.phase !== "review") return null;
    const edited = run?.preview?.proposed_assignments.find((item) => item.class_id === classId);
    if (!edited) return null;
    const isLab = subjectsByCode.get(edited.subject_code)?.subject_type === "LAB";
    const target = partnerRun.preview.proposed_assignments.find((item) => (isLab
      ? item.subject_code === edited.subject_code
      : edited.offering_id !== undefined && item.offering_id === edited.offering_id));
    if (!target) return null;
    const moved: ClassPlacement = isLab
      ? { faculty_id: target.faculty_id, room_code: target.room_code, meeting_days: value.meeting_days, time_slot_id: value.time_slot_id }
      : value;
    if (moved.faculty_id === target.faculty_id && moved.room_code === target.room_code
      && moved.meeting_days === target.meeting_days && moved.time_slot_id === target.time_slot_id) return null;
    const assignments = toPayload(partnerRun.preview).map((item) => (
      item.class_id === target.class_id ? { class_id: target.class_id, ...moved } : item
    ));
    return { blockId: partnerId, classId: target.class_id, assignments };
  }

  /**
   * The paired Block's approved record holding `classId`'s shared session,
   * with the update that moves it to `value` and the one that puts it back,
   * when the partner has no proposal in review and something would change.
   */
  function approvedPairedMove(blockId: string, classId: string, value: ClassPlacement) {
    const partnerId = rowsById.get(blockId)?.partner?.block_id;
    if (!partnerId || runsRef.current[partnerId]?.phase === "review") return null;
    const edited = runsRef.current[blockId]?.preview?.proposed_assignments.find((item) => item.class_id === classId);
    if (!edited) return null;
    const isLab = subjectsByCode.get(edited.subject_code)?.subject_type === "LAB";
    const record = assignments.find((item) => item.block_id === partnerId && (isLab
      ? item.subject_code === edited.subject_code
      : edited.offering_id !== undefined && item.offering_id === edited.offering_id));
    if (!record) return null;
    // A paired LAB shares only its days and time; its teacher and room differ by design.
    const update: AssignmentUpdate = isLab ? { meeting_days: value.meeting_days, time_slot_id: value.time_slot_id } : { ...value };
    const current: AssignmentUpdate = {
      faculty_id: record.faculty_id,
      room_code: record.room_code ?? undefined,
      meeting_days: record.meeting_days ?? undefined,
      time_slot_id: record.time_slot_id,
    };
    const fields = Object.keys(update) as (keyof AssignmentUpdate)[];
    if (fields.every((field) => update[field] === current[field])) return null;
    const original = Object.fromEntries(fields.map((field) => [field, current[field]])) as AssignmentUpdate;
    return { record, update, original };
  }

  async function saveApprovedEdit(assignment: ScheduleAssignment, value: ClassPlacement) {
    const master = scheduleRef.current;
    if (!master) return;
    const result: ScheduleLifecycleResponse = await api.updateAssignment(master.schedule_id, assignment.record_id, value);
    scheduleMutationRef.current += 1;
    setSchedule(result.schedule);
    setValidation(result.validation);
    setEditing(null);
    setHighlightClassId(assignment.class_id);
    setNotice(
      `Assignment saved. ${formatCompleteness(result.validation.completeness_percentage)} complete, `
      + `${result.validation.hard_conflict_count} conflict${result.validation.hard_conflict_count === 1 ? "" : "s"}.`,
    );
    void refreshProposals(result.schedule.schedule_id);
  }

  function applyLifecycle(result: ScheduleLifecycleResponse) {
    scheduleMutationRef.current += 1;
    scheduleRef.current = result.schedule;
    setSchedule(result.schedule);
    setValidation(result.validation);
  }

  /** Unlock an approved Block for editing, or lock it again once it is clean. */
  async function changeLock(blockId: string, action: "unlock" | "lock") {
    const master = scheduleRef.current;
    if (!master || lockBusy) return;
    setLockBusy(blockId);
    setLockError(null);
    setNotice("");
    try {
      const result = action === "unlock"
        ? await api.unlockBlock(master.schedule_id, blockId)
        : await api.lockBlock(master.schedule_id, blockId);
      applyLifecycle(result);
      setNotice(action === "unlock"
        ? `${blockName(blockId)} is unlocked. Select a class to change it, or generate it again, then lock it.`
        : `${blockName(blockId)} is locked. Its week is read-only until it is unlocked.`);
      void refreshProposals(result.schedule.schedule_id);
    } catch (caught) {
      setLockError({ blockId, message: errorMessage(caught, `Could not ${action} ${blockName(blockId)}.`) });
      // Another session may have changed the lock meanwhile: show what is true now.
      sync(true);
    } finally {
      setLockBusy(null);
    }
  }

  /**
   * Generates a pair (A-B, C-D, ...) afresh. Paired Blocks share every LEC
   * session and take each LAB at the same time, so a Block regenerated alone
   * is held to whatever its partner already has (approved or proposed).
   * Clearing both unlocked weeks and discarding both proposals first lets the
   * pair move those sessions together; the first Block is then generated
   * freely and its partner around it.
   */
  async function regeneratePair(blockId: string) {
    const row = rowsById.get(blockId);
    const master = scheduleRef.current;
    if (!row?.partner || !master) return;
    const ids = [row.block, row.partner]
      .sort((left, right) => left.section_letter.localeCompare(right.section_letter))
      .map((block) => block.block_id);
    setPairArmed(null);
    setRejectArmed(null);
    setLockError(null);
    setNotice("");
    setLockBusy(blockId);
    try {
      for (const id of ids) {
        if ((rowsById.get(id)?.accepted.length ?? 0) > 0) applyLifecycle(await api.clearBlock(master.schedule_id, id));
        if (runsRef.current[id]) updateRun(id, null);
      }
    } catch (caught) {
      setLockError({ blockId, message: errorMessage(caught, "Could not clear the pair's weeks.") });
      sync(true);
      return;
    } finally {
      setLockBusy(null);
    }
    await generateBlocks(ids);
  }

  async function createMasterSchedule() {
    if (!term) return;
    setError("");
    try {
      const created = await api.createMasterSchedule(term.term_id);
      scheduleMutationRef.current += 1;
      setSchedule(created);
    } catch (caught) {
      // Another session may have created it first: show the one that exists.
      if (caught instanceof ApiError && caught.code === "MASTER_SCHEDULE_EXISTS") {
        sync(false);
        return;
      }
      setError(errorMessage(caught, "Could not create the master schedule."));
    }
  }

  async function finalizeMasterSchedule() {
    if (!schedule || finalizing) return;
    const scheduleId = schedule.schedule_id;
    setFinalizing(true);
    setFinalizationError("");
    try {
      const result = await api.finalizeSchedule(scheduleId);
      if (scheduleRef.current?.schedule_id !== scheduleId) return;
      scheduleMutationRef.current += 1;
      setSchedule(result.schedule);
      setValidation(result.validation);
      setFinalizationOpen(false);
      setNotice("The server finalized this master schedule after independent validation.");
    } catch (caught) {
      setFinalizationError(errorMessage(caught, "Could not finalize the master schedule."));
    } finally {
      setFinalizing(false);
    }
  }

  // ---- Derived view state -------------------------------------------------

  const assignments = useMemo(() => schedule?.assignments ?? [], [schedule]);
  const facultyById = useMemo(() => new Map(catalog.faculty.map((member) => [member.faculty_id, member])), [catalog.faculty]);
  facultyByIdRef.current = facultyById;
  const subjectsByCode = useMemo(() => new Map(catalog.subjects.map((subject) => [subject.subject_code, subject])), [catalog.subjects]);
  const slotsById = useMemo(() => new Map(catalog.timeSlots.map((slot) => [slot.time_slot_id, slot])), [catalog.timeSlots]);
  const finalized = schedule?.finalized_at != null;
  const conflictsByClass = useMemo(() => firstConflictByClass(validation?.conflicts ?? []), [validation]);

  const gridBounds = useMemo(() => {
    if (catalog.timeSlots.length === 0) return { start: 7 * 60 + 30, end: 18 * 60 };
    return {
      start: Math.min(...catalog.timeSlots.map((slot) => toMinutes(slot.start_time))),
      end: Math.max(...catalog.timeSlots.map((slot) => toMinutes(slot.end_time))),
    };
  }, [catalog.timeSlots]);

  const unlockedIds = useMemo(() => new Set(schedule?.unlocked_block_ids ?? []), [schedule]);
  const blockRows = catalog.blocks.map((block) => {
    const accepted = assignments.filter((assignment) => assignment.block_id === block.block_id);
    const conflictCount = accepted.filter((assignment) => conflictsByClass.has(assignment.class_id)).length;
    const empty = classCounts.get(block.block_id) === 0;
    // Approved means locked: read-only until someone unlocks it.
    const unlocked = accepted.length > 0 && unlockedIds.has(block.block_id);
    const locked = accepted.length > 0 && !unlocked;
    const run = runs[block.block_id] as BlockRun | undefined;
    // Generation applies to a Block with classes that is not locked and has
    // no run in flight or waiting for review: never generated, discarded,
    // failed, or unlocked to be generated again.
    const generatable = !empty && !locked && !(run && HELD_PHASES.includes(run.phase));
    return { block, accepted, conflictCount, empty, unlocked, locked, generatable, run, partner: findPartner(block, catalog.blocks) };
  });
  const rowsById = new Map(blockRows.map((row) => [row.block.block_id, row]));
  const selectedRow = rowsById.get(selectedBlockId) ?? null;
  const reviewRows = blockRows.filter((row) => row.run?.phase === "review" || row.run?.phase === "approving");
  // Proposals that can be approved as they stand: in review, every class
  // placed, no conflict error.
  const approvableIds = reviewRows.filter((row) => row.run?.phase === "review" && runIsValid(row.run)).map((row) => row.block.block_id);
  const programOptions = [...new Set(catalog.blocks.map((block) => block.program_code))].sort(compareProgramCodes);
  const yearOptions = [...new Set(catalog.blocks.map((block) => block.year_level))].sort((a, b) => a - b);
  const filterActive = programFilter !== "" || yearFilter !== "";
  const matchesFilter = (block: BlockSection) =>
    (programFilter === "" || block.program_code === programFilter) && (yearFilter === "" || String(block.year_level) === yearFilter);
  const visibleRows = blockRows.filter((row) => matchesFilter(row.block));
  // Any Block the filter shows can be ticked, except one with nothing to do
  // (no classes and nothing approved). The filters make the batches: narrow
  // to a program and year, select all, then generate or approve them.
  const selectableIds = visibleRows.filter((row) => !row.empty || row.accepted.length > 0).map((row) => row.block.block_id);
  // "Select all" skips locked Blocks: there is nothing to generate or approve
  // on one until it is unlocked. A locked Block can still be ticked by hand.
  const selectAllIds = selectableIds.filter((id) => !rowsById.get(id)?.locked);
  const lockedShownCount = selectableIds.length - selectAllIds.length;
  const bulkIds = selectableIds.filter((id) => bulkSelection.has(id));
  const bulkReviewIds = bulkIds.filter((id) => rowsById.get(id)?.run?.phase === "review");
  const bulkApprovableIds = bulkReviewIds.filter((id) => approvableIds.includes(id));
  const bulkNeedsFixes = bulkReviewIds.length - bulkApprovableIds.length;
  const bulkGenerateIds = bulkIds.filter((id) => rowsById.get(id)?.generatable);
  const bulkLockedCount = bulkIds.filter((id) => rowsById.get(id)?.locked).length;
  const allSelected = selectAllIds.length > 0 && selectAllIds.every((id) => bulkSelection.has(id));
  const bulkReplacing = bulkApprovableIds.filter((id) => (rowsById.get(id)?.accepted.length ?? 0) > 0).length;
  const anyApproving = reviewRows.some((row) => row.run?.phase === "approving");
  const bulkLocked = Boolean(bulkApproving) || anyApproving || finalized || editing !== null;
  const showBulkColumn = !finalized && selectableIds.length > 0;
  const generatingRows = blockRows.filter((row) => row.run && ACTIVE_PHASES.includes(row.run.phase));
  const blocksAdded = blockRows.filter((row) => row.accepted.length > 0).length;
  const blocksToAdd = blockRows.filter((row) => !row.empty || row.accepted.length > 0).length;
  const unlockedRows = blockRows.filter((row) => row.unlocked);
  // Blocks with classes and nothing approved or in flight: what is left to do.
  const pendingRows = blockRows.filter((row) => !row.empty && row.accepted.length === 0 && !(row.run && HELD_PHASES.includes(row.run.phase)));
  const canGenerate = Boolean(schedule && readiness?.ready && !finalized && !batch && !bulkApproving && !lockBusy);
  const generationBusy = Boolean(batch) || generatingRows.length > 0 || reviewRows.length > 0;
  const finalizeBlocked = finalizing || generationBusy || editing !== null || unlockedRows.length > 0 || validation?.export_allowed !== true;
  const firstLoad = loading && !term && !needsActiveTerm && !error;

  // Units each instructor has taken in the approved schedule, and what they
  // would carry if every proposal in review were approved (a proposal
  // replaces its own Block's approved week). Re-read on every sync.
  const unitsOf = (code: string) => subjectsByCode.get(code)?.units ?? 0;
  const approvedUnits = unitsByFaculty(assignments, unitsOf);
  const reviewBlockIds = new Set(reviewRows.filter((row) => row.run?.preview).map((row) => row.block.block_id));
  const projectedUnits = unitsByFaculty([
    ...assignments.filter((item) => !reviewBlockIds.has(item.block_id)),
    ...reviewRows.flatMap((row) => row.run?.preview?.proposed_assignments ?? []),
  ], unitsOf);
  const loadRows = catalog.faculty
    .filter((member) => member.is_active || (approvedUnits.get(member.faculty_id) ?? 0) > 0 || (projectedUnits.get(member.faculty_id) ?? 0) > 0)
    .map((member) => {
      const approvedLoad = approvedUnits.get(member.faculty_id) ?? 0;
      const projected = projectedUnits.get(member.faculty_id) ?? 0;
      const maximum = maxWorkloadFor(member.is_full_time);
      const problem = !member.is_active && (approvedLoad > 0 || projected > 0)
        ? "No longer teaching: reassign their classes"
        : Math.max(approvedLoad, projected) > maximum ? "Over the unit limit" : "";
      return { member, approved: approvedLoad, projected, maximum, problem };
    })
    .sort((left, right) => Number(Boolean(right.problem)) - Number(Boolean(left.problem))
      || right.projected - left.projected || left.member.full_name.localeCompare(right.member.full_name));
  const loadProblems = loadRows.filter((row) => row.problem).length;

  // ---- Realtime -----------------------------------------------------------

  // Proposals are re-checked whenever what they were judged against changes:
  // the approved schedule (another session approved, edited or unlocked a
  // Block) or the roster and rooms (a teacher deactivated, a room retired).
  // The 5-second sync re-reads both, so a change elsewhere shows here
  // within seconds, as conflict errors or a refusal on the proposal.
  const inputsKey = [
    catalog.faculty.map((member) => `${member.faculty_id}:${member.is_active ? 1 : 0}:${member.is_full_time ? 1 : 0}`).join(","),
    catalog.rooms.map((room) => `${room.room_code}:${room.is_active === false ? 0 : 1}`).join(","),
    assignments.map((item) => `${item.record_id}:${item.faculty_id}:${item.room_code}:${item.meeting_days}:${item.time_slot_id}`).join(","),
  ].join("|");
  const lastInputsKey = useRef("");
  useEffect(() => {
    if (!schedule) return;
    if (lastInputsKey.current && lastInputsKey.current !== inputsKey) void refreshProposals(schedule.schedule_id);
    lastInputsKey.current = inputsKey;
  }, [inputsKey, schedule, refreshProposals]);

  // ---- Selection ----------------------------------------------------------

  // Press a row and drag up or down: every row from the first to the one under
  // the pointer takes the state the first one flipped to. With a mouse the
  // drag starts on a hold or as soon as the pointer reaches another row; on
  // touch it needs the hold, so an ordinary swipe still scrolls the list.
  // A plain click still toggles one.
  const LONG_PRESS_MS = 350;
  const DRAG_SLOP_PX = 8;
  const EDGE_SCROLL_PX = 32;
  type DragState = {
    mode: boolean;
    active: boolean;
    startId: string;
    timer: number;
    pointerId: number;
    pointerType: string;
    x: number;
    y: number;
    base: Set<string>;
    list: HTMLElement | null;
    /** The pointer has been on another row; coming back to the start undoes the drag. */
    left: boolean;
  };
  const dragRef = useRef<DragState | null>(null);
  const suppressClickRef = useRef(false);
  const [dragging, setDragging] = useState(false);
  // The window listeners below read the list as it is now, not as it was
  // when they were attached.
  const rowOrderRef = useRef<string[]>([]);
  const selectableRef = useRef<Set<string>>(new Set());
  const rowOrderKey = visibleRows.map((row) => row.block.block_id).join("|");
  const selectableKey = selectableIds.join("|");
  useEffect(() => {
    rowOrderRef.current = rowOrderKey ? rowOrderKey.split("|") : [];
    selectableRef.current = new Set(selectableKey ? selectableKey.split("|") : []);
  }, [rowOrderKey, selectableKey]);

  function setSelected(blockId: string, value: boolean) {
    setBulkSelection((current) => {
      if (current.has(blockId) === value) return current;
      const next = new Set(current);
      if (value) next.add(blockId);
      else next.delete(blockId);
      return next;
    });
  }

  function onRowPointerDown(blockId: string, event: ReactPointerEvent) {
    if (event.button !== 0 || bulkLocked || !selectableIds.includes(blockId)) return;
    if (dragRef.current) window.clearTimeout(dragRef.current.timer);
    const state: DragState = {
      mode: !bulkSelection.has(blockId),
      active: false,
      startId: blockId,
      timer: 0,
      pointerId: event.pointerId,
      pointerType: event.pointerType || "mouse",
      x: event.clientX,
      y: event.clientY,
      base: new Set(bulkSelection),
      list: event.currentTarget.parentElement,
      left: false,
    };
    state.timer = window.setTimeout(() => activateDrag(state), LONG_PRESS_MS);
    dragRef.current = state;
  }

  function activateDrag(state: DragState) {
    if (state.active) return;
    window.clearTimeout(state.timer);
    state.active = true;
    setBulkRejectArmed(false);
    setSelected(state.startId, state.mode);
    setDragging(true);
    window.getSelection()?.removeAllRanges();
  }

  /**
   * Every selectable row from the start row to `blockId` takes the drag's
   * state. `null` (back on the start row after leaving it, or off the side of
   * the list) restores the selection from before the press, so a scheduler
   * who changes their mind can drag back and let go.
   */
  function applyDragRange(state: DragState, blockId: string | null) {
    const next = new Set(state.base);
    if (blockId !== null) {
      const order = rowOrderRef.current;
      const from = order.indexOf(state.startId);
      const to = order.indexOf(blockId);
      if (from < 0 || to < 0) return;
      const [lo, hi] = from <= to ? [from, to] : [to, from];
      for (let index = lo; index <= hi; index += 1) {
        const id = order[index];
        if (!selectableRef.current.has(id)) continue;
        if (state.mode) next.add(id);
        else next.delete(id);
      }
    }
    setBulkSelection((current) =>
      current.size === next.size && [...next].every((id) => current.has(id)) ? current : next,
    );
  }

  useEffect(() => {
    function rowAt(x: number, y: number): string | null {
      const hit = document.elementFromPoint?.(x, y);
      const row = hit instanceof Element ? hit.closest<HTMLElement>("[data-block-row]") : null;
      return row?.dataset.blockRow ?? null;
    }
    function move(event: PointerEvent) {
      const state = dragRef.current;
      if (!state || event.pointerId !== state.pointerId) return;
      const blockId = rowAt(event.clientX, event.clientY);
      if (!state.active) {
        if (state.pointerType === "mouse") {
          // A mouse cannot scroll by dragging, so reaching another row is a drag.
          if (!blockId || blockId === state.startId) return;
          activateDrag(state);
        } else {
          // Touch or pen that moves before the hold registers is a scroll.
          if (Math.hypot(event.clientX - state.x, event.clientY - state.y) > DRAG_SLOP_PX) {
            window.clearTimeout(state.timer);
            dragRef.current = null;
          }
          return;
        }
      }
      event.preventDefault();
      const list = state.list;
      const box = list?.isConnected ? list.getBoundingClientRect() : null;
      if (list && box && box.width > 0) {
        // Off the side of the list: back to how it was before the press.
        if (event.clientX < box.left || event.clientX > box.right) {
          if (state.left) applyDragRange(state, null);
          return;
        }
        // Near the list's top or bottom edge, scroll it so hidden rows can be reached.
        if (event.clientY < box.top + EDGE_SCROLL_PX) list.scrollTop -= 16;
        else if (event.clientY > box.bottom - EDGE_SCROLL_PX) list.scrollTop += 16;
      }
      if (!blockId) return;
      if (blockId !== state.startId) {
        state.left = true;
        applyDragRange(state, blockId);
      } else if (state.left) {
        // Back on the starting row after leaving it: undo the drag.
        applyDragRange(state, null);
      }
    }
    function end(event: PointerEvent) {
      const state = dragRef.current;
      if (!state || event.pointerId !== state.pointerId) return;
      window.clearTimeout(state.timer);
      // The click that ends a drag must not toggle the last row again.
      if (state.active) suppressClickRef.current = true;
      dragRef.current = null;
      setDragging(false);
    }
    // While a hold is active, a touch must not scroll the page, select text
    // or open the long-press menu.
    function blockWhileActive(event: Event) {
      if (dragRef.current?.active) event.preventDefault();
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    window.addEventListener("touchmove", blockWhileActive, { passive: false });
    window.addEventListener("selectstart", blockWhileActive);
    window.addEventListener("dragstart", blockWhileActive);
    window.addEventListener("contextmenu", blockWhileActive);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      window.removeEventListener("touchmove", blockWhileActive);
      window.removeEventListener("selectstart", blockWhileActive);
      window.removeEventListener("dragstart", blockWhileActive);
      window.removeEventListener("contextmenu", blockWhileActive);
    };
  }, []);

  function swallowDragClick(event: ReactMouseEvent) {
    if (!suppressClickRef.current) return;
    suppressClickRef.current = false;
    event.preventDefault();
    event.stopPropagation();
  }

  /** Generates the ticked Blocks that can be generated, in the chosen generation order. */
  function generateSelected() {
    const ids = orderBlockIds(bulkGenerateIds, catalog.blocks, generationOrder);
    if (ids.length > 0) void generateBlocks(ids);
  }

  function selectBlock(blockId: string) {
    setSelectedBlockId(blockId);
    setView("proposal");
    setRejectArmed(null);
    setHighlightClassId("");
  }

  function toggleBulk(blockId: string) {
    setBulkRejectArmed(false);
    setSelected(blockId, !bulkSelection.has(blockId));
  }

  function toggleAllBulk() {
    setBulkRejectArmed(false);
    // Only the shown Blocks change; ticks on Blocks a filter hides are kept.
    setBulkSelection((current) => {
      const next = new Set(current);
      // Unticking clears every shown Block, locked ones ticked by hand too.
      if (allSelected) for (const id of selectableIds) next.delete(id);
      else for (const id of selectAllIds) next.add(id);
      return next;
    });
  }

  function stepBlock(direction: 1 | -1) {
    const shown = visibleRows.map((row) => row.block.block_id);
    const ids = shown.length > 0 ? shown : catalog.blocks.map((block) => block.block_id);
    const index = ids.indexOf(selectedBlockId);
    const next = ids[(index + direction + ids.length) % ids.length];
    if (next) selectBlock(next);
  }

  function goToReview(blockId?: string) {
    const target = blockId ?? reviewRows[0]?.block.block_id;
    if (!target) return;
    selectBlock(target);
    requestAnimationFrame(() => {
      const heading = document.getElementById("selected-block-heading");
      heading?.scrollIntoView?.({ block: "start", behavior: "smooth" });
      heading?.focus({ preventScroll: true });
    });
  }

  function goToClass(classId: string) {
    const assignment = assignments.find((item) => item.class_id === classId);
    if (!assignment) return;
    selectBlock(assignment.block_id);
    if (runsRef.current[assignment.block_id]?.phase === "review") setView("approved");
    setHighlightClassId(classId);
    requestAnimationFrame(() => document.getElementById("selected-block-heading")?.scrollIntoView?.({ block: "start", behavior: "smooth" }));
  }

  function describeClass(classId: string): string {
    const assignment = assignments.find((item) => item.class_id === classId);
    if (!assignment) return "another term's class";
    return `${assignment.subject_code} — ${blocksById.get(assignment.block_id)?.name ?? "unknown Block"}`;
  }

  function meetingOf(classId: string, blockId: string, subjectCode: string, facultyId: string, roomCode: string | null, meetingDays: string | null, slotId: string, offeringId?: string): OccupiedMeeting | null {
    const slot = slotsById.get(slotId);
    if (!slot) return null;
    return {
      classId,
      blockId,
      offeringId,
      label: `${subjectCode} ${blocksById.get(blockId)?.name ?? ""}`.trim(),
      facultyId,
      roomCode,
      days: parseMeetingDays(meetingDays),
      start: toMinutes(slot.start_time),
      end: toMinutes(meetingEndTime(slot.start_time, slot.end_time, meetingDays)),
    };
  }

  /** Grid entries for accepted assignments of one Block. */
  function approvedEntries(blockId: string, variant: "approved" | "ghost"): WeekGridEntry[] {
    return assignments.filter((item) => item.block_id === blockId).flatMap((item) => {
      const slot = slotsById.get(item.time_slot_id);
      if (!slot) return [];
      return [{
        id: `${variant}-${item.class_id}`,
        days: parseMeetingDays(item.meeting_days),
        start: slot.start_time,
        end: meetingEndTime(slot.start_time, slot.end_time, item.meeting_days),
        subjectCode: item.subject_code,
        subjectTitle: subjectsByCode.get(item.subject_code)?.subject_title,
        room: item.room_code,
        teacher: facultyById.get(item.faculty_id)?.full_name ?? item.faculty_id,
        variant,
        conflict: variant === "approved" ? conflictsByClass.get(item.class_id) ?? null : null,
        highlighted: variant === "approved" && highlightClassId === item.class_id,
      }];
    });
  }

  function proposalEntries(run: BlockRun): WeekGridEntry[] {
    if (!run.preview) return [];
    const conflicts = firstConflictByClass(proposalProblems(run.preview).conflicts);
    const diffs = new Map(run.preview.changed_values.map((item) => [item.class_id, item]));
    const replacing = run.preview.changed_values.some((item) => item.current_assignment != null);
    return run.preview.proposed_assignments.flatMap((item) => {
      const slot = slotsById.get(item.time_slot_id);
      if (!slot) return [];
      const diff = diffs.get(item.class_id);
      const edited = run.editedClassIds?.includes(item.class_id);
      return [{
        id: `proposed-${item.class_id}`,
        days: parseMeetingDays(item.meeting_days),
        start: slot.start_time,
        end: meetingEndTime(slot.start_time, slot.end_time, item.meeting_days),
        subjectCode: item.subject_code,
        subjectTitle: subjectsByCode.get(item.subject_code)?.subject_title,
        room: item.room_code,
        teacher: facultyById.get(item.faculty_id)?.full_name ?? item.faculty_id,
        variant: "proposed" as const,
        conflict: conflicts.get(item.class_id) ?? null,
        badge: edited ? "Edited" : replacing && diff && diff.status !== "unchanged" ? (diff.status === "added" ? "New" : "Changed") : null,
        highlighted: highlightClassId === item.class_id,
      }];
    });
  }

  /**
   * Other Blocks' proposals still waiting for review, by Block. Generation
   * already treats them as taken, so the editor must too: otherwise a teacher
   * or room they hold looks free here and approving both double-books it.
   */
  function pendingProposals(blockId: string): [string, NonNullable<BlockRun["preview"]>][] {
    return Object.entries(runs).flatMap(([id, other]) =>
      id !== blockId && other.preview && (other.phase === "review" || other.phase === "approving") ? [[id, other.preview]] : []);
  }

  /**
   * Everything else occupying the week, for the editor: the approved
   * schedule, other Blocks' pending proposals (each replacing its Block's
   * approved classes), and the rest of this Block's own proposal.
   */
  function occupiedFor(blockId: string, run: BlockRun | null): OccupiedMeeting[] {
    const meetings: OccupiedMeeting[] = [];
    const pending = pendingProposals(blockId);
    const replaced = new Set(pending.map(([id]) => id));
    for (const item of assignments) {
      if ((run && item.block_id === blockId) || replaced.has(item.block_id)) continue;
      const meeting = meetingOf(item.class_id, item.block_id, item.subject_code, item.faculty_id, item.room_code, item.meeting_days, item.time_slot_id, item.offering_id);
      if (meeting) meetings.push(meeting);
    }
    for (const [otherId, preview] of pending) {
      for (const item of preview.proposed_assignments) {
        const meeting = meetingOf(item.class_id, otherId, item.subject_code, item.faculty_id, item.room_code, item.meeting_days, item.time_slot_id, item.offering_id);
        if (meeting) meetings.push(meeting);
      }
    }
    for (const item of run?.preview?.proposed_assignments ?? []) {
      const meeting = meetingOf(item.class_id, blockId, item.subject_code, item.faculty_id, item.room_code, item.meeting_days, item.time_slot_id, item.offering_id);
      if (meeting) meetings.push(meeting);
    }
    return meetings;
  }

  /** The offering a class belongs to, from the approved schedule or a proposal. */
  function offeringOf(classId: string, run: BlockRun | null): string | undefined {
    return assignments.find((item) => item.class_id === classId)?.offering_id
      ?? run?.preview?.proposed_assignments.find((item) => item.class_id === classId)?.offering_id;
  }

  /** Units each instructor has taken, not counting the edited class's session. */
  function loadsFor(blockId: string, run: BlockRun | null, classId: string): Map<string, number> {
    const offeringId = offeringOf(classId, run);
    const skip = (item: { class_id: string; offering_id?: string }) => item.class_id === classId || (offeringId !== undefined && item.offering_id === offeringId);
    const pending = pendingProposals(blockId);
    const replaced = new Set(pending.map(([id]) => id));
    return unitsByFaculty([
      ...assignments.filter((item) => !skip(item) && !(run && item.block_id === blockId) && !replaced.has(item.block_id)),
      ...pending.flatMap(([, preview]) => preview.proposed_assignments).filter((item) => !skip(item)),
      ...(run?.preview?.proposed_assignments ?? []).filter((item) => !skip(item)),
    ], unitsOf);
  }

  function openProposalEditor(blockId: string, entry: WeekGridEntry) {
    const run = runsRef.current[blockId];
    const classId = entry.id.replace(/^proposed-/, "");
    const item = run?.preview?.proposed_assignments.find((candidate) => candidate.class_id === classId);
    if (!item) return;
    setEditing({
      kind: "proposal",
      blockId,
      classId,
      subjectCode: item.subject_code,
      initial: { faculty_id: item.faculty_id, room_code: item.room_code, meeting_days: item.meeting_days, time_slot_id: item.time_slot_id },
      isNew: false,
    });
  }

  function openApprovedEditor(entry: WeekGridEntry) {
    const classId = entry.id.replace(/^approved-/, "");
    const assignment = assignments.find((item) => item.class_id === classId);
    // Only an unlocked Block's week is editable.
    if (assignment && unlockedIds.has(assignment.block_id)) setEditing({ kind: "approved", assignment });
  }

  // ---- Next step ----------------------------------------------------------

  const unassigned = validation ? validation.required_class_count - validation.assigned_class_count : 0;
  let nextStep: { text: string; action?: { label: string; run: () => void } } | null = null;
  if (schedule) {
    if (finalized) {
      nextStep = { text: "Done. The master schedule is finalized; open the Committee Timetable to review and export it." };
    } else if (catalog.blocks.length === 0) {
      nextStep = { text: "This term has no Blocks yet. Generate them from the curriculum in Term Setup, then come back here." };
    } else if (readiness && !readiness.ready && reviewRows.length === 0 && !batch) {
      nextStep = { text: "Finish term setup first. The outstanding items are listed above." };
    } else if (reviewRows.length > 0) {
      nextStep = {
        text: `${reviewRows.length} ${reviewRows.length === 1 ? "proposal is" : "proposals are"} waiting for review${batch ? " while the rest generate" : ""}. Check each Block's week and edit any class now, then approve it: approving locks the Block.`,
        action: reviewRows.some((row) => row.block.block_id === selectedBlockId) ? undefined : { label: "Review next", run: () => goToReview() },
      };
    } else if (batch) {
      nextStep = { text: "The selected Blocks are generating one after another. Each proposal opens for review as soon as it is ready." };
    } else if ((validation?.hard_conflict_count ?? 0) > 0) {
      nextStep = {
        text: `Fix ${validation?.hard_conflict_count} conflict error${validation?.hard_conflict_count === 1 ? "" : "s"}: unlock the Block, change the class (or generate the Block again), then lock it.`,
        action: { label: "Go to conflict errors", run: () => document.getElementById("conflicts-heading")?.scrollIntoView?.({ block: "start", behavior: "smooth" }) },
      };
    } else if (unlockedRows.length > 0) {
      nextStep = {
        text: `${unlockedRows.map((row) => row.block.name).join(", ")} ${unlockedRows.length === 1 ? "is" : "are"} unlocked. Lock ${unlockedRows.length === 1 ? "it" : "each"} when the changes are done; finalizing waits for every Block to be locked.`,
        action: unlockedRows.some((row) => row.block.block_id === selectedBlockId) ? undefined : { label: "Show unlocked", run: () => selectBlock(unlockedRows[0].block.block_id) },
      };
    } else if (validation?.export_allowed) {
      nextStep = { text: "Every class is assigned and every Block is locked with no conflict errors. Finalize the master schedule." };
    } else if (pendingRows.length > 0) {
      nextStep = {
        text: blocksAdded === 0
          ? `Tick the Blocks to generate in the list below (filter by program and year to take them in batches, or select all), then press Generate. ${pendingRows.length} Blocks are waiting.`
          : `${pendingRows.length} ${pendingRows.length === 1 ? "Block is" : "Blocks are"} still to generate. Select ${pendingRows.length === 1 ? "it" : "them"} below and press Generate. ${blocksAdded} of ${blocksToAdd} are approved so far.`,
      };
    } else if (unassigned > 0) {
      nextStep = { text: `Every Block with classes is approved, but ${unassigned} ${unassigned === 1 ? "class is" : "classes are"} still unassigned. Unlock the affected Block and generate it again.` };
    }
  }

  const queuedRun = Object.values(runs).find((run) => run.phase === "queued" && run.queuedAt);
  const queuedSeconds = queuedRun?.queuedAt ? Math.max(0, Math.floor((now - queuedRun.queuedAt) / 1000)) : null;

  // ---- Render -------------------------------------------------------------

  const selectedRun = selectedRow?.run ?? null;
  const selectedInReview = selectedRun?.phase === "review" || selectedRun?.phase === "approving";
  const selectedHasApproved = (selectedRow?.accepted.length ?? 0) > 0;
  const effectiveView: ReviewView = selectedInReview && selectedHasApproved ? view : selectedInReview ? "proposal" : "approved";

  let gridEntries: WeekGridEntry[] = [];
  if (selectedRow) {
    if (effectiveView === "approved") gridEntries = approvedEntries(selectedRow.block.block_id, "approved");
    else if (effectiveView === "compare" && selectedRun) gridEntries = [...approvedEntries(selectedRow.block.block_id, "ghost"), ...proposalEntries(selectedRun)];
    else if (selectedRun) gridEntries = proposalEntries(selectedRun);
  }
  // A proposal is edited before approval; an approved week only once unlocked.
  const gridEditable = !bulkApproving && !finalized && (
    effectiveView === "approved"
      ? Boolean(selectedRow?.unlocked) && !lockBusy && !(selectedRun && HELD_PHASES.includes(selectedRun.phase))
      : selectedRun?.phase === "review"
  );
  const showGrid = Boolean(selectedRow && (selectedInReview || selectedHasApproved));

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            <div className="flex flex-wrap justify-between items-end gap-4">
              <div className="min-w-0">
                <h1 className="font-headline-lg text-[32px] leading-tight">Master Schedule</h1>
                <p className="text-on-surface-variant mt-1">Generate every Block in one go, review each Block's week, approve it, then finalize the term's schedule.</p>
              </div>
              <button onClick={() => sync(false)} disabled={loading} className={BTN_SECONDARY}>
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin motion-reduce:animate-none" : ""}`} aria-hidden="true" /> Refresh
              </button>
            </div>
            {error && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>}
            {firstLoad && <p className={`${CARD} p-10 text-center text-on-surface-variant`}>Loading master schedule...</p>}
            {!firstLoad && needsActiveTerm && (
              <section className="border border-tertiary/40 bg-tertiary-fixed/20 rounded-xl p-6 flex flex-wrap items-center gap-4">
                <CalendarDays className="w-7 h-7 text-tertiary shrink-0" aria-hidden="true" />
                <div className="min-w-0">
                  <h2 className="font-headline-md text-xl">No active term selected</h2>
                  <p className="text-sm text-on-surface-variant mt-1">Choose the operational term before creating a master schedule.</p>
                </div>
                <Link to="/schedule" className={`${BTN_PRIMARY} ml-auto`}>Go to Term Setup</Link>
              </section>
            )}
            {!firstLoad && term && readiness && !readiness.ready && (
              <section className={`${CARD} p-6 flex gap-3`}>
                <ListChecks className="w-6 h-6 text-primary shrink-0" aria-hidden="true" />
                <div className="min-w-0">
                  <h2 className="font-headline-md text-xl">Term setup is not complete yet</h2>
                  <p className="text-sm text-on-surface-variant mt-1">These inputs are still outstanding for Term {term.term_number}, AY {term.academic_year}. Blocks can be generated once they are in place.</p>
                  <ul className="mt-4 space-y-2">
                    {readiness.blockers.map((blocker, index) => (
                      <li key={`${blocker.code}-${blocker.message}-${index}`} className="border border-outline-variant rounded-lg px-4 py-2.5 text-sm">{blocker.message}<BlockerFixLink code={blocker.code} /></li>
                    ))}
                  </ul>
                  <Link to="/schedule" className="inline-block mt-4 text-primary font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded">Go to Term Setup</Link>
                </div>
              </section>
            )}
            {!firstLoad && term && readiness && <ReadinessWarnings warnings={readiness.warnings} />}
            {!firstLoad && term && !schedule && readiness?.ready && (
              <section className={`${CARD} p-8 text-center`}>
                <CalendarDays className="w-8 h-8 text-primary mx-auto mb-3" aria-hidden="true" />
                <h2 className="font-headline-md text-xl">No master schedule yet</h2>
                <p className="text-on-surface-variant mt-2">Create the master schedule for Term {term.term_number}, AY {term.academic_year}. Every Block is then generated into it and reviewed.</p>
                <button onClick={() => void createMasterSchedule()} className={`${BTN_PRIMARY} mt-5`}>
                  <Plus className="w-4 h-4" aria-hidden="true" /> Create master schedule
                </button>
              </section>
            )}

            {!firstLoad && schedule && (
              <section aria-labelledby="progress-heading" className={`${CARD} p-5 md:p-6`}>
                {nextStep && (
                  <div className="-mx-5 md:-mx-6 -mt-5 md:-mt-6 mb-5 px-5 md:px-6 py-3.5 rounded-t-xl bg-primary/5 border-b border-primary/20 flex flex-wrap items-center gap-x-4 gap-y-2">
                    <p className="flex items-start gap-2 min-w-0 flex-[1_1_18rem]">
                      <ArrowRight className="w-4 h-4 text-primary shrink-0 mt-1" aria-hidden="true" />
                      <span className="min-w-0 break-words"><span className="font-semibold text-primary">Next step: </span>{nextStep.text}</span>
                    </p>
                    {nextStep.action && (
                      <button type="button" onClick={nextStep.action.run} className={`${BTN_SECONDARY} md:min-h-9`}>{nextStep.action.label}</button>
                    )}
                  </div>
                )}
                <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-3">
                      <h2 id="progress-heading" className="font-headline-md text-xl">Term {term?.term_number}, AY {term?.academic_year}</h2>
                      {validation && (
                        <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                          validation.export_allowed ? "bg-secondary-container text-on-secondary-container" : "bg-surface-container-high text-on-surface-variant"
                        }`}>
                          {validation.export_allowed && <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />}
                          {!validation.export_allowed ? "Not ready yet" : finalized ? "Ready to export" : "Ready to finalize"}
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-on-surface-variant mt-1">
                      {assignments.length === 0
                        ? "Nothing approved yet."
                        : `${assignments.length} approved assignment${assignments.length === 1 ? "" : "s"}.`}
                    </p>
                  </div>
                  {!finalized && (
                    <button
                      onClick={() => { setFinalizationError(""); setFinalizationOpen(true); }}
                      disabled={finalizeBlocked}
                      className={validation?.export_allowed ? BTN_PRIMARY : BTN_SECONDARY}
                    >
                      {finalizing ? <RefreshCw className="w-4 h-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Lock className="w-4 h-4" aria-hidden="true" />}
                      {finalizing ? "Finalizing..." : "Finalize master schedule"}
                    </button>
                  )}
                </div>

                {validation && (
                  <div className="mt-5">
                    <div
                      role="progressbar"
                      aria-label="Classes assigned in the master schedule"
                      aria-valuemin={0}
                      aria-valuemax={validation.required_class_count}
                      aria-valuenow={validation.assigned_class_count}
                      className="h-2 rounded-full bg-surface-container-high overflow-hidden"
                    >
                      <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, validation.completeness_percentage)}%` }} />
                    </div>
                    <dl className="mt-3 flex flex-col sm:flex-row sm:flex-wrap gap-x-8 gap-y-1 text-sm tabular-nums">
                      <div className="flex flex-wrap gap-x-1.5">
                        <dt className="text-on-surface-variant">{finalized ? "Term assigned" : "Term assigned so far"}</dt>
                        <dd className="font-semibold">{formatCompleteness(validation.completeness_percentage)}</dd>
                        <dd>{validation.assigned_class_count} of {validation.required_class_count} {validation.required_class_count === 1 ? "class" : "classes"} assigned</dd>
                      </div>
                      <div className="flex gap-1.5">
                        <dt className="text-on-surface-variant">Conflict errors</dt>
                        <dd className={`font-semibold ${validation.hard_conflict_count > 0 ? "text-error" : ""}`}>{validation.hard_conflict_count}</dd>
                      </div>
                      <div className="flex gap-1.5">
                        <dt className="text-on-surface-variant">Blocks approved and locked</dt>
                        <dd className="font-semibold">{blocksAdded - unlockedRows.length} of {blocksToAdd}</dd>
                      </div>
                      {unlockedRows.length > 0 && (
                        <div className="flex gap-1.5">
                          <dt className="text-on-surface-variant">Unlocked for editing</dt>
                          <dd className="font-semibold text-tertiary">{unlockedRows.length}</dd>
                        </div>
                      )}
                    </dl>
                  </div>
                )}

                {finalized ? (
                  <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                    <p className="flex items-center gap-2">
                      <Lock className="w-4 h-4 text-primary shrink-0" aria-hidden="true" />
                      Finalized on {new Date(schedule.finalized_at as string).toLocaleString()}. This schedule is read-only.
                    </p>
                    {validation?.export_allowed && (
                      <Link to={`/timetable?schedule=${encodeURIComponent(schedule.schedule_id)}`} className="text-primary font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded">
                        Open Committee Timetable to export
                      </Link>
                    )}
                    {user?.role === "department_administrator" && (
                      <button type="button" onClick={() => setDeleteOpen(true)} className="text-error font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-error rounded">
                        Delete master schedule
                      </button>
                    )}
                  </div>
                ) : (
                  <p className="mt-4 text-sm text-on-surface-variant">
                    {!validation
                      ? "Checking whether this schedule can be finalized..."
                      : !validation.export_allowed
                        ? `Finalize unlocks when every class is assigned with no conflict errors. Now: ${validation.assigned_class_count} of ${validation.required_class_count} assigned, ${validation.hard_conflict_count} conflict error${validation.hard_conflict_count === 1 ? "" : "s"}.`
                        : generationBusy
                          ? "Finalize is unavailable while Blocks are generating or proposals wait for review. Approve or don't approve them first."
                          : unlockedRows.length > 0
                            ? `Finalize is unavailable while a Block is unlocked. Lock ${unlockedRows.map((row) => row.block.name).join(", ")} first.`
                            : "Every class is assigned with no conflict errors. Finalizing makes the schedule read-only."}
                  </p>
                )}
              </section>
            )}
            {!firstLoad && schedule && catalog.facultyUnavailable && (
              <p role="status" className="border border-tertiary/40 bg-tertiary-fixed/20 text-on-tertiary-fixed rounded-lg p-4 text-sm">
                Faculty information is temporarily unavailable. Faculty IDs are shown until the catalog can be refreshed.
              </p>
            )}
            <p role="status" className={notice ? "border border-outline-variant rounded-lg bg-surface-container-lowest p-4 text-sm flex items-start gap-2" : "sr-only"}>
              {notice && <CheckCircle2 className="w-4 h-4 text-primary shrink-0 mt-0.5" aria-hidden="true" />}
              {notice}
            </p>

            {!firstLoad && schedule && term && (
              <section aria-labelledby="blocks-heading" className={`${CARD} overflow-hidden grid lg:grid-cols-[minmax(0,300px)_minmax(0,1fr)]`}>
                <div className="lg:col-span-2 border-b border-outline-variant px-5 py-4 space-y-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 max-w-3xl">
                      <h2 id="blocks-heading" className="font-headline-md text-lg">Blocks</h2>
                      <p className="text-sm text-on-surface-variant mt-1">
                        {finalized
                          ? "Choose a Block to see its week."
                          : "Tick the Blocks to work on, then Generate. Filter by program and year to take them in batches, or select all. Review and edit each proposal, then approve it: approving locks the Block. Hold a checkbox and drag to tick several."}
                      </p>
                    </div>
                    <p className="text-sm text-on-surface-variant tabular-nums">{blocksAdded} of {blocksToAdd} approved</p>
                  </div>

                  {catalog.blocks.length > 0 && (programOptions.length > 1 || yearOptions.length > 1) && (
                    <div role="search" aria-label="Filter Blocks" className="flex flex-wrap items-end gap-3">
                      <label className="flex flex-col gap-1 text-sm">
                        <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Program</span>
                        <select
                          value={programFilter}
                          onChange={(event) => { setProgramFilter(event.target.value); setBulkRejectArmed(false); }}
                          className="min-h-11 min-w-40 border border-outline-variant rounded-lg px-3 bg-surface-container-lowest"
                        >
                          <option value="">All programs</option>
                          {programOptions.map((code) => <option key={code} value={code}>{code}</option>)}
                        </select>
                      </label>
                      <label className="flex flex-col gap-1 text-sm">
                        <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Year</span>
                        <select
                          value={yearFilter}
                          onChange={(event) => { setYearFilter(event.target.value); setBulkRejectArmed(false); }}
                          className="min-h-11 min-w-32 border border-outline-variant rounded-lg px-3 bg-surface-container-lowest"
                        >
                          <option value="">All years</option>
                          {yearOptions.map((year) => <option key={year} value={String(year)}>Year {year}</option>)}
                        </select>
                      </label>
                      {filterActive && (
                        <button type="button" onClick={() => { setProgramFilter(""); setYearFilter(""); setBulkRejectArmed(false); }} className={BTN_SECONDARY}>
                          Clear filters
                        </button>
                      )}
                      <p className="text-sm text-on-surface-variant tabular-nums self-center" aria-live="polite">
                        {filterActive ? `Showing ${visibleRows.length} of ${blockRows.length} Blocks` : `${blockRows.length} Blocks`}
                      </p>
                    </div>
                  )}

                  {!finalized && catalog.blocks.length > 0 && (
                    <div role="region" aria-label="Selected Blocks" className="rounded-lg bg-primary/5 border border-primary/20 px-4 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <label className="inline-flex items-center gap-2.5 min-h-11 text-sm font-medium cursor-pointer has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60">
                          <input
                            type="checkbox"
                            checked={allSelected}
                            ref={(input) => { if (input) input.indeterminate = bulkIds.length > 0 && !allSelected; }}
                            onChange={toggleAllBulk}
                            disabled={bulkLocked || selectAllIds.length === 0}
                            className="w-4 h-4 accent-primary"
                          />
                          {filterActive ? "Select all shown" : "Select all"} ({selectAllIds.length})
                        </label>
                        {lockedShownCount > 0 && (
                          <span className="text-sm text-on-surface-variant -ml-1 mr-auto">
                            {lockedShownCount} locked {lockedShownCount === 1 ? "Block" : "Blocks"} skipped
                          </span>
                        )}
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm text-on-surface-variant tabular-nums" aria-live="polite">
                            {bulkApproving
                              ? `Approving ${Math.min(bulkApproving.done + 1, bulkApproving.total)} of ${bulkApproving.total}...`
                              : bulkIds.length > 0
                                ? [
                                    `${bulkIds.length} selected`,
                                    bulkGenerateIds.length > 0 ? `${bulkGenerateIds.length} to generate` : "",
                                    bulkReviewIds.length > 0 ? `${bulkReviewIds.length} in review` : "",
                                    bulkNeedsFixes > 0 ? `${bulkNeedsFixes} ${bulkNeedsFixes === 1 ? "needs" : "need"} fixes` : "",
                                    bulkReplacing > 0 ? `${bulkReplacing} will replace an approved week` : "",
                                    bulkLockedCount > 0 ? `${bulkLockedCount} locked (unlock to generate again)` : "",
                                  ].filter(Boolean).join(" · ")
                                : "Nothing selected"}
                          </span>
                          {bulkRejectArmed && bulkReviewIds.length > 0 ? (
                            <div role="group" aria-label="Confirm don't approve selected" className="flex flex-wrap items-center gap-2">
                              <span className="text-sm">Discard {bulkReviewIds.length} {bulkReviewIds.length === 1 ? "proposal" : "proposals"}?</span>
                              <button type="button" onClick={() => setBulkRejectArmed(false)} className={BTN_SECONDARY}>Keep reviewing</button>
                              <button type="button" onClick={() => rejectSelected(bulkReviewIds)} className={`${BTN_SECONDARY} text-error border-error/40`}>
                                Discard {bulkReviewIds.length}
                              </button>
                            </div>
                          ) : (
                            <>
                              {bulkIds.length > 0 && !bulkApproving && (
                                <button type="button" onClick={() => setBulkSelection(new Set())} className={BTN_SECONDARY}>Clear</button>
                              )}
                              {bulkReviewIds.length > 0 && (
                                <>
                                  <button type="button" onClick={() => setBulkRejectArmed(true)} disabled={bulkLocked} className={BTN_SECONDARY}>
                                    <X className="w-4 h-4" aria-hidden="true" /> Don't approve ({bulkReviewIds.length})
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => void approveSelected(bulkApprovableIds)}
                                    disabled={bulkLocked || bulkApprovableIds.length === 0}
                                    className={BTN_SECONDARY}
                                  >
                                    {bulkApproving
                                      ? <RefreshCw className="w-4 h-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                                      : <ListChecks className="w-4 h-4" aria-hidden="true" />}
                                    {bulkApproving ? "Approving..." : `Approve and lock (${bulkApprovableIds.length})`}
                                  </button>
                                </>
                              )}
                              {batch ? (
                                <button type="button" onClick={stopBatch} disabled={batch.cancelling} className={BTN_SECONDARY}>
                                  <Square className="w-4 h-4" aria-hidden="true" /> {batch.cancelling ? "Stopping after this Block..." : "Stop after this Block"}
                                </button>
                              ) : (
                                <button
                                  id="generate"
                                  type="button"
                                  onClick={generateSelected}
                                  disabled={!canGenerate || bulkGenerateIds.length === 0}
                                  className={BTN_PRIMARY}
                                >
                                  <Play className="w-4 h-4" fill="currentColor" aria-hidden="true" />
                                  Generate ({bulkGenerateIds.length})
                                </button>
                              )}
                            </>
                          )}
                        </div>
                      </div>
                      {(programOptions.length > 1 || yearOptions.length > 1) && (
                        <GenerationOrderControls
                          blocks={catalog.blocks}
                          order={generationOrder}
                          onChange={setGenerationOrder}
                          disabled={Boolean(batch)}
                        />
                      )}
                      {selectableIds.length > 0 && (
                        <ul aria-label="Select Blocks" className="lg:hidden mt-2 flex flex-wrap gap-2">
                          {visibleRows.filter((row) => selectableIds.includes(row.block.block_id)).map((row) => (
                            <li key={row.block.block_id}>
                              <label className="inline-flex items-center gap-2 min-h-11 px-3 rounded-full border border-outline-variant bg-surface-container-lowest text-sm cursor-pointer has-[:checked]:border-primary has-[:checked]:bg-primary/10">
                                <input
                                  type="checkbox"
                                  checked={bulkSelection.has(row.block.block_id)}
                                  onChange={() => toggleBulk(row.block.block_id)}
                                  disabled={bulkLocked}
                                  className="w-4 h-4 accent-primary"
                                />
                                {row.block.name}
                                <span className="text-xs text-on-surface-variant">{statusFor(row).short ?? statusFor(row).label}</span>
                              </label>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}

                  {batch && (
                    <div className="rounded-lg bg-surface-container-low p-4">
                      <p className="text-sm font-medium" aria-live="polite">
                        Generating {Math.min(batch.done + 1, batch.queue.length)} of {batch.queue.length}
                        {batch.current ? `: ${blockName(batch.current)}` : ""}
                        {batch.current && runs[batch.current] ? ` · ${PHASE_LABEL[runs[batch.current].phase]}` : ""}
                      </p>
                      <div
                        role="progressbar"
                        aria-label="Blocks generated"
                        aria-valuemin={0}
                        aria-valuemax={batch.queue.length}
                        aria-valuenow={batch.done}
                        className="mt-3 h-2 rounded-full bg-outline-variant overflow-hidden"
                      >
                        <div className="h-full rounded-full bg-primary transition-[width] motion-reduce:transition-none" style={{ width: `${(batch.done / batch.queue.length) * 100}%` }} />
                      </div>
                      {queuedSeconds !== null && queuedSeconds >= WORKER_HINT_SECONDS && (
                        <p className="mt-3 flex items-start gap-2 text-sm">
                          <AlertTriangle className="w-4 h-4 text-tertiary shrink-0 mt-0.5" aria-hidden="true" />
                          <span>No worker has picked this run up yet. The job worker must be running on this machine (<code className="font-mono text-xs">python -m app.worker</code>). The run starts as soon as it is.</span>
                        </p>
                      )}
                    </div>
                  )}
                  {!finalized && !readiness?.ready && (
                    <p className="flex items-center gap-2 text-sm text-error">
                      <CircleAlert className="w-4 h-4" aria-hidden="true" /> Resolve the readiness blockers above before generating.
                    </p>
                  )}
                  {!finalized && (
                    <p className="flex items-center gap-1.5 text-xs text-on-surface-variant">
                      <ShieldCheck className="w-4 h-4 shrink-0" aria-hidden="true" />
                      {user?.role === "scheduling_committee"
                        ? "The active saved GA configuration is used. Configuration remains administrator-only. Each Block is generated around the approved schedule and the proposals before it."
                        : "Runs use the active saved GA configuration, edited under GA Configuration. Each Block is generated around the approved schedule and the proposals before it."}
                    </p>
                  )}
                </div>
                <div className="hidden lg:block relative border-r border-outline-variant">
                  <div className="absolute inset-0 flex flex-col">
                  {catalog.blocks.length === 0 ? (
                    <p className="px-5 py-5 text-sm text-on-surface-variant">
                      This term has no Blocks yet. <Link to="/schedule#generate-from-curriculum" className="text-primary font-medium hover:underline">Generate Blocks in Term Setup</Link>.
                    </p>
                  ) : (
                    <ul className={`flex-1 min-h-0 py-2 overflow-y-auto ${dragging ? "select-none cursor-ns-resize" : ""}`} aria-label="Blocks" onClickCapture={swallowDragClick}>
                      {visibleRows.length === 0 && (
                        <li className="px-5 py-3 text-sm text-on-surface-variant">No Blocks match these filters.</li>
                      )}
                      {visibleRows.map((row) => {
                        const selected = row.block.block_id === selectedBlockId;
                        const ticked = bulkSelection.has(row.block.block_id);
                        return (
                          <li
                            key={row.block.block_id}
                            data-block-row={row.block.block_id}
                            onPointerDown={(event) => onRowPointerDown(row.block.block_id, event)}
                            className={`flex items-stretch border-l-[3px] ${
                              selected ? "border-primary bg-primary/5" : ticked ? "border-transparent bg-primary/[0.03] hover:bg-surface-container-low" : "border-transparent hover:bg-surface-container-low"
                            }`}
                          >
                            {showBulkColumn && (
                              selectableIds.includes(row.block.block_id) ? (
                                <label className="shrink-0 flex items-center justify-center w-11 pl-2 cursor-pointer has-[:disabled]:cursor-not-allowed">
                                  <input
                                    type="checkbox"
                                    aria-label={`Select ${row.block.name}`}
                                    checked={ticked}
                                    onChange={() => toggleBulk(row.block.block_id)}
                                    disabled={bulkLocked}
                                    className="w-4 h-4 accent-primary"
                                  />
                                </label>
                              ) : (
                                <span className="shrink-0 w-11" aria-hidden="true" />
                              )
                            )}
                            <button
                              type="button"
                              onClick={() => { if (!selected) selectBlock(row.block.block_id); }}
                              aria-pressed={selected}
                              className={`flex-1 min-w-0 min-h-11 flex items-center justify-between gap-3 ${showBulkColumn ? "pl-2" : "pl-5"} pr-5 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary`}
                            >
                              <span className="min-w-0 font-medium truncate">{row.block.name}</span>
                              <BlockStatus {...statusFor(row)} compact />
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  </div>
                </div>

                <div className="p-5 md:p-7 min-w-0 lg:min-h-[32rem]">
                  {catalog.blocks.length > 0 && (
                    <label className="lg:hidden flex flex-col gap-1 mb-5 text-sm">
                      <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Block</span>
                      <select
                        value={selectedBlockId}
                        onChange={(event) => selectBlock(event.target.value)}
                        className="min-h-11 border border-outline-variant rounded-lg px-3 bg-surface-container-lowest"
                      >
                        {blockRows.filter((row) => matchesFilter(row.block) || row.block.block_id === selectedBlockId).map((row) => (
                          <option key={row.block.block_id} value={row.block.block_id}>{row.block.name} — {statusFor(row).label}</option>
                        ))}
                      </select>
                    </label>
                  )}
                  {selectedRow ? (
                    renderBlockPanel()
                  ) : (
                    <p className="text-on-surface-variant">{catalog.blocks.length === 0 ? "This term has no Blocks yet." : "Choose a Block."}</p>
                  )}
                </div>
              </section>
            )}

            {!firstLoad && schedule && assignments.length > 0 && validation && validation.conflicts.length > 0 && (
              <section aria-labelledby="conflicts-heading" className={`${CARD} p-5 md:p-6`}>
                <div className="flex items-center gap-3">
                  <AlertTriangle className="w-5 h-5 text-error shrink-0" aria-hidden="true" />
                  <h2 id="conflicts-heading" tabIndex={-1} className="font-headline-md text-lg">Conflict errors in the approved schedule</h2>
                </div>
                <p className="text-sm text-on-surface-variant mt-1">Checked again every few seconds, so a roster change (such as a deactivated instructor) shows here as it happens. Unlock the Block to fix a class.</p>
                <ul className="mt-4 space-y-3">
                  {validation.conflicts.map((conflict) => (
                    <li key={conflictKey(conflict)} className="border border-error/30 rounded-lg p-4">
                      <p className="font-medium">{conflictLabel(conflict.code)}</p>
                      <p className="text-sm mt-1">{conflict.message}</p>
                      <div className="flex flex-wrap gap-2 mt-3">
                        {conflict.class_ids.map((classId) => (
                          <button key={classId} type="button" onClick={() => goToClass(classId)} className={BTN_SECONDARY}>
                            Go to {describeClass(classId)}
                          </button>
                        ))}
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {!firstLoad && schedule && term && loadRows.length > 0 && (
              <section aria-labelledby="loads-heading" className={`${CARD} p-5 md:p-6`}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <Users className="w-5 h-5 text-primary shrink-0" aria-hidden="true" />
                    <div className="min-w-0">
                      <h2 id="loads-heading" className="font-headline-md text-lg">Units taken by instructor</h2>
                      <p className="text-sm text-on-surface-variant">
                        Updates as Blocks are approved, edited or unlocked, here or in another session.
                        {loadProblems > 0 ? ` ${loadProblems} ${loadProblems === 1 ? "instructor needs" : "instructors need"} attention.` : ""}
                      </p>
                    </div>
                  </div>
                  <button type="button" onClick={() => setLoadsOpen((open) => !open)} aria-expanded={loadsOpen} aria-controls="loads-table" className={BTN_SECONDARY}>
                    {loadsOpen ? "Hide" : `Show ${loadRows.length} instructors`}
                  </button>
                </div>
                {loadsOpen && (
                  <div id="loads-table" className="mt-4 overflow-x-auto">
                    <table className="w-full text-sm tabular-nums">
                      <thead>
                        <tr className="text-left text-on-surface-variant border-b border-outline-variant">
                          <th scope="col" className="py-2 pr-4 font-medium">Instructor</th>
                          <th scope="col" className="py-2 pr-4 font-medium">Approved</th>
                          <th scope="col" className="py-2 pr-4 font-medium">With proposals in review</th>
                          <th scope="col" className="py-2 pr-4 font-medium">Limit</th>
                          <th scope="col" className="py-2 font-medium">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {loadRows.map((row) => (
                          <tr key={row.member.faculty_id} className="border-b border-outline-variant/60 last:border-0">
                            <th scope="row" className="py-2 pr-4 font-normal text-left">
                              {row.member.full_name}
                              <span className="block text-xs text-on-surface-variant">{row.member.is_full_time ? "Full-Time" : "Part-Time"}</span>
                            </th>
                            <td className="py-2 pr-4">{row.approved}</td>
                            <td className="py-2 pr-4">{row.projected === row.approved ? "—" : row.projected}</td>
                            <td className="py-2 pr-4">{row.maximum}</td>
                            <td className={`py-2 ${row.problem ? "text-error font-medium" : "text-on-surface-variant"}`}>
                              {row.problem || `${Math.max(0, row.maximum - Math.max(row.approved, row.projected))} units free`}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            )}
          </div>
        </main>
      </div>
      {schedule && term && finalizationOpen && !finalized && (
        <Modal title="Finalize master schedule?" subtitle={`Term ${term.term_number}, AY ${term.academic_year}`} onClose={() => { if (!finalizing) setFinalizationOpen(false); }}>
          <div className="p-6 space-y-4">
            <p>Finalization cannot be undone. If the server's independent validation passes, this master schedule becomes read-only.</p>
            <p className="text-sm break-all">Master schedule: <code>{schedule.schedule_id}</code></p>
            <p className="text-sm text-on-surface-variant">Finalization does not capture history, run mining, or activate rules.</p>
            {finalizationError && <p role="alert" className="text-sm text-error">{finalizationError}</p>}
          </div>
          <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright">
            <button type="button" onClick={() => setFinalizationOpen(false)} disabled={finalizing} className={BTN_SECONDARY}>Cancel</button>
            <button type="button" onClick={() => void finalizeMasterSchedule()} disabled={finalizing} className={BTN_PRIMARY}>{finalizing ? "Finalizing..." : "Confirm finalization"}</button>
          </div>
        </Modal>
      )}
      {schedule && term && deleteOpen && finalized && (
        <ConfirmDeleteDialog
          title="Delete finalized master schedule?"
          subtitle={`Term ${term.term_number}, AY ${term.academic_year}`}
          actionLabel="Delete master schedule"
          pendingLabel="Deleting..."
          fallbackError="Could not delete the master schedule."
          onConfirm={async () => {
            await api.deleteSchedule(schedule.schedule_id);
            // A sync already in flight must not put the deleted schedule back.
            scheduleMutationRef.current += 1;
            setSchedule(null);
            setValidation(null);
            setNotice("The finalized master schedule was deleted. Per-Block generation starts again from empty.");
            sync(false);
          }}
          onClose={() => setDeleteOpen(false)}
        >
          <p>This removes the term's finalized master schedule and every Block assignment on it. Files already exported from it are not affected. It cannot be undone.</p>
        </ConfirmDeleteDialog>
      )}
      {editing?.kind === "proposal" && (
        <ClassEditor
          title={editing.isNew ? "Place class in proposal" : "Edit proposed class"}
          intro="Only options that fit the approved schedule, the rest of this proposal and each instructor's units are listed. The server checks the change again; if it causes a conflict error it is not applied, and the reason is shown here."
          submitLabel="Apply to proposal"
          classId={editing.classId}
          subjectCode={editing.subjectCode}
          blockId={editing.blockId}
          offeringId={offeringOf(editing.classId, runs[editing.blockId] ?? null)}
          initial={editing.initial}
          catalog={catalog}
          loads={loadsFor(editing.blockId, runs[editing.blockId] ?? null, editing.classId)}
          occupied={occupiedFor(editing.blockId, runs[editing.blockId] ?? null)}
          onSubmit={(value) => saveProposalEdit(editing.blockId, editing.classId, value)}
          onClose={() => setEditing(null)}
          pairNote={proposalPairNote(editing.blockId, editing.classId)}
          termNumber={term?.term_number}
        />
      )}
      {editing?.kind === "approved" && schedule && !finalized && (
        <ClassEditor
          title="Edit class in unlocked Block"
          intro="Saving changes the master schedule right away. A combined lecture moves every Block attending it, and a paired lab moves its partner's time too, so those Blocks must be unlocked as well. The server re-validates the whole schedule and reports any conflict error that remains."
          submitLabel="Save assignment"
          classId={editing.assignment.class_id}
          subjectCode={editing.assignment.subject_code}
          blockId={editing.assignment.block_id}
          offeringId={editing.assignment.offering_id}
          initial={{
            faculty_id: editing.assignment.faculty_id,
            room_code: editing.assignment.room_code ?? "",
            meeting_days: editing.assignment.meeting_days ?? "",
            time_slot_id: editing.assignment.time_slot_id,
          }}
          catalog={catalog}
          loads={loadsFor(editing.assignment.block_id, null, editing.assignment.class_id)}
          occupied={occupiedFor(editing.assignment.block_id, null)}
          onSubmit={(value) => saveApprovedEdit(editing.assignment, value)}
          onClose={() => setEditing(null)}
          pairNote={approvedPairNote(editing.assignment)}
          termNumber={term?.term_number}
        />
      )}
    </div>
  );

  // The selected Block's panel: progress, failure, review or approved week.
  function renderBlockPanel() {
    if (!selectedRow) return null;
    const { block, run, accepted, empty, locked, unlocked, partner, conflictCount } = selectedRow;
    const blockId = block.block_id;
    const preview = run?.preview;
    const problems = preview ? proposalProblems(preview) : null;
    const valid = runIsValid(run);
    const replacing = accepted.length > 0;
    const position = batch ? batch.queue.indexOf(blockId) - batch.done : -1;
    const canGenerateThis = canGenerate && selectedRow.generatable;
    const busyHere = lockBusy === blockId;

    // The pair: what holds this Block's shared lectures and lab times.
    const partnerRow = partner ? rowsById.get(partner.block_id) : undefined;
    const partnerInReview = partnerRow?.run?.phase === "review" || partnerRow?.run?.phase === "approving";
    const partnerActive = Boolean(partnerRow?.run && ACTIVE_PHASES.includes(partnerRow.run.phase));
    const pairBlockers = partnerRow
      ? [locked ? block.name : "", partnerRow.locked ? partnerRow.block.name : ""].filter(Boolean)
      : [];
    const pairClears = partnerRow ? [selectedRow, partnerRow].filter((item) => item.accepted.length > 0).map((item) => item.block.name) : [];
    const canRegeneratePair = Boolean(partnerRow) && canGenerate && !empty && !partnerActive
      && !(run && ACTIVE_PHASES.includes(run.phase)) && run?.phase !== "approving" && partnerRow?.run?.phase !== "approving"
      && pairBlockers.length === 0;
    // Why generating this Block alone would keep its shared sessions where they are.
    const pairHold = !partnerRow
      ? ""
      : partnerInReview
        ? `${partnerRow.block.name}'s proposal is still in review, so generating ${block.name} alone fits its lectures and labs around that proposal.`
        : partnerRow.accepted.length > 0
          ? `${partnerRow.block.name} is approved${partnerRow.locked ? " and locked" : ""}, so generating ${block.name} alone keeps the lecture sessions and lab times ${partnerRow.block.name} already has.`
          : "";
    const regeneratePairControl = partnerRow && !finalized && (
      pairArmed === blockId ? (
        <div role="group" aria-label="Confirm regenerate pair" className="flex flex-wrap items-center gap-2">
          <span className="text-sm">
            {pairClears.length > 0
              ? `This clears the approved week of ${pairClears.join(" and ")} and generates both again. Nothing replaces it until you approve the new proposals.`
              : `Generate ${block.name} and ${partnerRow.block.name} again together?`}
          </span>
          <button type="button" onClick={() => setPairArmed(null)} className={BTN_SECONDARY}>Cancel</button>
          <button type="button" onClick={() => void regeneratePair(blockId)} className={BTN_PRIMARY}>
            <RotateCcw className="w-4 h-4" aria-hidden="true" /> Regenerate pair
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setPairArmed(blockId)}
          disabled={!canRegeneratePair}
          title={pairBlockers.length > 0 ? `Unlock ${pairBlockers.join(" and ")} first.` : undefined}
          className={BTN_SECONDARY}
        >
          <RotateCcw className="w-4 h-4" aria-hidden="true" /> Regenerate pair ({block.section_letter}+{partnerRow.block.section_letter})
        </button>
      )
    );

    return (
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <h3 id="selected-block-heading" tabIndex={-1} className="font-headline-md text-2xl scroll-mt-4 min-w-0 break-words focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded">{block.name}</h3>
          <BlockStatus {...statusFor(selectedRow)} />
          {catalog.blocks.length > 1 && (
            <div className="ml-auto flex gap-1">
              <button type="button" onClick={() => stepBlock(-1)} aria-label="Previous Block" className={`${BTN_SECONDARY} px-2.5`}><ChevronLeft className="w-4 h-4" aria-hidden="true" /></button>
              <button type="button" onClick={() => stepBlock(1)} aria-label="Next Block" className={`${BTN_SECONDARY} px-2.5`}><ChevronRight className="w-4 h-4" aria-hidden="true" /></button>
            </div>
          )}
        </div>
        <p className="text-sm text-on-surface-variant mt-1">
          {block.program_code} year {block.year_level} · seats {block.capacity}
          {partnerRow ? ` · paired with ${partnerRow.block.name} (shared lectures and lab times)` : ""}
        </p>

        {accepted.length > 0 && !finalized && (
          <div className={`mt-5 rounded-lg border p-4 flex flex-wrap items-center gap-3 ${unlocked ? "border-tertiary/50 bg-tertiary-fixed/20" : "border-outline-variant bg-surface-container-low"}`}>
            {unlocked
              ? <LockOpen className="w-5 h-5 text-tertiary shrink-0" aria-hidden="true" />
              : <Lock className="w-5 h-5 text-primary shrink-0" aria-hidden="true" />}
            <p className="text-sm min-w-0 flex-[1_1_16rem]">
              {unlocked
                ? <><span className="font-semibold">Unlocked for editing.</span> Each change is checked by the server and saved to the master schedule right away. Lock the Block when you are done{conflictCount > 0 ? `; first fix its ${conflictCount} conflict error${conflictCount === 1 ? "" : "s"}` : ""}.</>
                : <><span className="font-semibold">Approved and locked.</span> The week is read-only. {conflictCount > 0 ? `It has ${conflictCount} conflict error${conflictCount === 1 ? "" : "s"} now: unlock it to fix ${conflictCount === 1 ? "it" : "them"}.` : "Unlock it to edit a class or generate it again."}</>}
            </p>
            {unlocked ? (
              <button
                type="button"
                onClick={() => void changeLock(blockId, "lock")}
                disabled={Boolean(lockBusy) || conflictCount > 0 || Boolean(bulkApproving) || editing !== null}
                className={BTN_PRIMARY}
              >
                {busyHere ? <RefreshCw className="w-4 h-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Lock className="w-4 h-4" aria-hidden="true" />}
                {busyHere ? "Locking..." : "Lock"}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void changeLock(blockId, "unlock")}
                disabled={Boolean(lockBusy) || Boolean(bulkApproving) || Boolean(run && HELD_PHASES.includes(run.phase))}
                className={BTN_SECONDARY}
              >
                {busyHere ? <RefreshCw className="w-4 h-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <LockOpen className="w-4 h-4" aria-hidden="true" />}
                {busyHere ? "Unlocking..." : "Unlock"}
              </button>
            )}
          </div>
        )}
        {lockError?.blockId === blockId && (
          <p role="alert" className="mt-3 border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-3 text-sm">{lockError.message}</p>
        )}

        {run && ACTIVE_PHASES.includes(run.phase) && (
          <div role="status" className="mt-5 rounded-lg bg-surface-container-low p-4 flex items-center gap-3">
            <Clock3 className="w-5 h-5 text-tertiary shrink-0" aria-hidden="true" />
            <p className="text-sm">
              {run.phase === "waiting"
                ? `Waiting its turn${position > 0 ? ` (${position} ahead)` : ""}. Its proposal opens here when it is ready.`
                : run.phase === "loading"
                  ? "Generation finished. Loading the proposal..."
                  : `${PHASE_LABEL[run.phase]}. Its proposal opens here when it is ready.`}
            </p>
          </div>
        )}

        {run?.phase === "failed" && (
          <div role="alert" className="mt-5 border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">
            <p className="font-semibold">Generation did not produce a proposal. The Block was not changed.</p>
            <p className="text-sm mt-1">{run.error}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {run.previewFailed && run.jobId && schedule ? (
                <button
                  type="button"
                  onClick={() => {
                    updateRun(blockId, { phase: "loading", error: undefined });
                    void loadProposal(blockId, run.jobId as string, schedule.schedule_id, sessionRef.current);
                  }}
                  className={BTN_SECONDARY}
                >
                  <RotateCcw className="w-4 h-4" aria-hidden="true" /> Load proposal again
                </button>
              ) : (
                <button type="button" onClick={() => void generateBlocks([blockId])} disabled={!canGenerate} className={BTN_SECONDARY}>
                  <RotateCcw className="w-4 h-4" aria-hidden="true" /> Try again
                </button>
              )}
            </div>
          </div>
        )}

        {run?.phase === "rejected" && (
          <div className="mt-5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-on-surface-variant">This Block's proposal was not approved and was discarded. Nothing in the master schedule changed.</p>
              {!finalized && !showGrid && (
                <button type="button" onClick={() => void generateBlocks([blockId])} disabled={!canGenerateThis} className={BTN_SECONDARY}>
                  <RotateCcw className="w-4 h-4" aria-hidden="true" /> Generate again
                </button>
              )}
            </div>
            {pairHold && !finalized && (
              <div className="rounded-lg border border-tertiary/40 bg-tertiary-fixed/20 p-3 text-sm flex flex-wrap items-center gap-3">
                <AlertTriangle className="w-4 h-4 text-tertiary shrink-0" aria-hidden="true" />
                <p className="min-w-0 flex-[1_1_18rem]">
                  {pairHold} To move the shared lectures and labs, regenerate the pair together
                  {pairBlockers.length > 0 ? ` (unlock ${pairBlockers.join(" and ")} first)` : ""}.
                </p>
                {regeneratePairControl}
              </div>
            )}
          </div>
        )}

        {preview && problems && (run?.phase === "review" || run?.phase === "approving") && (
          <div className="mt-5 space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <h4 id="review-heading" className="font-semibold">Review the proposed week</h4>
                <p className="text-sm text-on-surface-variant mt-0.5 tabular-nums">
                  {preview.proposed_assignments.length} {preview.proposed_assignments.length === 1 ? "class" : "classes"}
                  {run?.metrics ? ` · history attainment ${Math.round(run.metrics.historical_pattern_attainment * 100)}%` : ""} ·{" "}
                  <span className={valid ? "text-primary font-semibold" : "text-error font-semibold"}>
                    {valid ? "no conflict errors" : `${problems.conflicts.length} conflict error${problems.conflicts.length === 1 ? "" : "s"}, ${problems.unplaced.length} unplaced`}
                  </span>
                  {" "}· term {formatCompleteness(preview.validation.completeness_percentage)} if approved
                </p>
                <p className="text-sm text-on-surface-variant mt-0.5">Edit now: select a class to change its instructor, room, days or time. Approving locks the Block.</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {rejectArmed === blockId ? (
                  <div role="group" aria-label="Confirm don't approve" className="flex flex-wrap items-center gap-2">
                    {partnerInReview && partnerRow ? (
                      <>
                        <span className="text-sm max-w-md">
                          {partnerRow.block.name}'s proposal shares its lectures and lab times with this one. Discard only {block.name} and it is generated again around {partnerRow.block.name}'s proposal; discard both to generate the pair afresh.
                        </span>
                        <button type="button" onClick={() => setRejectArmed(null)} className={BTN_SECONDARY}>Keep reviewing</button>
                        <button type="button" onClick={() => reject(blockId)} className={`${BTN_SECONDARY} text-error border-error/40`}>Discard only {block.name}</button>
                        <button type="button" onClick={() => reject(blockId, true)} disabled={partnerRow.run?.phase === "approving"} className={`${BTN_SECONDARY} text-error border-error/40`}>Discard both</button>
                      </>
                    ) : (
                      <>
                        <span className="text-sm">Discard this proposal?</span>
                        <button type="button" onClick={() => setRejectArmed(null)} className={BTN_SECONDARY}>Keep reviewing</button>
                        <button type="button" onClick={() => reject(blockId)} className={`${BTN_SECONDARY} text-error border-error/40`}>Discard it</button>
                      </>
                    )}
                  </div>
                ) : (
                  <>
                    <button type="button" onClick={() => setRejectArmed(blockId)} disabled={run?.phase === "approving" || Boolean(bulkApproving)} className={BTN_SECONDARY}>
                      <X className="w-4 h-4" aria-hidden="true" /> Don't approve
                    </button>
                    <button type="button" onClick={() => void approve(blockId)} disabled={!valid || run?.phase === "approving" || finalized || Boolean(bulkApproving)} className={BTN_PRIMARY}>
                      {run?.phase === "approving" ? <RefreshCw className="w-4 h-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <CheckCircle2 className="w-4 h-4" aria-hidden="true" />}
                      {run?.phase === "approving" ? "Approving..." : replacing ? "Approve, replace and lock" : "Approve and lock"}
                    </button>
                  </>
                )}
              </div>
            </div>
            {run?.error && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-3 text-sm">{run.error}</p>}
            {run?.refreshError && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-3 text-sm">{run.refreshError}</p>}
            {pairHold && partnerRow && !partnerInReview && (
              <p className="text-sm text-on-surface-variant">{pairHold}</p>
            )}
            {!valid && (
              <div className="text-sm">
                <p className="flex items-start gap-2 text-error font-medium">
                  <CircleAlert className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" /> Approve unlocks when every class is placed with no conflict error. Change the marked classes, or generate the Block again.
                </p>
                <ul className="mt-2 ml-6 list-disc space-y-1 text-on-surface-variant">
                  {problems.unplaced.map((item) => (
                    <li key={item.class_id}>
                      {item.subject_code} could not be placed.{" "}
                      <button
                        type="button"
                        onClick={() => setEditing({
                          kind: "proposal",
                          blockId,
                          classId: item.class_id,
                          subjectCode: item.subject_code,
                          initial: { faculty_id: "", room_code: "", meeting_days: "", time_slot_id: "" },
                          isNew: true,
                        })}
                        className="text-primary font-medium hover:underline"
                      >
                        Place it by hand
                      </button>
                    </li>
                  ))}
                  {problems.conflicts.map((conflict) => (
                    <li key={conflictKey(conflict)}>{conflictLabel(conflict.code)}: {conflict.message}</li>
                  ))}
                </ul>
              </div>
            )}
            {replacing && (
              <div role="radiogroup" aria-label="Week view" className="inline-flex rounded-lg border border-outline-variant p-0.5 bg-surface-container-low">
                {([
                  ["proposal", "Proposal"],
                  ["compare", "Compare with approved"],
                  ["approved", "Currently approved"],
                ] as const).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={effectiveView === value}
                    onClick={() => setView(value)}
                    className={`min-h-9 px-3 rounded-md text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                      effectiveView === value ? "bg-surface-container-lowest shadow-sm font-semibold" : "text-on-surface-variant hover:text-on-surface"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {!run && !empty && accepted.length === 0 && (
          <div className="mt-5 rounded-lg border border-dashed border-outline-variant p-6 text-center">
            <p className="text-on-surface-variant">Not generated yet. Tick it with others in the list and press Generate, or generate just this Block.</p>
            {pairHold && <p className="text-sm text-on-surface-variant mt-2">{pairHold}</p>}
            <button type="button" onClick={() => void generateBlocks([blockId])} disabled={!canGenerateThis} className={`${BTN_SECONDARY} mt-4`}>
              <Play className="w-4 h-4" aria-hidden="true" /> Generate {block.name}
            </button>
          </div>
        )}
        {empty && accepted.length === 0 && !run && (
          <p className="mt-5 text-sm text-on-surface-variant">This Block has no classes, so there is nothing to generate. Add its classes in Term Setup.</p>
        )}

        {showGrid && (
          <div className="mt-5 space-y-3">
            {!selectedInReview && (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-on-surface-variant">
                  Approved week{gridEditable ? ". Select a class to change it." : locked && !finalized ? " (locked)." : "."}
                </p>
                {!finalized && unlocked && (!run || run.phase === "rejected" || run.phase === "failed") && (
                  <div className="flex flex-wrap gap-2">
                    {regeneratePairControl}
                    <button type="button" onClick={() => void generateBlocks([blockId])} disabled={!canGenerateThis} className={BTN_SECONDARY}>
                      <RotateCcw className="w-4 h-4" aria-hidden="true" /> Generate again
                    </button>
                  </div>
                )}
              </div>
            )}
            <GridLegend view={effectiveView} />
            <WeekScheduleGrid
              label={`${effectiveView === "approved" ? "Approved" : effectiveView === "compare" ? "Proposed and approved" : "Proposed"} week for ${block.name}`}
              entries={gridEntries}
              dayStart={gridBounds.start}
              dayEnd={gridBounds.end}
              onSelect={gridEditable ? (entry) => (effectiveView === "approved" ? openApprovedEditor(entry) : openProposalEditor(blockId, entry)) : undefined}
              selectVerb="Edit"
              emptyMessage={effectiveView === "approved" ? "Nothing is approved for this Block yet." : "The proposal places no classes."}
            />
          </div>
        )}
      </div>
    );
  }
}

const PHASE_LABEL: Record<RunPhase, string> = {
  waiting: "Waiting",
  submitting: "Submitting",
  queued: "Queued",
  running: "Generating",
  loading: "Loading proposal",
  review: "Ready to review",
  approving: "Approving",
  failed: "Failed",
  rejected: "Not approved",
};

type BlockTone = "muted" | "progress" | "review" | "error" | "done";

const TONE_STYLE: Record<BlockTone, string> = {
  muted: "text-on-surface-variant",
  progress: "px-2.5 py-0.5 bg-tertiary-fixed text-on-tertiary-fixed",
  review: "px-2.5 py-0.5 bg-primary/10 text-primary",
  error: "px-2.5 py-0.5 bg-error-container text-on-error-container",
  done: "px-2.5 py-0.5 bg-secondary-container text-on-secondary-container",
};

// The Block list is narrow, so long phases get a short pill there; the full
// wording stays in the detail header and in the pill's tooltip.
const SHORT_PHASE_LABEL: Partial<Record<RunPhase, string>> = {
  loading: "Loading",
  review: "Ready",
};

function statusFor(row: { run?: BlockRun; accepted: ScheduleAssignment[]; conflictCount: number; empty: boolean; unlocked?: boolean }): { label: string; short?: string; tone: BlockTone } {
  const { run, accepted, conflictCount, empty, unlocked } = row;
  if (run && run.phase !== "rejected") {
    if (run.phase === "review" && run.preview && !runIsValid(run)) return { label: "Review · needs fixes", short: "Needs fixes", tone: "error" };
    const tone: BlockTone = run.phase === "failed" ? "error" : run.phase === "review" || run.phase === "approving" ? "review" : "progress";
    return { label: PHASE_LABEL[run.phase], short: SHORT_PHASE_LABEL[run.phase], tone };
  }
  if (accepted.length > 0) {
    const errors = `${conflictCount} conflict error${conflictCount === 1 ? "" : "s"}`;
    if (unlocked) {
      return conflictCount > 0
        ? { label: `Unlocked · ${errors}`, short: "Unlocked · errors", tone: "error" }
        : { label: "Unlocked · editing", short: "Unlocked", tone: "progress" };
    }
    return conflictCount > 0
      ? { label: `Locked · ${errors}`, short: errors, tone: "error" }
      : { label: `Approved · locked · ${accepted.length} ${accepted.length === 1 ? "class" : "classes"}`, short: "Locked", tone: "done" };
  }
  if (run?.phase === "rejected") return { label: "Not approved", tone: "muted" };
  return empty ? { label: "No classes", tone: "muted" } : { label: "Not generated", tone: "muted" };
}

function BlockStatus({ label, short, tone, compact = false }: { label: string; short?: string; tone: BlockTone; compact?: boolean }) {
  const Icon = tone === "done" ? CheckCircle2 : tone === "error" ? AlertTriangle : tone === "progress" ? Clock3 : tone === "review" ? CircleAlert : null;
  const shown = compact && short ? short : label;
  return (
    <span
      title={shown === label ? undefined : label}
      className={`shrink-0 inline-flex items-center gap-1 rounded-full text-xs font-semibold tabular-nums whitespace-nowrap ${TONE_STYLE[tone]}`}
    >
      {Icon && <Icon className="w-3.5 h-3.5" aria-hidden="true" />}
      {shown}
    </span>
  );
}

function GridLegend({ view }: { view: ReviewView }) {
  const item = (swatch: string, label: string) => (
    <li className="flex items-center gap-1.5"><span className={`inline-block w-3.5 h-3.5 rounded border ${swatch}`} aria-hidden="true" />{label}</li>
  );
  return (
    <ul aria-label="Legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-on-surface-variant">
      {view !== "approved" && item("bg-primary/10 border-primary/50", "Proposed")}
      {view === "approved" && item("bg-secondary-container/60 border-secondary/50", "Approved")}
      {view === "compare" && item("bg-surface-container-low border-dashed border-outline", "Currently approved (to be replaced)")}
      {item("border-error ring-1 ring-error/60", "Conflict error")}
    </ul>
  );
}
