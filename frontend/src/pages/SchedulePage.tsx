import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { Archive, CheckCircle2, CircleAlert, Pencil, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import {
  ApiError,
  api,
  describeDeleteError,
  type BlockSection,
  type BlockClass,
  type CohortGenerationReport,
  type SubjectOffering,
  type Faculty,
  type CourseProgram,
  type Readiness,
  type Subject,
  type Term,
  type TimeSlot,
} from "../api/client";
import {
  CatalogRow,
  CatalogTable,
  EditButton,
  PlainHeader,
  RowActions,
  SortHeader,
  naturalCompare,
  sortRows,
  useSort,
} from "../components/catalog/CatalogTable";
import AddClassesDialog from "../components/modals/AddClassesDialog";
import AddTermDialog from "../components/modals/AddTermDialog";
import { InactiveBadge, ShowInactiveToggle, isActive } from "../components/catalog/ActiveStatus";
import ConfirmDeleteDialog from "../components/modals/ConfirmDeleteDialog";
import EditDialog, { type EditField, type EditValues } from "../components/modals/EditDialog";
import SearchSelect, { type SearchSelectOption } from "../components/forms/SearchSelect";
import { matchesSubjectSearch } from "../utils/subjectSearch";
import Sidebar from "../components/layout/Sidebar";
import BlockerFixLink from "../components/readiness/BlockerFixLink";
import ReadinessWarnings from "../components/readiness/ReadinessWarnings";
import TopBar from "../components/layout/TopBar";
import { usePeriodicSync } from "../hooks/usePeriodicSync";
import { publishTermChanged } from "../state/appEvents";
import { MAX_SECTIONS, SETUP_CUTOFF_ACADEMIC_YEAR, isBeforeSetupCutoff, sectionLetters } from "../config/terms";

interface EditTarget {
  title: string;
  fields: EditField[];
  save: (values: EditValues) => Promise<unknown>;
  termChanged?: boolean;
}

interface SetupData {
  terms: Term[];
  activeTerm: Term | null;
  subjects: Subject[];
  programs: CourseProgram[];
  faculty: Faculty[];
  blocks: BlockSection[];
  blockClasses: BlockClass[];
  offerings: SubjectOffering[];
  slots: TimeSlot[];
  readiness: Readiness | null;
}

// Anchors that live inside the Advanced setup section.
const ADVANCED_ANCHORS = ["course-programs", "time-slots", "block-subjects"];

function Panel({ id, title, description, children }: { id?: string; title: string; description: string; children: ReactNode }) {
  return (
    <section id={id} tabIndex={id ? -1 : undefined} className="focus:outline-none bg-surface-container-lowest border border-outline-variant rounded-xl shadow-sm overflow-hidden">
      <div className="p-5 border-b border-outline-variant bg-surface-container-low/40">
        <h2 className="font-headline-md text-xl">{title}</h2>
        <p className="text-sm text-on-surface-variant mt-1">{description}</p>
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

function Field({ label, className = "", children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <label className={`block space-y-1.5 ${className}`}>
      <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">{label}</span>
      {children}
    </label>
  );
}

const inputClass = "w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary";
// 44px below md for touch, 24px from md up so dense lists stay dense.
const touchTarget = "inline-flex items-center justify-center min-h-11 min-w-11 md:min-h-6 md:min-w-6 px-2";
// Past this many Blocks the list is long enough to offer a jump past it.
const LONG_BLOCK_LIST = 8;
const buttonClass = "flex items-center justify-center gap-2 min-h-11 md:min-h-6 px-4 py-2.5 rounded-lg bg-surface-tint text-white font-bold disabled:opacity-50";

function RecordActions({ onEdit, onDelete }: { onEdit?: () => void; onDelete: () => void }) {
  return (
    <span className="flex gap-3 md:shrink-0">
      {onEdit && <button onClick={onEdit} className={`${touchTarget} text-xs text-primary hover:underline`}>Edit</button>}
      <button onClick={onDelete} className={`${touchTarget} text-xs text-error hover:underline`}>Delete</button>
    </span>
  );
}

// A Subject is searchable by both halves of how an operator knows it, so the
// title travels with the code into the picker.
function subjectOptions(subjects: Subject[]): SearchSelectOption[] {
  return subjects.map((subject) => ({
    value: subject.subject_code,
    label: subject.subject_code,
    description: subject.subject_title,
  }));
}

// Subject and offering pickers also match title initials, so "DSA" finds
// "Data Structures and Algorithms".
function subjectFilter(option: SearchSelectOption, query: string): boolean {
  return matchesSubjectSearch(option.label, option.description ?? "", query);
}

function blockOptions(blocks: BlockSection[]): SearchSelectOption[] {
  return blocks.map((block) => ({ value: block.block_id, label: block.name }));
}

function offeringOptions(offerings: SubjectOffering[], subjects: Subject[]): SearchSelectOption[] {
  return offerings.map((offering) => ({
    value: offering.offering_id,
    label: offering.group_label,
    description: subjects.find((subject) => subject.subject_code === offering.subject_code)?.subject_title,
  }));
}

// A Block spans one program year at a time; anything outside this range is a
// data-entry error rather than a rare curriculum, so the form offers no way in.
const YEAR_LEVELS = [1, 2, 3, 4, 5];

interface DeleteTarget {
  label: string;
  consequence: string;
  action: () => Promise<unknown>;
  termChanged: boolean;
}

interface ArchiveTarget {
  term: Term;
}

// The server spells a Block's name from its cohort tuple. The form derives the
// same string so the operator reads the identity they are about to create
// instead of a name the API would ignore.
function derivedBlockName(programCode: string, yearLevel: string, sectionLetter: string) {
  if (!programCode || !sectionLetter) return "";
  return `${programCode}-${yearLevel}${sectionLetter}`.toUpperCase();
}

// FR-MGT-003 requires a refused delete to name what blocks it, so the dependent
// records the server returns are spelled out rather than left in the envelope.
function describeError(caught: unknown, fallback: string): string {
  if (caught instanceof ApiError && caught.code === "NO_APPLICABLE_CURRICULUM") {
    const { program_code: program, entry_year: entryYear } = caught.details ?? {};
    return `No curriculum on file for ${program} covers students who entered in ${entryYear}.`;
  }
  return describeDeleteError(caught, fallback);
}

function termLabel(term: Term) {
  return `Term ${term.term_number}, AY ${term.academic_year}`;
}

// Newest academic year first, so the term being set up is the first choice.
function academicYears(terms: Term[]): string[] {
  return [...new Set(terms.map((term) => term.academic_year))].sort((a, b) => b.localeCompare(a));
}

function CohortReport({ report }: { report: CohortGenerationReport }) {
  return (
    <div className="mt-4 rounded-lg border border-outline-variant p-4 text-sm break-words" role="status">
      <p className="font-bold">
        {report.curriculum_label}, year {report.year_level}, term {report.term_number}:{" "}
        {report.blocks_created} Blocks, {report.offerings_created} offerings, {report.classes_created} classes created.
      </p>
      <ul className="mt-3 space-y-2">
        {report.blocks.map((block) => (
          <li key={block.block_id}>
            <strong>{block.name}</strong>{" "}
            <span className="text-on-surface-variant">{block.created ? "created" : "already existed"}</span>
            {block.note ? (
              <p className="text-on-surface-variant">{block.note}</p>
            ) : (
              <p className="text-on-surface-variant">
                {block.classes.map((item) => `${item.group_label}${item.created ? " (new)" : ""}`).join(", ")}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// Units are owned by the Subject catalog, so an offering form reads them off
// the selected record instead of collecting a value that could disagree with it.
function DerivedUnits({ subject }: { subject: Subject | null }) {
  return (
    <div className="block space-y-1.5">
      <span id="offering-units-label" className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Units</span>
      <p
        aria-labelledby="offering-units-label"
        aria-live="polite"
        className="px-3 py-2.5 border border-dashed border-outline-variant rounded-lg bg-surface-container-highest/40 text-on-surface-variant"
      >
        {subject
          ? <>{subject.units} units <span className="text-xs">(from catalog)</span></>
          : "Select a subject"}
      </p>
    </div>
  );
}

function DerivedBlockName({ name }: { name: string }) {
  return (
    <div className="block space-y-1.5">
      <span id="block-name-label" className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Block name</span>
      <p
        aria-labelledby="block-name-label"
        aria-live="polite"
        className="min-w-0 break-words px-3 py-2.5 border border-dashed border-outline-variant rounded-lg bg-surface-container-highest/40 text-on-surface-variant"
      >
        {name ? <>{name} <span className="text-xs">(derived)</span></> : "Choose a Course Program and section letter"}
      </p>
    </div>
  );
}

/**
 * The Block this one is paired with: A-B, C-D, E-F, ... of the same program
 * and year. Mirrors `partner_section_letter` on the server, which merges the
 * pair's lectures and makes their labs meet at the same time.
 */
function pairedBlock(block: BlockSection, blocks: BlockSection[]): BlockSection | undefined {
  if (!/^[A-Z]$/.test(block.section_letter)) return undefined;
  const letter = String.fromCharCode(65 + ((block.section_letter.charCodeAt(0) - 65) ^ 1));
  return blocks.find((other) =>
    other.program_code === block.program_code
    && other.year_level === block.year_level
    && other.section_letter === letter);
}

type BlockSortKey = "name" | "program" | "year" | "section" | "seats";

const BLOCK_SORT: Record<BlockSortKey, (a: BlockSection, b: BlockSection) => number> = {
  name: (a, b) => naturalCompare(a.name, b.name),
  program: (a, b) => naturalCompare(a.program_code, b.program_code),
  year: (a, b) => a.year_level - b.year_level,
  section: (a, b) => naturalCompare(a.section_letter, b.section_letter),
  seats: (a, b) => a.capacity - b.capacity,
};

// Program, then year, then section: the order a cohort list is read in.
const byCohort = (a: BlockSection, b: BlockSection) =>
  naturalCompare(a.program_code, b.program_code)
  || a.year_level - b.year_level
  || naturalCompare(a.section_letter, b.section_letter);

const filterSelectClass = "border border-outline-variant rounded-lg px-3 py-2 bg-surface-container-lowest text-sm outline-none focus:border-primary";
const rowButtonClass = "flex items-center gap-1.5 px-2.5 py-1.5 rounded text-xs";

/**
 * The term's Blocks, one table row each like the catalog tables. The derived
 * name leads; the cohort tuple it is built from sits beside it so the
 * derivation is never a mystery. A Block holds no Subject: the offerings it
 * takes are listed read-only and linked through their own workflow.
 */
function BlockTable({ blocks, blockClasses, onAddClasses, onEdit, onDelete }: {
  blocks: BlockSection[];
  blockClasses: BlockClass[];
  onAddClasses: (block: BlockSection) => void;
  onEdit: (block: BlockSection) => void;
  onDelete: (block: BlockSection) => void;
}) {
  const [query, setQuery] = useState("");
  const [program, setProgram] = useState("");
  const [year, setYear] = useState("");
  const [sort, toggleSort] = useSort<BlockSortKey>("program");

  const subjectsOf = (block: BlockSection) =>
    blockClasses.filter((item) => item.block_id === block.block_id).map((item) => item.subject_code);
  const programs = [...new Set(blocks.map((block) => block.program_code))].sort(naturalCompare);
  const years = [...new Set(blocks.map((block) => block.year_level))].sort((a, b) => a - b);
  const needle = query.trim().toLowerCase();
  const filtered = blocks.filter((block) => (
    (!program || block.program_code === program)
    && (!year || block.year_level === Number(year))
    && (!needle
      || block.name.toLowerCase().includes(needle)
      || block.program_code.toLowerCase().includes(needle)
      || subjectsOf(block).some((code) => code.toLowerCase().includes(needle)))
  ));
  const sorted = sortRows(filtered, sort, BLOCK_SORT, byCohort);
  const filtering = Boolean(needle || program || year);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[14rem] max-w-md">
          <Search aria-hidden className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-outline" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search Blocks or subject codes"
            aria-label="Search Block sections"
            className="w-full pl-9 pr-3 py-2 text-sm bg-surface-container-lowest border border-outline-variant rounded-lg outline-none focus:border-primary"
          />
        </div>
        <select value={program} onChange={(event) => setProgram(event.target.value)} aria-label="Filter by Course Program" className={filterSelectClass}>
          <option value="">All Course Programs</option>
          {programs.map((code) => <option key={code} value={code}>{code}</option>)}
        </select>
        <select value={year} onChange={(event) => setYear(event.target.value)} aria-label="Filter by year level" className={filterSelectClass}>
          <option value="">All years</option>
          {years.map((level) => <option key={level} value={level}>Year {level}</option>)}
        </select>
        {filtering && (
          <button type="button" onClick={() => { setQuery(""); setProgram(""); setYear(""); }} className={`${touchTarget} text-sm text-primary hover:underline`}>
            Clear filters
          </button>
        )}
        <span aria-live="polite" className="ml-auto text-sm text-on-surface-variant">
          {filtering ? `${sorted.length} of ${blocks.length} Blocks` : `${blocks.length} Blocks`}
        </span>
      </div>
      {sorted.length === 0 ? (
        <p className="border border-outline-variant rounded-xl p-8 text-center text-sm text-on-surface-variant">No Block Sections match your search or filters.</p>
      ) : (
        <CatalogTable
          caption={`Block sections, ${sorted.length} shown. Column headings sort the list.`}
          headings={(
            <>
              <SortHeader label="Block" sortKey="name" sort={sort} onSort={toggleSort} />
              <SortHeader label="Program" sortKey="program" sort={sort} onSort={toggleSort} className="hidden sm:table-cell" />
              <SortHeader label="Year" sortKey="year" sort={sort} onSort={toggleSort} className="hidden sm:table-cell" />
              <SortHeader label="Section" sortKey="section" sort={sort} onSort={toggleSort} className="hidden sm:table-cell" />
              <SortHeader label="Seats" sortKey="seats" sort={sort} onSort={toggleSort} className="hidden md:table-cell" />
              <PlainHeader className="hidden lg:table-cell">Subject offerings</PlainHeader>
              <PlainHeader className="hidden xl:table-cell">Paired with</PlainHeader>
            </>
          )}
        >
          {sorted.map((block) => {
            const codes = subjectsOf(block);
            // Long lists collapse to a count; the full list is the tooltip.
            const codesLabel = codes.length > 4 ? `${codes.slice(0, 3).join(", ")} +${codes.length - 3} more` : codes.join(", ");
            const partner = pairedBlock(block, blocks)?.name ?? null;
            return (
              <CatalogRow key={block.block_id} active>
                <th scope="row" className="px-4 py-2 min-w-[8rem] text-left font-normal">
                  <span className="font-medium text-on-surface break-words">{block.name}</span>{" "}
                  {/* Columns hidden on narrow screens fold under the name. */}
                  <span className="sm:hidden block text-xs text-on-surface-variant">
                    {block.program_code} · Year {block.year_level} · Section {block.section_letter} · {block.capacity} seats
                  </span>
                </th>
                <td className="hidden sm:table-cell px-4 py-2 break-words">{block.program_code}</td>
                <td className="hidden sm:table-cell px-4 py-2">{block.year_level}</td>
                <td className="hidden sm:table-cell px-4 py-2">{block.section_letter}</td>
                <td className="hidden md:table-cell px-4 py-2 whitespace-nowrap text-on-surface-variant">{block.capacity} seats</td>
                <td className="hidden lg:table-cell px-4 py-2 text-on-surface-variant break-words" title={codes.join(", ") || undefined}>
                  {codesLabel || <span className="italic">None linked yet</span>}
                </td>
                <td className="hidden xl:table-cell px-4 py-2 text-on-surface-variant" title={partner ? "Shared lectures, labs at the same time" : undefined}>
                  {partner ?? "—"}
                </td>
                <RowActions>
                  <button type="button" onClick={() => onAddClasses(block)} title="Add classes" aria-label={`Add classes to ${block.name}`} className={`${rowButtonClass} border border-outline-variant hover:border-primary`}>
                    <Plus aria-hidden className="w-4 h-4" /><span className="hidden sm:inline">Add classes</span>
                  </button>
                  <EditButton label={block.name} onClick={() => onEdit(block)} />
                  <button type="button" onClick={() => onDelete(block)} title="Delete" aria-label={`Delete ${block.name}`} className={`${rowButtonClass} text-error hover:bg-error-container/30`}>
                    <Trash2 aria-hidden className="w-4 h-4" /><span className="hidden lg:inline">Delete</span>
                  </button>
                </RowActions>
              </CatalogRow>
            );
          })}
        </CatalogTable>
      )}
    </div>
  );
}

export default function SchedulePage() {
  const [data, setData] = useState<SetupData | null>(null);
  const [deleting, setDeleting] = useState<DeleteTarget | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // Sync-originated, kept apart from `error` (saves) so a background read that succeeds clears only this one.
  const [loadError, setLoadError] = useState("");
  // The latest sync failed, foreground or background; `data` is then the last good read, so the ready banner must not offer a next step.
  const [syncFailed, setSyncFailed] = useState(false);
  const [editing, setEditing] = useState<EditTarget | null>(null);

  // The term picked in the Academic terms dropdowns; empty falls back to the
  // active term, then the newest term that can be set up.
  const [pickedYear, setPickedYear] = useState("");
  const [pickedTermId, setPickedTermId] = useState("");
  const [addingTerm, setAddingTerm] = useState(false);
  const [archiving, setArchiving] = useState<ArchiveTarget | null>(null);
  const [programCode, setProgramCode] = useState("");
  const [programName, setProgramName] = useState("");
  const [generatingSlots, setGeneratingSlots] = useState(false);
  const [slotError, setSlotError] = useState("");
  const [blockProgram, setBlockProgram] = useState("");
  const [blockYear, setBlockYear] = useState("1");
  const [blockLetter, setBlockLetter] = useState("");
  const [offeringSubject, setOfferingSubject] = useState("");
  const [cohortProgram, setCohortProgram] = useState("");
  const [cohortYear, setCohortYear] = useState("1");
  const [cohortSections, setCohortSections] = useState("1");
  const [showInactivePrograms, setShowInactivePrograms] = useState(false);
  const [programError, setProgramError] = useState("");
  const [cohortReport, setCohortReport] = useState<CohortGenerationReport | null>(null);
  const [classBlock, setClassBlock] = useState("");
  const [classOffering, setClassOffering] = useState("");
  // The user's own choice; until they make one, the section follows whether a
  // prerequisite inside it is missing.
  const [advancedChoice, setAdvancedChoice] = useState<boolean | null>(null);
  const [addingClassesTo, setAddingClassesTo] = useState<BlockSection | null>(null);

  const { sync } = usePeriodicSync(
    async ({ isBackground, isStale }) => {
      if (!isBackground) {
        setLoading(true);
        setError("");
        setLoadError("");
      }
      try {
        const [terms, subjects, programs, faculty, activeTerm] = await Promise.all([
          // Archived terms come too, so they can be restored and a duplicate
          // of one is caught before the request.
          api.listTerms({ includeArchived: true }),
          api.listSubjects(),
          // Inactive programs are listed so they can be activated again; the
          // Block pickers only offer active ones.
          api.listCoursePrograms(true),
          api.listFaculty(),
          api.getActiveTerm().catch((caught: unknown) => {
            if (caught instanceof ApiError && caught.code === "NO_ACTIVE_TERM") return null;
            throw caught;
          }),
        ]);
        if (isStale()) return;
        // A term from before the cutoff is never set up, so its inputs are not read.
        const scoped = activeTerm && !isBeforeSetupCutoff(activeTerm.academic_year)
          ? await Promise.all([
            api.listBlocks(activeTerm.term_id),
            api.listTimeSlots(activeTerm.term_id),
            api.getReadiness(activeTerm.term_id),
            api.listOfferings(activeTerm.term_id),
          ])
          : null;
        if (isStale()) return;
        // A Block's subjects hang off the Block, so they are fetched once the
        // Blocks themselves are known.
        const blockClasses = scoped
          ? (await Promise.all(scoped[0].items.map((block) => api.listBlockClasses(block.block_id))))
            .flatMap((response) => response.items)
          : [];
        if (isStale()) return;
        setData({
          terms: terms.items,
          activeTerm,
          subjects: subjects.items,
          programs: programs.items,
          faculty: faculty.items,
          blocks: scoped?.[0].items ?? [],
          slots: scoped?.[1].items ?? [],
          readiness: scoped?.[2] ?? null,
          offerings: scoped?.[3].items ?? [],
          blockClasses,
        });
        setSyncFailed(false);
        setLoadError("");
      } catch (caught: unknown) {
        if (isStale()) return;
        setSyncFailed(true);
        if (!isBackground) {
          setLoadError(caught instanceof ApiError ? caught.message : "Could not load current-term setup.");
        }
      } finally {
        if (!isStale() && !isBackground) {
          setLoading(false);
        }
      }
    },
    {
      intervalMs: 5000,
      enabled: !editing && !generatingSlots && !addingClassesTo && !addingTerm && !archiving,
      listenTermChanged: true,
    },
  );

  // Every cohort submission takes a sequence number. A newer submission or a
  // term switch retires the older ones, so a response that lands late, report
  // or error, is dropped rather than shown against the wrong request.
  const activeTermId = data?.activeTerm?.term_id ?? null;
  const cohortRequest = useRef(0);
  useEffect(() => {
    cohortRequest.current += 1;
    setCohortReport(null);
    // A collapse chosen for one term must not hide what another term is missing.
    setAdvancedChoice(null);
    setSlotError("");
  }, [activeTermId]);

  const advancedOpen = advancedChoice ?? (data?.slots.length === 0 || data?.programs.length === 0);

  // A fix link from another page lands on /schedule#anchor. The target may sit
  // inside the closed Advanced setup, so open it first and scroll once it shows.
  const { hash, key: locationKey } = useLocation();
  const scrolledTo = useRef("");
  useEffect(() => {
    const id = hash.slice(1);
    if (!id || !data || scrolledTo.current === locationKey) return;
    // Without an active term the panels are not rendered yet; keep the
    // navigation pending until its target exists. A closed section still holds
    // its panels in the DOM.
    const target = document.getElementById(id);
    if (!target) return;
    // Pin the section open even when it is already auto-open, or the next
    // refresh would collapse the panel the user just came to use.
    if (ADVANCED_ANCHORS.includes(id) && advancedChoice !== true) {
      setAdvancedChoice(true);
      return;
    }
    scrolledTo.current = locationKey;
    // A navigation that resolves late must not pull the user out of a field
    // or dialog they are using.
    if (document.activeElement?.closest("input, select, textarea, [contenteditable], [role=dialog]")) return;
    target.scrollIntoView?.();
    // A router Link, unlike a native anchor, leaves keyboard focus behind.
    target.focus({ preventScroll: true });
  }, [hash, locationKey, data, advancedChoice]);

  async function execute(
    action: () => Promise<unknown>,
    { termChanged = false, isCurrent = () => true }: { termChanged?: boolean; isCurrent?: () => boolean } = {},
  ) {
    setError("");
    try {
      await action();
      // A superseded request still changed the data, so refresh it, but in
      // the background: a foreground sync clears the error the newer request
      // may already be showing.
      if (!isCurrent()) {
        sync(true);
        return;
      }
      if (termChanged) {
        publishTermChanged();
      } else {
        sync(false);
      }
    } catch (caught: unknown) {
      if (!isCurrent()) return;
      setError(describeError(caught, "The request to the Chronos server failed."));
    }
  }

  function submitProgram(event: FormEvent) {
    event.preventDefault();
    void execute(() => api.createCourseProgram({ program_code: programCode.trim(), program_name: programName.trim() }));
  }

  async function generateSlots() {
    if (!data?.activeTerm) return;
    setGeneratingSlots(true);
    setSlotError("");
    try {
      await api.generateCanonicalSlots(data.activeTerm.term_id);
      sync(false);
    } catch (caught: unknown) {
      setSlotError(describeError(caught, "Could not generate canonical time slots."));
      // The failure is reported inside the section, which may have been collapsed.
      setAdvancedChoice(true);
    } finally {
      setGeneratingSlots(false);
    }
  }

  function submitBlock(event: FormEvent) {
    event.preventDefault();
    if (!data?.activeTerm || !blockProgram || !blockLetter) return;
    void execute(async () => {
      await api.createBlock({
        term_id: data.activeTerm!.term_id,
        program_code: blockProgram,
        year_level: Number(blockYear),
        section_letter: blockLetter.toUpperCase(),
        is_expanded: false,
      });
      setBlockLetter("");
    });
  }

  function submitCohort(event: FormEvent) {
    event.preventDefault();
    // A count of sections becomes letters A, B, C...; the API takes the letters.
    const count = Number(cohortSections);
    const letters = sectionLetters(count);
    if (!data?.activeTerm || !cohortProgram || count > MAX_SECTIONS || letters.length === 0) return;
    const request = ++cohortRequest.current;
    const isCurrent = () => cohortRequest.current === request;
    setCohortReport(null);
    void execute(async () => {
      const report = await api.generateCohort(data.activeTerm!.term_id, {
        program_code: cohortProgram,
        year_level: Number(cohortYear),
        section_letters: letters,
      });
      if (isCurrent()) setCohortReport(report);
    }, { isCurrent });
  }

  function submitOffering(event: FormEvent) {
    event.preventDefault();
    if (!data?.activeTerm || !offeringSubject) return;
    void execute(async () => {
      await api.createOffering(data.activeTerm!.term_id, offeringSubject);
      setOfferingSubject("");
    });
  }

  function submitBlockClass(event: FormEvent) {
    event.preventDefault();
    void execute(() => api.addBlockClass(classBlock, classOffering));
  }

  function editTerm(term: Term) {
    setEditing({
      title: `Edit Term ${term.term_number}, AY ${term.academic_year}`,
      termChanged: true,
      fields: [
        { name: "academic_year", label: "Academic year", kind: "text", value: term.academic_year, maxLength: 9 },
        { name: "term_number", label: "Term number", kind: "number", value: term.term_number, min: 1 },
      ],
      save: (values) => api.updateTerm(term.term_id, {
        academic_year: String(values.academic_year),
        term_number: Number(values.term_number),
      }),
    });
  }

  function editCourseProgram(program: CourseProgram) {
    setEditing({
      title: `Edit ${program.program_code}`,
      fields: [
        { name: "program_name", label: "Course Program name", kind: "text", value: program.program_name, maxLength: 150 },
      ],
      save: (values) => api.updateCourseProgram(program.program_code, { program_name: String(values.program_name) }),
    });
  }


  // The cohort tuple is what the overlap constraint keys on, so program, year
  // and letter are fixed once created — the title carries them so the dialog
  // still names the Block being edited. Capacity is derived, leaving expansion
  // as the only thing an edit can change: twenty seats, or twenty-five.
  function editBlock(block: BlockSection) {
    setEditing({
      title: `Edit ${block.name} (${block.program_code}, Year ${block.year_level}, Section ${block.section_letter})`,
      fields: [
        { name: "is_expanded", label: "Expanded (25 seats)", kind: "checkbox", value: block.is_expanded },
      ],
      save: (values) => api.updateBlock(block.block_id, Boolean(values.is_expanded)),
    });
  }

  // The dialog reports its own failure, so a refused edit stays on screen with
  // the entered values rather than emptying into the page-level banner.
  async function saveEdit(target: EditTarget, values: EditValues) {
    await target.save(values);
    if (target.termChanged) {
      publishTermChanged();
    } else {
      sync(false);
    }
  }

  function confirmDelete(label: string, consequence: string, action: () => Promise<unknown>, options?: { termChanged?: boolean }) {
    setDeleting({ label, consequence, action, termChanged: options?.termChanged ?? false });
  }

  // Archiving only hides the term; nothing it references is removed.
  async function runArchive(target: ArchiveTarget) {
    await api.archiveTerm(target.term.term_id);
    sync(false);
  }

  async function activateProgram(programCode: string) {
    setProgramError("");
    try {
      await api.updateCourseProgram(programCode, { is_active: true });
      sync(false);
    } catch (caught: unknown) {
      setProgramError(caught instanceof ApiError ? caught.message : "Could not activate the Course Program.");
    }
  }

  const activePrograms = data?.programs.filter(isActive) ?? [];
  const subjectUnits = new Map(data?.subjects.map((subject) => [subject.subject_code, subject.units]));
  const offeringUnits = data?.offerings.reduce((total, offering) => total + (subjectUnits.get(offering.subject_code) ?? 0), 0) ?? 0;
  const inactivePrograms = data?.programs.filter((program) => !isActive(program)) ?? [];
  const setupTerms = data?.terms.filter((term) => !term.is_captured && !term.is_archived) ?? [];
  const archivedTerms = data?.terms.filter((term) => term.is_archived && !term.is_captured) ?? [];
  const capturedTerms = data?.terms.filter((term) => term.is_captured) ?? [];
  const activeSetupTerm = setupTerms.find((term) => term.is_active) ?? null;
  const termYears = academicYears(setupTerms);
  const shownYear = termYears.includes(pickedYear)
    ? pickedYear
    : activeSetupTerm?.academic_year ?? termYears.find((year) => !isBeforeSetupCutoff(year)) ?? termYears[0] ?? "";
  const termsInYear = setupTerms
    .filter((term) => term.academic_year === shownYear)
    .sort((a, b) => a.term_number - b.term_number);
  const pickedTerm = termsInYear.find((term) => term.term_id === pickedTermId)
    ?? termsInYear.find((term) => term.is_active)
    ?? termsInYear[0]
    ?? null;
  const pickedIsOlder = pickedTerm ? isBeforeSetupCutoff(pickedTerm.academic_year) : false;
  const activeIsOlder = data?.activeTerm ? isBeforeSetupCutoff(data.activeTerm.academic_year) : false;
  const sectionCount = Number(cohortSections);
  const cohortLetters = sectionLetters(sectionCount);
  const sectionCountValid = Number.isInteger(sectionCount) && sectionCount >= 1 && sectionCount <= MAX_SECTIONS;

  // The dialog reports a refusal itself, so only a success reaches the refresh.
  async function runDelete(target: DeleteTarget) {
    await target.action();
    if (target.termChanged) {
      publishTermChanged();
    } else {
      sync(false);
    }
  }

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            <div className="flex flex-wrap justify-between items-end gap-4">
              <div>
                <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">Generation inputs</p>
                <h1 className="font-headline-lg text-[32px] mt-1">Term Setup</h1>
                <p className="text-on-surface-variant max-w-2xl">Manage the inputs the scheduler uses for the active term. Generate and review the master schedule from the Master Schedule page.</p>
                {/* Subjects are term-independent, so the catalog lives on its
                    own page and this one only consumes it. */}
                <p className="text-on-surface-variant mt-1">
                  Subjects are managed in the <Link to="/subjects" className="text-primary underline">Subjects catalog</Link>.
                </p>
              </div>
              <button onClick={() => sync(false)} disabled={loading} className="flex items-center gap-2 min-h-11 md:min-h-6 px-4 py-2 border border-outline-variant rounded-lg bg-surface-container-lowest">
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Refresh
              </button>
            </div>
            {error && <p role="alert" className="break-words border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>}
            {loadError && <p role="alert" className="break-words border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{loadError}</p>}
            {loading && !data && <p className="border border-outline-variant rounded-xl bg-surface-container-lowest p-10 text-center text-on-surface-variant">Loading setup data...</p>}
            {data && (
              <>
                <Panel title="Academic terms" description="Pick an academic year and term, then set it as the active operational term.">
                  <div className="flex flex-wrap items-end gap-3">
                    <Field label="Academic year" className="w-full sm:w-60">
                      <select
                        value={shownYear}
                        onChange={(event) => { setPickedYear(event.target.value); setPickedTermId(""); }}
                        disabled={termYears.length === 0}
                        className={inputClass}
                      >
                        {termYears.length === 0 && <option value="">No terms yet</option>}
                        {termYears.map((year) => (
                          <option key={year} value={year} disabled={isBeforeSetupCutoff(year)}>
                            AY {year}{isBeforeSetupCutoff(year) ? " (older, read-only)" : ""}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Term" className="w-full sm:w-44">
                      <select
                        value={pickedTerm?.term_id ?? ""}
                        onChange={(event) => setPickedTermId(event.target.value)}
                        disabled={termsInYear.length === 0}
                        className={inputClass}
                      >
                        {termsInYear.length === 0 && <option value="">No terms</option>}
                        {termsInYear.map((term) => (
                          <option key={term.term_id} value={term.term_id}>
                            Term {term.term_number}{term.is_active ? " (active)" : ""}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <button
                      type="button"
                      onClick={() => { if (pickedTerm) void execute(() => api.selectTerm(pickedTerm.term_id), { termChanged: true }); }}
                      disabled={!pickedTerm || pickedTerm.is_active || pickedIsOlder}
                      className={buttonClass}
                    >
                      <CheckCircle2 className="w-4 h-4" /> {pickedTerm?.is_active ? "Active" : "Set active"}
                    </button>
                    <span className="flex gap-1">
                      <button
                        type="button"
                        onClick={() => { if (pickedTerm) editTerm(pickedTerm); }}
                        disabled={!pickedTerm || pickedIsOlder}
                        aria-label={pickedTerm ? `Edit ${termLabel(pickedTerm)}` : "Edit term"}
                        className={`${touchTarget} gap-1 text-sm text-primary hover:underline disabled:opacity-50 disabled:no-underline`}
                      >
                        <Pencil className="w-4 h-4" /> Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => { if (pickedTerm) setArchiving({ term: pickedTerm }); }}
                        disabled={!pickedTerm || pickedTerm.is_active}
                        title={pickedTerm?.is_active ? "Set another term active before archiving this one." : undefined}
                        aria-label={pickedTerm ? `Archive ${termLabel(pickedTerm)}` : "Archive term"}
                        className={`${touchTarget} gap-1 text-sm text-on-surface-variant hover:underline disabled:opacity-50 disabled:no-underline`}
                      >
                        <Archive className="w-4 h-4" /> Archive
                      </button>
                    </span>
                    <button
                      type="button"
                      onClick={() => setAddingTerm(true)}
                      className="sm:ml-auto flex items-center justify-center gap-2 min-h-11 md:min-h-6 px-4 py-2.5 rounded-lg border border-primary text-primary font-bold"
                    >
                      <Plus className="w-4 h-4" /> Add term
                    </button>
                  </div>
                  <p className="mt-3 text-sm text-on-surface-variant" aria-live="polite">
                    {data.activeTerm
                      ? <>Active term: <strong className="text-on-surface">{termLabel(data.activeTerm)}</strong></>
                      : "No term is active yet."}
                    {pickedIsOlder && ` Terms before AY ${SETUP_CUTOFF_ACADEMIC_YEAR} are read-only.`}
                  </p>
                  {archivedTerms.length > 0 && (
                    <details className="mt-4">
                      <summary className="cursor-pointer text-sm font-bold py-3 md:py-0.5">
                        Archived terms ({archivedTerms.length})
                      </summary>
                      <p className="text-sm text-on-surface-variant mt-2">Archived terms are hidden from the lists above. Nothing they reference was removed.</p>
                      <ul className="mt-2 grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
                        {archivedTerms.map((term) => (
                          <li key={term.term_id} className="border border-outline-variant rounded-lg px-3 py-2 text-sm flex items-center justify-between gap-2">
                            <span>{termLabel(term)}</span>
                            <button
                              type="button"
                              onClick={() => void execute(() => api.restoreTerm(term.term_id))}
                              aria-label={`Restore ${termLabel(term)}`}
                              className={`${touchTarget} text-xs text-primary hover:underline`}
                            >
                              Restore
                            </button>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {capturedTerms.length > 0 && (
                    <details className="mt-4">
                      <summary className="cursor-pointer text-sm font-bold py-3 md:py-0.5">
                        Historical terms ({capturedTerms.length})
                      </summary>
                      <p className="text-sm text-on-surface-variant mt-2">Captured terms are read-only history and cannot be selected or edited.</p>
                      <ul className="mt-2 grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
                        {capturedTerms.map((term) => (
                          <li key={term.term_id} className="border border-outline-variant rounded-lg px-3 py-2 text-sm">
                            {termLabel(term)}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </Panel>

                {!data.activeTerm ? (
                  <div className="border border-tertiary/40 bg-tertiary-fixed/20 rounded-xl p-6 flex gap-3">
                    <CircleAlert className="w-6 h-6 text-tertiary" />
                    <p>Select an active term before adding term-scoped scheduling inputs.</p>
                  </div>
                ) : activeIsOlder ? (
                  // Terms before the cutoff were scheduled before Chronos and are
                  // done, so their setup is not shown or edited here.
                  <div role="status" className="border border-tertiary/40 bg-tertiary-fixed/20 rounded-xl p-6 flex gap-3">
                    <CircleAlert className="w-6 h-6 text-tertiary shrink-0" />
                    <div>
                      <p className="font-bold">Unable to display older terms before AY {SETUP_CUTOFF_ACADEMIC_YEAR}</p>
                      <p className="text-sm text-on-surface-variant mt-1">
                        {termLabel(data.activeTerm)} is the active term. Set a term from AY {SETUP_CUTOFF_ACADEMIC_YEAR} or later active, or add one.
                      </p>
                    </div>
                  </div>
                ) : (
                  <>
                    <section className={`border rounded-xl p-5 ${data.readiness?.ready ? "border-secondary/40 bg-secondary-container/10" : "border-tertiary/40 bg-tertiary-fixed/20"}`}>
                      <div className="flex gap-3">
                        {data.readiness?.ready
                          ? <CheckCircle2 className="w-6 h-6 text-secondary" />
                          : <CircleAlert className="w-6 h-6 text-tertiary" />}
                        <div className="min-w-0">
                          <h2 className="font-headline-md text-lg">{data.readiness?.ready ? "Ready for generation" : "Setup incomplete"}</h2>
                          {data.readiness?.ready && !syncFailed && <Link to="/master-schedule" className="inline-flex items-center mt-2 min-h-11 md:min-h-6 text-primary font-bold underline">Go to Master Schedule</Link>}
                          {!!data.readiness?.blockers.length && (
                            <ul aria-label="Setup checklist" className="mt-1 space-y-1">
                              {data.readiness.blockers.map((blocker, index) => (
                                <li key={`${blocker.code}-${blocker.message}-${index}`} className="text-sm break-words">
                                  {blocker.message}
                                  <BlockerFixLink code={blocker.code} />
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      </div>
                    </section>

                    {data.readiness && <ReadinessWarnings warnings={data.readiness.warnings} />}

                    <div className="grid gap-6">
                      {/* Generation only adds what the curriculum requires and
                          the term lacks, one offering per Block per subject.
                          Merging Blocks into one group and adding electives
                          stay manual, below. */}
                      <Panel id="generate-from-curriculum" title="Generate from curriculum" description="Create a cohort's Blocks and the classes its curriculum requires this term. Running it again creates only what is missing.">
                        {activePrograms.length === 0 && (
                          <p className="text-sm mb-3">
                            No Course Programs yet.{" "}
                            <a href="#course-programs" onClick={() => setAdvancedChoice(true)} className="text-primary font-bold underline">Add a Course Program</a>
                          </p>
                        )}
                        <form onSubmit={submitCohort} className="grid grid-cols-2 items-end gap-3 max-w-3xl">
                          <Field label="Course Program">
                            <select value={cohortProgram} onChange={(event) => setCohortProgram(event.target.value)} required className={inputClass}>
                              <option value="">Select Course Program</option>
                              {activePrograms.map((program) => (
                                <option key={program.program_code} value={program.program_code}>{program.program_code}</option>
                              ))}
                            </select>
                          </Field>
                          <Field label="Year level">
                            <select value={cohortYear} onChange={(event) => setCohortYear(event.target.value)} required className={inputClass}>
                              {YEAR_LEVELS.map((year) => (
                                <option key={year} value={year}>Year {year}</option>
                              ))}
                            </select>
                          </Field>
                          <Field label="Number of sections">
                            <input
                              type="number"
                              min={1}
                              max={MAX_SECTIONS}
                              step={1}
                              inputMode="numeric"
                              value={cohortSections}
                              onChange={(event) => setCohortSections(event.target.value)}
                              required
                              aria-invalid={!sectionCountValid}
                              aria-describedby="cohort-sections-preview"
                              className={inputClass}
                            />
                          </Field>
                          <div className="block space-y-1.5">
                            <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Sections</span>
                            <p
                              id="cohort-sections-preview"
                              aria-live="polite"
                              className="min-w-0 break-words px-3 py-2.5 border border-dashed border-outline-variant rounded-lg bg-surface-container-highest/40 text-on-surface-variant"
                            >
                              {sectionCountValid
                                ? <>{cohortLetters.join(", ")} <span className="text-xs">(derived)</span></>
                                : `Enter 1 to ${MAX_SECTIONS}`}
                            </p>
                          </div>
                          <button disabled={!cohortProgram || !sectionCountValid} className={`${buttonClass} col-span-2`}><Plus className="w-4 h-4" /> Generate Blocks and classes</button>
                        </form>
                        {cohortReport?.term_id === activeTermId && <CohortReport report={cohortReport} />}
                      </Panel>

                      {/* A Block is a cohort, not a class list: the form collects
                          the identity tuple only, and the subjects the cohort
                          takes are linked in Block subjects below. */}
                      <Panel title="Block sections" description={`${data.blocks.length} Block Sections for the active term. Subject Offerings are linked in Block subjects.`}>
                        <details className="mb-5">
                        <summary className="cursor-pointer text-sm font-bold mb-3 py-3 md:py-0.5">Add one Block without curriculum classes</summary>
                        <form onSubmit={submitBlock} className="grid grid-cols-2 items-end gap-3">
                          <Field label="Course Program">
                            <select value={blockProgram} onChange={(event) => setBlockProgram(event.target.value)} required className={inputClass}>
                              <option value="">Select Course Program</option>
                              {activePrograms.map((program) => (
                                <option key={program.program_code} value={program.program_code}>{program.program_code}</option>
                              ))}
                            </select>
                          </Field>
                          <Field label="Year level">
                            <select value={blockYear} onChange={(event) => setBlockYear(event.target.value)} required className={inputClass}>
                              {YEAR_LEVELS.map((year) => (
                                <option key={year} value={year}>Year {year}</option>
                              ))}
                            </select>
                          </Field>
                          {/* One letter, stored uppercase: the derived name reads
                              the letter back verbatim, so "a" and "A" would be two
                              Blocks that display identically. */}
                          <Field label="Section letter">
                            <input
                              value={blockLetter}
                              onChange={(event) => setBlockLetter(event.target.value.toUpperCase())}
                              placeholder="A"
                              maxLength={1}
                              pattern="[A-Z]"
                              title="A single letter, A to Z."
                              required
                              className={inputClass}
                            />
                          </Field>
                          <DerivedBlockName name={derivedBlockName(blockProgram, blockYear, blockLetter)} />
                          <button disabled={!blockProgram || !blockLetter} className={`${buttonClass} col-span-2`}><Plus className="w-4 h-4" /> Add Block</button>
                        </form>
                        </details>
                        {data.blocks.length === 0 ? (
                          <p className="text-sm text-on-surface-variant">No Block Sections for this term yet.</p>
                        ) : (
                          <>
                          {data.blocks.length > LONG_BLOCK_LIST && (
                            <a href="#course-programs" onClick={() => setAdvancedChoice(true)} className="inline-flex items-center min-h-11 md:min-h-6 mb-3 text-primary font-bold underline">Jump to Advanced setup</a>
                          )}
                          <BlockTable
                            blocks={data.blocks}
                            blockClasses={data.blockClasses}
                            onAddClasses={setAddingClassesTo}
                            onEdit={editBlock}
                            onDelete={(block) => confirmDelete(`Block ${block.name}`, "This removes the Block and the class assignments listed on it. A Block whose classes already have schedule records cannot be removed.", () => api.deleteBlock(block.block_id))}
                          />
                          </>
                        )}
                      </Panel>
                    </div>

                    {/* Everything the common path does not need. It opens by itself when a
                        prerequisite inside it is missing, so nothing required is hidden. */}
                    <details
                      open={advancedOpen}
                      // The browser also fires this for a change React made, so
                      // only a difference from what was rendered is the user's.
                      onToggle={(event) => {
                        if (event.currentTarget.open !== advancedOpen) setAdvancedChoice(event.currentTarget.open);
                      }}
                      className="border border-outline-variant rounded-xl p-5"
                    >
                      <summary className="cursor-pointer font-headline-md text-lg py-3 md:py-0.5">Advanced setup</summary>
                      <p className="text-sm text-on-surface-variant mt-1 mb-5">Course Programs, the time-slot calendar, and manual class groups for electives or shared classes.</p>
                      <div className="grid xl:grid-cols-2 gap-6">
                      <Panel id="course-programs" title="Course Programs" description={`${activePrograms.length} active Course Program catalog records`}>
                        <form onSubmit={submitProgram} className="grid sm:grid-cols-[140px_1fr_auto] items-end gap-3 mb-5">
                          <Field label="Course Program code">
                            <input value={programCode} onChange={(event) => setProgramCode(event.target.value)} placeholder="BSCS" maxLength={20} required className={inputClass} />
                          </Field>
                          <Field label="Course Program name">
                            <input value={programName} onChange={(event) => setProgramName(event.target.value)} maxLength={150} required className={inputClass} />
                          </Field>
                          <button className={buttonClass}><Plus className="w-4 h-4" /> Add</button>
                        </form>
                        {inactivePrograms.length > 0 && (
                          <div className="mb-3">
                            <ShowInactiveToggle checked={showInactivePrograms} onChange={setShowInactivePrograms} count={inactivePrograms.length} />
                          </div>
                        )}
                        <div className="max-h-52 overflow-y-auto divide-y divide-outline-variant">
                          {data.programs.filter((program) => showInactivePrograms || isActive(program)).map((program) => (
                            <div key={program.program_code} className={`py-2 flex flex-wrap md:flex-nowrap justify-between gap-3 ${isActive(program) ? "" : "opacity-80"}`}>
                              <span className="min-w-0 break-words flex flex-wrap items-center gap-2">
                                <strong>{program.program_code}</strong>
                                <span className="text-sm text-on-surface-variant">{program.program_name}</span>
                                {!isActive(program) && <InactiveBadge />}
                              </span>
                              {isActive(program) ? (
                                <span className="flex gap-3 md:shrink-0">
                                  <button onClick={() => editCourseProgram(program)} className={`${touchTarget} text-xs text-primary hover:underline`}>Edit</button>
                                  <button
                                    onClick={() => confirmDelete(program.program_code, "This deactivates the Course Program: nothing is deleted, past Blocks and curricula keep naming it, and no new Blocks can be created for it. You can activate it again from this list.", () => api.deactivateCourseProgram(program.program_code))}
                                    className={`${touchTarget} text-xs text-error hover:underline`}
                                  >
                                    Deactivate
                                  </button>
                                </span>
                              ) : (
                                <button
                                  onClick={() => void activateProgram(program.program_code)}
                                  className={`${touchTarget} text-xs text-primary hover:underline md:shrink-0`}
                                >
                                  Activate
                                </button>
                              )}
                            </div>
                          ))}
                        </div>
                        {programError && <p role="alert" className="text-sm text-error mt-2">{programError}</p>}
                      </Panel>

                      <Panel
                        id="time-slots"
                        title="Time slots"
                        description={`${data.slots.length} canonical slots generated from the Monday–Sunday 07:30–21:00 calendar`}
                      >
                        <div className="bg-surface-container-low/60 border border-outline-variant rounded-lg p-4 mb-4 space-y-3">
                          <div className="flex justify-between items-center">
                            <span className="font-label-md text-[11px] uppercase tracking-wider text-primary font-bold">
                              Configured Calendar (Read-Only)
                            </span>
                            <span className="text-xs bg-surface-container-highest px-2 py-0.5 rounded text-on-surface-variant font-medium">
                              {data.slots.length} slots generated
                            </span>
                          </div>
                          <dl className="grid sm:grid-cols-2 gap-3 text-sm">
                            <div>
                              <dt className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Teaching days</dt>
                              <dd className="font-medium mt-0.5">Monday – Sunday</dd>
                            </div>
                            <div>
                              <dt className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Teaching hours</dt>
                              <dd className="font-medium mt-0.5">07:30 – 21:00 (30-min intervals)</dd>
                            </div>
                            <div>
                              <dt className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Lecture duration (LEC)</dt>
                              <dd className="font-medium mt-0.5">1h 30m</dd>
                            </div>
                            <div>
                              <dt className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Laboratory duration (LAB)</dt>
                              <dd className="font-medium mt-0.5">2h 30m</dd>
                            </div>
                          </dl>
                        </div>

                        {slotError && (
                          <div role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-3 text-sm flex justify-between items-center gap-2 mb-3">
                            <span>{slotError}</span>
                            <button
                              type="button"
                              onClick={() => void generateSlots()}
                              className={`${touchTarget} text-xs font-bold underline hover:opacity-80 shrink-0`}
                            >
                              Retry
                            </button>
                          </div>
                        )}

                        {data.slots.length === 0 ? (
                          <div className="border border-tertiary/40 bg-tertiary-fixed/20 rounded-lg p-4 space-y-3">
                            <div className="flex items-start gap-3">
                              <CircleAlert className="w-5 h-5 text-tertiary shrink-0 mt-0.5" />
                              <div>
                                <p className="font-medium text-sm">No time slots generated for the active term.</p>
                                <p className="text-xs text-on-surface-variant mt-1">
                                  Canonical meeting windows must be materialized from the local calendar template.
                                </p>
                              </div>
                            </div>
                            <button
                              type="button"
                              onClick={() => void generateSlots()}
                              disabled={generatingSlots}
                              className={buttonClass}
                            >
                              <RefreshCw className={`w-4 h-4 ${generatingSlots ? "animate-spin" : ""}`} />
                              {generatingSlots ? "Generating slots..." : "Generate canonical slots"}
                            </button>
                          </div>
                        ) : (
                          <div role="region" aria-label="Time slot list" tabIndex={0} className="max-h-52 overflow-y-auto divide-y divide-outline-variant focus:outline focus:outline-2 focus:outline-primary">
                            {data.slots.map((slot) => (
                              <div key={slot.time_slot_id} className="py-2 flex justify-between items-center gap-3 text-sm">
                                <div>
                                  <strong className="font-medium">{slot.label}</strong>
                                  <span className="text-on-surface-variant ml-2">{slot.start_time} - {slot.end_time}</span>
                                </div>
                                <span className="text-xs text-on-surface-variant font-mono">Ordinal {slot.ordinal}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </Panel>

                      <Panel title="Subject offerings" description={`${data.offerings.length} offerings, ${offeringUnits} units for the active term`}>
                        <form onSubmit={submitOffering} className="grid sm:grid-cols-[1fr_170px_auto] items-end gap-3 mb-5">
                          <SearchSelect
                            label="Subject"
                            value={offeringSubject}
                            options={subjectOptions(data.subjects)}
                            onChange={setOfferingSubject}
                            placeholder="Search by code or title"
                            loading={loading}
                            loadingMessage="Loading subjects..."
                            filter={subjectFilter}
                            emptyMessage="No subjects in the catalog yet."
                          />
                          <DerivedUnits subject={data.subjects.find((subject) => subject.subject_code === offeringSubject) ?? null} />
                          {/* Nothing to submit until a catalog record is chosen,
                              which is what keeps an unmatched typed code out. */}
                          <button disabled={!offeringSubject} className={buttonClass}><Plus className="w-4 h-4" /> Add offering</button>
                        </form>
                        <div className="max-h-52 overflow-y-auto divide-y divide-outline-variant">
                          {data.offerings.map((offering) => {
                            const subject = data.subjects.find((item) => item.subject_code === offering.subject_code);
                            return (
                              <div key={offering.offering_id} className="py-2 flex flex-wrap md:flex-nowrap justify-between gap-3">
                                <span className="min-w-0 break-words">
                                  <strong>{offering.group_label}</strong>
                                  <span className="text-sm text-on-surface-variant ml-2">{subject?.subject_title ?? offering.subject_code}</span>
                                </span>
                                <span className="flex flex-wrap items-center gap-3 md:shrink-0">
                                  <span className="text-sm text-on-surface-variant">{subject ? `${subject.units} units` : ""}</span>
                                  <RecordActions
                                    onDelete={() => confirmDelete(`offering ${offering.group_label}`, "This removes the offering from the term; the other groups keep their numbers. An offering that a Block class uses cannot be removed.", () => api.deleteOffering(offering.offering_id))}
                                  />
                                </span>
                              </div>
                            );
                          })}
                        </div>
                      </Panel>

                      <Panel id="block-subjects" title="Block subjects" description={`${data.blockClasses.length} classes for the active term`}>
                        <form onSubmit={submitBlockClass} className="grid grid-cols-2 items-end gap-3 mb-5">
                          <SearchSelect
                            label="Block"
                            value={classBlock}
                            options={blockOptions(data.blocks)}
                            onChange={setClassBlock}
                            placeholder="Search by Block name"
                            loading={loading}
                            loadingMessage="Loading Blocks..."
                            emptyMessage="No Blocks for this term yet."
                          />
                          <SearchSelect
                            label="Subject offering"
                            value={classOffering}
                            options={offeringOptions(data.offerings, data.subjects)}
                            onChange={setClassOffering}
                            placeholder="Search by code or title"
                            loading={loading}
                            loadingMessage="Loading offerings..."
                            filter={subjectFilter}
                            emptyMessage="No offerings for this term yet."
                          />
                          <button disabled={!classBlock || !classOffering} className={buttonClass}><Plus className="w-4 h-4" /> Add class</button>
                        </form>
                        <div className="max-h-52 overflow-y-auto divide-y divide-outline-variant">
                          {data.blockClasses.map((item) => {
                            const block = data.blocks.find((candidate) => candidate.block_id === item.block_id);
                            return (
                              <div key={item.class_id} className="py-2 flex flex-wrap md:flex-nowrap justify-between gap-3">
                                <span className="min-w-0 break-words"><strong>{block?.name ?? item.block_id}</strong> <span className="text-sm text-on-surface-variant ml-2">{item.subject_code}</span></span>
                                <RecordActions
                                  onDelete={() => confirmDelete(`${item.subject_code} from ${block?.name ?? "this Block"}`, "This takes the class off the Block. A class that already has schedule records cannot be removed.", () => api.removeBlockClass(item.block_id, item.class_id))}
                                />
                              </div>
                            );
                          })}
                        </div>
                      </Panel>
                      </div>
                    </details>
                  </>
                )}
              </>
            )}
          </div>
        </main>
      </div>
      {addingClassesTo && data?.activeTerm && (
        <AddClassesDialog
          block={addingClassesTo}
          termId={data.activeTerm.term_id}
          subjects={data.subjects}
          takenCodes={data.blockClasses.filter((item) => item.block_id === addingClassesTo.block_id).map((item) => item.subject_code)}
          onClose={() => setAddingClassesTo(null)}
          onChanged={() => sync(true)}
        />
      )}
      {editing && (
        <EditDialog
          title={editing.title}
          fields={editing.fields}
          onClose={() => setEditing(null)}
          onSubmit={(values) => saveEdit(editing, values)}
        />
      )}
      {deleting && (
        <ConfirmDeleteDialog
          title={`Delete ${deleting.label}?`}
          subtitle="This cannot be undone."
          actionLabel="Delete"
          pendingLabel="Deleting..."
          fallbackError="The request to the Chronos server failed."
          onConfirm={() => runDelete(deleting)}
          onClose={() => setDeleting(null)}
        >
          <p>{deleting.consequence}</p>
        </ConfirmDeleteDialog>
      )}
      {archiving && (
        <ConfirmDeleteDialog
          title={`Archive ${termLabel(archiving.term)}?`}
          subtitle="You can restore it from Archived terms."
          actionLabel="Archive"
          pendingLabel="Archiving..."
          fallbackError="The request to the Chronos server failed."
          onConfirm={() => runArchive(archiving)}
          onClose={() => setArchiving(null)}
        >
          <p>The term is hidden from the term lists. Its Blocks, offerings, schedules and time slots stay on file.</p>
        </ConfirmDeleteDialog>
      )}
      {addingTerm && data && (
        <AddTermDialog
          existingTerms={data.terms}
          onClose={() => setAddingTerm(false)}
          onCreated={(term) => {
            setPickedYear(term.academic_year);
            setPickedTermId(term.term_id);
            sync(false);
          }}
        />
      )}
    </div>
  );
}
