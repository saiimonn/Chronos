import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  api,
  type HistoricalTermOption,
  type ARMJobStatusResponse,
  type RuleSetDetail,
  type RuleSetFacultyCoverage,
  type RuleSetFacultyCoverageItem,
  type RuleSetRule,
  type RuleSetSummary,
} from "../api/client";
import { FROZEN_THRESHOLDS } from "../config/frozenThresholds";
import RuleSetsPage from "./RuleSetsPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      listRuleSets: vi.fn(),
      getRuleSet: vi.fn(),
      listRuleSetRules: vi.fn(),
      getRuleSetFacultyCoverage: vi.fn(),
      listHistoricalTerms: vi.fn(),
      activateRuleSet: vi.fn(),
      rollbackRuleSet: vi.fn(),
      submitArmJob: vi.fn(),
      getARMJob: vi.fn(),
    },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({
    user: { user_id: "user-1", username: "chair", role: "department_administrator" },
    logout: vi.fn(),
  }),
}));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: null, loading: false }),
}));

const ACTIVE: RuleSetSummary = {
  rule_set_id: "rs-active",
  version: 2,
  status: "active",
  generated_at: "2026-02-01T02:00:00Z",
  activated_at: "2026-02-02T02:00:00Z",
  notes: "Refreshed after the 2024-2025 capture.",
  term_count: 3,
  rule_count: 41,
};

const SUPERSEDED: RuleSetSummary = {
  rule_set_id: "rs-old",
  version: 1,
  status: "superseded",
  generated_at: "2025-08-01T02:00:00Z",
  activated_at: "2025-08-02T02:00:00Z",
  notes: null,
  term_count: 2,
  rule_count: 12,
};

const DRAFT: RuleSetSummary = {
  rule_set_id: "rs-draft",
  version: 3,
  status: "draft",
  generated_at: "2026-03-01T02:00:00Z",
  activated_at: null,
  notes: null,
  term_count: 4,
  rule_count: 58,
};

const ACTIVE_DETAIL: RuleSetDetail = {
  rule_set_id: ACTIVE.rule_set_id,
  version: ACTIVE.version,
  status: "active",
  generated_at: ACTIVE.generated_at,
  activated_at: ACTIVE.activated_at,
  notes: ACTIVE.notes,
  included_terms: [
    { term_id: "term-1", academic_year: "2024-2025", term_number: 1 },
    { term_id: "term-2", academic_year: "2024-2025", term_number: 2 },
  ],
  thresholds: [
    {
      pattern_type: "teacher_subject",
      min_support: "0.10",
      min_confidence: "0.60",
      min_lift: "1.05",
      min_term_recurrence: 2,
      earliest_academic_year: "2018-2019",
    },
    {
      pattern_type: "teacher_day",
      min_support: "0.15",
      min_confidence: "0.65",
      min_lift: "1.10",
      min_term_recurrence: 3,
      earliest_academic_year: "2023-2024",
    },
  ],
  streams: [
    { pattern_type: "teacher_subject", rule_count: 20 },
    { pattern_type: "teacher_day", rule_count: 21 },
  ],
  // An imported package: no run, requester, snapshots or summary recorded.
  job_id: null,
  source_job_id: null,
  requested_by_user_id: null,
  requested_by_username: null,
  source_snapshots: [],
  mining_summary: null,
};

const MINED_DETAIL: RuleSetDetail = {
  ...ACTIVE_DETAIL,
  job_id: "job-arm-0001",
  source_job_id: "job-arm-0001",
  requested_by_user_id: "user-admin-1",
  requested_by_username: "registrar",
  source_snapshots: [
    { snapshot_id: "snap-1", academic_year: "2023-2024", term_number: 2 },
    { snapshot_id: "snap-2", academic_year: "2024-2025", term_number: 1 },
  ],
  mining_summary: {
    input_record_count: 340,
    mined_rule_count: 47,
    persisted_rule_count: 41,
    streams: [
      {
        stream: "teacher_day",
        training_term_count: 2,
        transaction_count: 80,
        mined_rule_count: 21,
        persisted_rule_count: 21,
        excluded_rule_counts: { unknown_faculty: 0, unknown_subject: 0, unknown_room: 0 },
        skipped_reason: null,
      },
      {
        stream: "teacher_subject",
        training_term_count: 2,
        transaction_count: 90,
        mined_rule_count: 26,
        persisted_rule_count: 20,
        excluded_rule_counts: { unknown_faculty: 4, unknown_subject: 2, unknown_room: 0 },
        skipped_reason: null,
      },
      {
        stream: "teacher_subject_room",
        training_term_count: 0,
        transaction_count: 0,
        mined_rule_count: 0,
        persisted_rule_count: 0,
        excluded_rule_counts: {},
        skipped_reason: "no_eligible_terms",
      },
    ],
  },
};

const HISTORICAL_TERMS: HistoricalTermOption[] = [
  {
    snapshot_id: "snap-2",
    academic_year: "2024-2025",
    term_number: 2,
    captured_at: "2026-01-01T02:00:00Z",
    record_count: 120,
    eligible_streams: ["teacher_subject", "teacher_day", "teacher_time", "teacher_subject_room"],
  },
  {
    snapshot_id: "snap-old",
    academic_year: "2018-2019",
    term_number: 1,
    captured_at: "2019-06-01T02:00:00Z",
    record_count: 90,
    eligible_streams: ["teacher_subject"],
  },
  // A term the inclusion policy cannot place — an unreadable academic year, or
  // one older than every stream's window — feeds nothing and cannot be mined.
  {
    snapshot_id: "snap-unusable",
    academic_year: "2010-2011",
    term_number: 1,
    captured_at: "2011-06-01T02:00:00Z",
    record_count: 30,
    eligible_streams: [],
  },
];

function rule(index: number, overrides: Partial<RuleSetRule> = {}): RuleSetRule {
  return {
    rule_id: `rule-${index}`,
    pattern_type: "teacher_subject",
    faculty_id: "fac-1",
    antecedent: [`Faculty ${index}`],
    consequent: [`CS${index}`],
    support: "0.5000",
    confidence: "0.7500",
    lift: "1.2000",
    term_recurrence: 3,
    guidance_score: "0.8000",
    ...overrides,
  };
}

function job(overrides: Partial<ARMJobStatusResponse> = {}): ARMJobStatusResponse {
  return {
    job_id: "job-1",
    kind: "arm",
    status: "queued",
    submitted_at: "2026-03-01T02:00:00Z",
    started_at: null,
    completed_at: null,
    result: null,
    error: null,
    ...overrides,
  };
}

function renderPage() {
  return render(<MemoryRouter><RuleSetsPage /></MemoryRouter>);
}

// Both sides of the page head their versions "Version N", so every query says
// which side it means.
function inventory() {
  return within(screen.getByRole("list", { name: "Rule set versions" }));
}

function coverageItem(overrides: Partial<RuleSetFacultyCoverageItem>): RuleSetFacultyCoverageItem {
  return {
    source_faculty_key: "key",
    faculty_id: "f-1",
    faculty_name: "Teacher",
    on_roster: true,
    is_active: true,
    has_rule: false,
    subject_code: "CS 1101",
    room_code: null,
    meeting_days: null,
    time_slot: null,
    offering_count: 12,
    support: "0.0120",
    confidence: "0.4500",
    lift: "3.1000",
    term_recurrence: 8,
    meets_support: true,
    meets_confidence: true,
    meets_lift: true,
    meets_term_recurrence: true,
    ...overrides,
  };
}

const COVERAGE: RuleSetFacultyCoverage = {
  pattern_type: "teacher_subject",
  metrics_recorded: true,
  version_with_rule_count: 2,
  teacher_count: 3,
  with_rule_count: 1,
  without_rule_count: 2,
  items: [
    coverageItem({ source_faculty_key: "a", faculty_name: "Covered Teacher", has_rule: true }),
    coverageItem({
      source_faculty_key: "b",
      faculty_name: "Rare Teacher",
      support: "0.0016",
      term_recurrence: 1,
      meets_support: false,
      meets_term_recurrence: false,
    }),
    coverageItem({
      source_faculty_key: "c",
      faculty_id: null,
      faculty_name: "Former Teacher",
      on_roster: false,
      is_active: null,
      meets_lift: false,
    }),
  ],
};

function detailPanel() {
  return within(screen.getByRole("region", { name: "Version detail" }));
}

/** Resolves once the page has loaded and opened on the active version. */
async function waitForActiveVersion() {
  return screen.findByRole("region", { name: "Version detail" }).then(async () => {
    await detailPanel().findByRole("heading", { name: "Version 2", level: 2 });
  });
}

beforeEach(() => {
  vi.mocked(api.listRuleSets).mockResolvedValue({ items: [DRAFT, ACTIVE, SUPERSEDED] });
  vi.mocked(api.getRuleSet).mockResolvedValue(ACTIVE_DETAIL);
  vi.mocked(api.listRuleSetRules).mockResolvedValue({
    pattern_type: "teacher_subject",
    total: 0,
    limit: 25,
    offset: 0,
    items: [],
  });
  vi.mocked(api.listHistoricalTerms).mockResolvedValue({ items: HISTORICAL_TERMS });
  vi.mocked(api.getRuleSetFacultyCoverage).mockResolvedValue(COVERAGE);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("version provenance", () => {
  function provenance() {
    return within(detailPanel().getByRole("region", { name: "Provenance" }));
  }

  it("shows who requested a mined version, its run, snapshots, and what filtering kept", async () => {
    vi.mocked(api.getRuleSet).mockResolvedValue(MINED_DETAIL);
    renderPage();
    await waitForActiveVersion();

    expect(provenance().getByText("registrar")).toBeInTheDocument();
    expect(provenance().getByText("job-arm-0001")).toBeInTheDocument();
    expect(provenance().queryByText(/No mining run is recorded/)).not.toBeInTheDocument();
    const snapshots = within(provenance().getByRole("list", { name: "Source snapshots" }));
    expect(snapshots.getByText("AY 2023-2024 · Term 2")).toBeInTheDocument();
    expect(snapshots.getByText("AY 2024-2025 · Term 1")).toBeInTheDocument();
    expect(provenance().getByText(/340 input records · 47 rules mined · 41 rules persisted/)).toBeInTheDocument();

    const rows = provenance().getAllByRole("row").slice(1);
    // Ordered by the SRS stream order, not the order the summary arrived in.
    expect(rows.map((row) => within(row).getByRole("rowheader").textContent)).toEqual([
      "Teacher / Subject",
      "Teacher / Day",
      "Teacher / Subject / RoomSkipped: No eligible terms",
    ]);
    expect(within(rows[0]).getByText("Unknown faculty: 4, Unknown subject: 2")).toBeInTheDocument();
    expect(within(rows[0]).getByText("20")).toBeInTheDocument();
    // Zero-count reasons are not exclusions.
    expect(within(rows[1]).getByText("None")).toBeInTheDocument();
    expect(within(rows[2]).getByText("None")).toBeInTheDocument();
  });

  it("states that no mining run is recorded instead of showing empty provenance", async () => {
    renderPage();
    await waitForActiveVersion();

    // An import and a version mined before job tracking cannot be told apart,
    // so neither is claimed.
    expect(
      provenance().getByText(
        "No mining run is recorded for this version (imported package, or mined before job tracking).",
      ),
    ).toBeInTheDocument();
    expect(provenance().getAllByText("—").length).toBe(3);
    expect(provenance().getByText("No mining summary was recorded for this version.")).toBeInTheDocument();
  });

  it("shows the publishing job of a version mined before provenance tracking, not an import", async () => {
    vi.mocked(api.getRuleSet).mockResolvedValue({ ...ACTIVE_DETAIL, job_id: "job-arm-legacy" });
    renderPage();
    await waitForActiveVersion();

    expect(provenance().getByText("job-arm-legacy")).toBeInTheDocument();
    expect(provenance().getByText(/Mined before provenance tracking/)).toBeInTheDocument();
    expect(provenance().queryByText(/No mining run is recorded/)).not.toBeInTheDocument();
  });

  it("names an unknown account when the requester no longer exists", async () => {
    vi.mocked(api.getRuleSet).mockResolvedValue({
      ...MINED_DETAIL,
      requested_by_user_id: "deadbeef-0000-4000-8000-000000000000",
      requested_by_username: null,
    });
    renderPage();
    await waitForActiveVersion();

    expect(provenance().getByText("Unknown account (deadbeef)")).toBeInTheDocument();
  });
});

describe("version inventory", () => {
  it("lists every version with its status, timestamps, and counts", async () => {
    renderPage();

    await waitForActiveVersion();
    expect(inventory().getByRole("heading", { name: "Version 3", level: 2 })).toBeInTheDocument();
    expect(inventory().getByRole("heading", { name: "Version 1", level: 2 })).toBeInTheDocument();
    expect(screen.getAllByText("Draft").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Active").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Superseded").length).toBeGreaterThan(0);
    expect(screen.getByText("4 terms")).toBeInTheDocument();
    expect(screen.getByText("58 rules")).toBeInTheDocument();
    // A draft has never been activated, and that has to read as a fact rather
    // than an empty cell.
    expect(screen.getAllByText(/Activated Not activated/).length).toBeGreaterThan(0);
  });

  it("opens on the active version and shows its terms, thresholds, and four stream summaries", async () => {
    renderPage();

    await waitForActiveVersion();
    expect(api.getRuleSet).toHaveBeenCalledWith("rs-active");

    expect(detailPanel().getByText("AY 2024-2025 · Term 1")).toBeInTheDocument();
    expect(detailPanel().getByText("Refreshed after the 2024-2025 capture.")).toBeInTheDocument();

    const rows = detailPanel().getAllByRole("row");
    const streamNames = rows.slice(1).map((row) => within(row).getAllByRole("rowheader")[0].textContent);
    expect(streamNames).toEqual([
      "Teacher / Subject",
      "Teacher / Day",
      "Teacher / Time",
      "Teacher / Subject / Room",
    ]);

    // Raw thresholds are shown at the precision the server stored them at.
    const subjectRow = rows.find((row) => within(row).queryByText("Teacher / Subject"))!;
    expect(within(subjectRow).getByText("0.10")).toBeInTheDocument();
    expect(within(subjectRow).getByText("1.05")).toBeInTheDocument();
    expect(within(subjectRow).getByText("2018-2019")).toBeInTheDocument();
    expect(within(subjectRow).getByText("20")).toBeInTheDocument();

    // A stream with no threshold row is still listed, so a version missing one
    // is visible rather than absent.
    const timeRow = rows.find((row) => within(row).queryByText("Teacher / Time"))!;
    expect(within(timeRow).getByText("0")).toBeInTheDocument();
  });

  it("says the inventory is empty rather than showing nothing", async () => {
    vi.mocked(api.listRuleSets).mockResolvedValue({ items: [] });

    renderPage();

    expect(await screen.findByText(/No rule sets have been mined yet/)).toBeInTheDocument();
    expect(api.getRuleSet).not.toHaveBeenCalled();
  });

  it("offers no way to edit the rules or thresholds inside a version", async () => {
    renderPage();
    await waitForActiveVersion();

    // The inventory offers inspection and lifecycle actions only; a version's
    // contents are immutable, so nothing on this page may accept input for them.
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete|remove/i })).not.toBeInTheDocument();
    expect(screen.getByText(/A version is immutable/)).toBeInTheDocument();
  });

  it("inspects the version the administrator selects", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();
    vi.mocked(api.getRuleSet).mockResolvedValue({
      ...ACTIVE_DETAIL,
      rule_set_id: DRAFT.rule_set_id,
      version: 3,
      status: "draft",
      activated_at: null,
      notes: null,
    });

    await user.click(inventory().getByRole("button", { name: "Inspect version 3" }));

    expect(api.getRuleSet).toHaveBeenLastCalledWith("rs-draft");
    expect(await detailPanel().findByText(/No notes were recorded for this version/)).toBeInTheDocument();
  });

  it("names a labelled version by its label instead of its number", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listRuleSets).mockResolvedValue({
      items: [{ ...DRAFT, version_label: "1.1" }, ACTIVE, SUPERSEDED],
    });
    renderPage();
    await waitForActiveVersion();
    expect(inventory().getByRole("heading", { name: "Version 1.1", level: 2 })).toBeInTheDocument();
    expect(inventory().queryByRole("heading", { name: "Version 3", level: 2 })).not.toBeInTheDocument();

    vi.mocked(api.getRuleSet).mockResolvedValue({
      ...ACTIVE_DETAIL,
      rule_set_id: DRAFT.rule_set_id,
      version: 3,
      version_label: "1.1",
      status: "draft",
      activated_at: null,
    });
    await user.click(inventory().getByRole("button", { name: "Inspect version 1.1" }));

    expect(await detailPanel().findByRole("heading", { name: "Version 1.1", level: 2 })).toBeInTheDocument();
  });
});

describe("activation and rollback", () => {
  it("only offers activation on a draft and rollback on a superseded version", async () => {
    renderPage();
    await waitForActiveVersion();

    expect(screen.getAllByRole("button", { name: /Validate and activate/ })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /Roll back to version 1/ })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /Roll back to version 2/ })).not.toBeInTheDocument();
  });

  it("requires an explicit confirmation before activating", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();

    await user.click(screen.getByRole("button", { name: /Validate and activate/ }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Version 2 becomes superseded/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(api.activateRuleSet).not.toHaveBeenCalled();
  });

  it("activates the draft and refreshes the active version state on confirmation", async () => {
    const user = userEvent.setup();
    vi.mocked(api.activateRuleSet).mockResolvedValue({
      ...ACTIVE_DETAIL,
      rule_set_id: DRAFT.rule_set_id,
      version: 3,
      status: "active",
      activated_at: "2026-03-02T02:00:00Z",
    });
    renderPage();
    await waitForActiveVersion();
    vi.mocked(api.listRuleSets).mockResolvedValue({
      items: [
        { ...DRAFT, status: "active", activated_at: "2026-03-02T02:00:00Z" },
        { ...ACTIVE, status: "superseded" },
        SUPERSEDED,
      ],
    });

    await user.click(screen.getByRole("button", { name: /Validate and activate/ }));
    await user.click(screen.getByRole("button", { name: "Activate version 3" }));

    expect(api.activateRuleSet).toHaveBeenCalledWith("rs-draft");
    expect(await detailPanel().findByRole("heading", { name: "Version 3", level: 2 })).toBeInTheDocument();
    // The list is re-read, so the superseded incumbent no longer offers activation.
    expect(screen.queryByRole("button", { name: /Validate and activate/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Roll back to version 2/ })).toBeInTheDocument();
  });

  it("reports a structural validation refusal without changing the version", async () => {
    const user = userEvent.setup();
    vi.mocked(api.activateRuleSet).mockRejectedValue(
      new ApiError(409, "RULE_SET_INVALID", "Rule set must contain at least one extracted rule."),
    );
    renderPage();
    await waitForActiveVersion();

    await user.click(screen.getByRole("button", { name: /Validate and activate/ }));
    await user.click(screen.getByRole("button", { name: "Activate version 3" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Structural validation failed: Rule set must contain at least one extracted rule\. The version stays as it is\./,
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("rolls back to a superseded version on confirmation", async () => {
    const user = userEvent.setup();
    vi.mocked(api.rollbackRuleSet).mockResolvedValue({
      ...ACTIVE_DETAIL,
      rule_set_id: SUPERSEDED.rule_set_id,
      version: 1,
      status: "active",
    });
    renderPage();
    await waitForActiveVersion();

    await user.click(inventory().getByRole("button", { name: /Roll back to version 1/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/rules inside them are never changed/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Roll back to version 1" }));

    expect(api.rollbackRuleSet).toHaveBeenCalledWith("rs-old");
    expect(await detailPanel().findByRole("heading", { name: "Version 1", level: 2 })).toBeInTheDocument();
  });
});

describe("mining a new candidate", () => {
  async function openDialog() {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();
    await user.click(screen.getByRole("button", { name: /Mine new candidate/ }));
    await screen.findByText("Historical terms");
    return user;
  }

  it("lists the eligible historical terms with the streams each one feeds", async () => {
    await openDialog();

    expect(screen.getByLabelText(/AY 2024-2025 · Term 2/)).toBeInTheDocument();
    expect(screen.getByText(/120 records · eligible for Teacher \/ Subject, Teacher \/ Day/)).toBeInTheDocument();
    expect(screen.getByText(/90 records · eligible for Teacher \/ Subject$/)).toBeInTheDocument();
  });

  it("clears a historical-term load error after a periodic retry succeeds", async () => {
    vi.useFakeTimers();
    vi.mocked(api.listHistoricalTerms)
      .mockResolvedValueOnce({ items: HISTORICAL_TERMS })
      .mockRejectedValueOnce(
        new ApiError(503, "SERVICE_UNAVAILABLE", "Historical terms are temporarily unavailable."),
      )
      .mockResolvedValue({ items: HISTORICAL_TERMS });

    renderPage();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(screen.getByRole("button", { name: /Mine new candidate/ }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    fireEvent.submit(screen.getByRole("button", { name: "Start mining run" }).closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Select at least one historical term to mine.",
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(screen.getAllByRole("alert").map((alert) => alert.textContent)).toEqual(
      expect.arrayContaining([
        "Historical terms are temporarily unavailable.",
        "Select at least one historical term to mine.",
      ]),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(
      screen.queryByText("Historical terms are temporarily unavailable."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Select at least one historical term to mine.",
    );
    expect(screen.getByLabelText(/AY 2024-2025 · Term 2/)).toBeInTheDocument();
  });

  it("offers no way to select a term that feeds no stream", async () => {
    await openDialog();

    const unusable = screen.getByLabelText(/AY 2010-2011 · Term 1/);
    expect(unusable).toBeDisabled();
    expect(screen.getByText(/eligible for no stream — cannot be mined/)).toBeInTheDocument();
  });

  it("leads with Historical Upload and Completed History links when no history exists", async () => {
    vi.mocked(api.listHistoricalTerms).mockResolvedValue({ items: [] });
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();
    await user.click(screen.getByRole("button", { name: /Mine new candidate/ }));

    expect(await screen.findByRole("link", { name: "Go to Historical Upload" })).toHaveAttribute(
      "href",
      "/historical-upload",
    );
    expect(screen.getByRole("link", { name: "Go to Completed History" })).toHaveAttribute(
      "href",
      "/history",
    );
    expect(screen.queryByRole("button", { name: "Start mining run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Teacher / Subject" })).not.toBeInTheDocument();
  });

  it("moves focus into the empty state and announces it", async () => {
    vi.mocked(api.listHistoricalTerms).mockResolvedValue({ items: [] });
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();
    await user.click(screen.getByRole("button", { name: /Mine new candidate/ }));

    const status = await screen.findByRole("status", { name: "" });
    await waitFor(() => expect(status).toHaveFocus());
    expect(status).toHaveTextContent(/No historical terms have been captured yet/);
    // jsdom has no layout; the panel clips at 90vh, so the state must scroll itself.
    expect(status).toHaveClass("min-h-0", "overflow-y-auto");
  });

  it("moves focus into the form when terms arrive after an empty read", async () => {
    vi.useFakeTimers();
    vi.mocked(api.listHistoricalTerms)
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValue({ items: HISTORICAL_TERMS });
    renderPage();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(screen.getByRole("button", { name: /Mine new candidate/ }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("status", { name: "" })).toHaveFocus();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(document.body);
  });

  it("says terms are loading, not that nothing can be mined, while the read is pending", async () => {
    vi.mocked(api.listHistoricalTerms).mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();
    await user.click(screen.getByRole("button", { name: /Mine new candidate/ }));

    expect(await screen.findByText("Loading historical terms...", { selector: "#start-mining-hint" })).toBeInTheDocument();
    expect(screen.queryByText(/nothing to mine/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Go to Historical Upload" })).not.toBeInTheDocument();
  });

  it("says terms could not be loaded, beside the hint, after a failed first read", async () => {
    vi.mocked(api.listHistoricalTerms).mockRejectedValue(
      new ApiError(503, "SERVICE_UNAVAILABLE", "Historical terms are temporarily unavailable."),
    );
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();
    await user.click(screen.getByRole("button", { name: /Mine new candidate/ }));

    expect(await screen.findByText(/could not be loaded, so mining cannot start yet/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Historical terms are temporarily unavailable.");
    expect(screen.queryByText(/nothing to mine/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start mining run" })).toBeDisabled();
  });

  it("offers the history links beside Start mining run when every captured term is unusable", async () => {
    vi.mocked(api.listHistoricalTerms).mockResolvedValue({
      items: [HISTORICAL_TERMS[HISTORICAL_TERMS.length - 1]],
    });
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();
    await user.click(screen.getByRole("button", { name: /Mine new candidate/ }));

    expect(await screen.findByText(/No captured term can feed a stream yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Historical Upload" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Completed History" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start mining run" })).toBeDisabled();
  });

  it("keeps the empty state and shows the alert when a later poll fails", async () => {
    vi.useFakeTimers();
    vi.mocked(api.listHistoricalTerms)
      .mockResolvedValueOnce({ items: [] })
      .mockRejectedValue(
        new ApiError(503, "SERVICE_UNAVAILABLE", "Historical terms are temporarily unavailable."),
      );
    renderPage();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(screen.getByRole("button", { name: /Mine new candidate/ }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("link", { name: "Go to Historical Upload" })).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(screen.getByRole("link", { name: "Go to Historical Upload" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start mining run" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Historical terms are temporarily unavailable.");
  });

  it("keeps Start mining run disabled, with an adjacent reason, until a term is selected", async () => {
    const user = await openDialog();

    const start = screen.getByRole("button", { name: "Start mining run" });
    expect(start).toBeDisabled();
    const hint = screen.getByText("Select at least one historical term to enable mining.");
    expect(start).toHaveAttribute("aria-describedby", hint.id);

    await user.click(screen.getByLabelText(/AY 2024-2025 · Term 2/));
    expect(start).toBeEnabled();
    expect(start).not.toHaveAttribute("aria-describedby");
  });

  it("bounds the notes field at the length the draft can be stored with", async () => {
    await openDialog();

    // `RuleSet.notes` is VARCHAR(500), and the submission schema bounds the
    // request at the same length.
    expect(screen.getByLabelText("Notes")).toHaveAttribute("maxlength", "500");
  });

  it("pre-fills the frozen thresholds per stream, warns on deviation and resets", async () => {
    const user = await openDialog();
    const streams = [
      ["Teacher / Subject", [0.0025, 0.2, 2, 3]],
      ["Teacher / Day", [0.005, 0.2, 1.25, 3]],
      ["Teacher / Time", [0.005, 0.1, 2, 3]],
      ["Teacher / Subject / Room", [0.0025, 0.5, 2, 2]],
    ] as const;
    for (const [name, [support, confidence, lift, recurrence]] of streams) {
      const group = screen.getByRole("group", { name });
      expect(within(group).getByLabelText("Min support")).toHaveValue(support);
      expect(within(group).getByLabelText("Min confidence")).toHaveValue(confidence);
      expect(within(group).getByLabelText("Min lift")).toHaveValue(lift);
      expect(within(group).getByLabelText("Min term recurrence")).toHaveValue(recurrence);
    }
    expect(screen.queryByText(/differs from the frozen thesis value/)).not.toBeInTheDocument();

    const support = within(screen.getByRole("group", { name: "Teacher / Subject" })).getByLabelText(
      "Min support",
    );
    await user.clear(support);
    await user.type(support, "0.10");
    // jsdom normalizes a number input's "0.10" to "0.1"; browsers keep it as typed.
    expect(
      screen.getByText(/Teacher \/ Subject min support 0\.10? differs from the frozen thesis value 0\.0025/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reset to frozen thresholds" }));
    expect(screen.queryByText(/differs from the frozen thesis value/)).not.toBeInTheDocument();
    expect(support).toHaveValue(0.0025);
  });

  it("still refuses a submit with no term selected", async () => {
    await openDialog();

    fireEvent.submit(screen.getByRole("button", { name: "Start mining run" }).closest("form")!);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Select at least one historical term to mine.",
    );
    expect(api.submitArmJob).not.toHaveBeenCalled();
  });

  it("submits the selected terms with per-stream thresholds and notes", async () => {
    const user = await openDialog();
    vi.mocked(api.submitArmJob).mockResolvedValue({ job_id: "job-1", kind: "arm", status: "queued" });
    vi.mocked(api.getARMJob).mockResolvedValue(job());

    await user.click(screen.getByLabelText(/AY 2024-2025 · Term 2/));
    const subjectStream = screen.getByRole("group", { name: "Teacher / Subject" });
    await user.clear(within(subjectStream).getByLabelText("Min support"));
    await user.type(within(subjectStream).getByLabelText("Min support"), "0.25");
    await user.type(screen.getByLabelText("Notes"), "Post-2024 refresh");
    await user.click(screen.getByRole("button", { name: "Start mining run" }));

    expect(api.submitArmJob).toHaveBeenCalledWith({
      selected_snapshot_ids: ["snap-2"],
      notes: "Post-2024 refresh",
      thresholds: {
        ...FROZEN_THRESHOLDS,
        teacher_subject: { ...FROZEN_THRESHOLDS.teacher_subject, min_support: "0.25" },
      },
    });
  });

  it("accepts thresholds at the four decimal places the API and column store", async () => {
    const user = await openDialog();
    vi.mocked(api.submitArmJob).mockResolvedValue({ job_id: "job-1", kind: "arm", status: "queued" });
    vi.mocked(api.getARMJob).mockResolvedValue(job());

    await user.click(screen.getByLabelText(/AY 2024-2025 · Term 2/));
    const subjectStream = screen.getByRole("group", { name: "Teacher / Subject" });
    const support = within(subjectStream).getByLabelText("Min support");
    const lift = within(subjectStream).getByLabelText("Min lift");
    await user.clear(support);
    await user.type(support, "0.0025");
    await user.clear(lift);
    await user.type(lift, "1.0025");

    // Thresholds are stored as NUMERIC(5, 4), so the browser's own step and
    // minimum must accept a fourth decimal place rather than reject it.
    expect((support as HTMLInputElement).checkValidity()).toBe(true);
    expect((lift as HTMLInputElement).checkValidity()).toBe(true);

    await user.click(screen.getByRole("button", { name: "Start mining run" }));

    expect(api.submitArmJob).toHaveBeenCalledWith(
      expect.objectContaining({
        thresholds: expect.objectContaining({
          teacher_subject: {
            min_support: "0.0025",
            min_confidence: "0.2000",
            min_lift: "1.0025",
            min_term_recurrence: 3,
          },
        }),
      }),
    );
  });

  it("cannot be dismissed while the submission that starts the run is in flight", async () => {
    const user = await openDialog();

    // The run is already being created server-side. Dismissing would read as
    // calling it off while the page went on to track it anyway.
    let releaseSubmit: () => void = () => {};
    vi.mocked(api.submitArmJob).mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseSubmit = () => resolve({ job_id: "job-1", kind: "arm", status: "queued" });
        }),
    );
    vi.mocked(api.getARMJob).mockResolvedValue(job({ status: "running" }));

    await user.click(screen.getByLabelText(/AY 2024-2025 · Term 2/));
    await user.click(screen.getByRole("button", { name: "Start mining run" }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Close dialog" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    releaseSubmit();

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("Mining run in progress")).toBeInTheDocument();
    expect(api.submitArmJob).toHaveBeenCalledTimes(1);
  });

  it("surfaces a rejected selection instead of leaving the dialog silent", async () => {
    const user = await openDialog();
    vi.mocked(api.submitArmJob).mockRejectedValue(
      new ApiError(
        422,
        "VALIDATION_ERROR",
        "The selected historical terms leave no training data.",
        { selected_snapshot_ids: ["snap-old"] },
      ),
    );

    await user.click(screen.getByLabelText(/AY 2018-2019 · Term 1/));
    await user.click(screen.getByRole("button", { name: "Start mining run" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The selected historical terms leave no training data.",
    );
  });
});

describe("mining run progress and outcomes", () => {
  async function submitRun() {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();
    await user.click(screen.getByRole("button", { name: /Mine new candidate/ }));
    await screen.findByText("Historical terms");
    await user.click(screen.getByLabelText(/AY 2024-2025 · Term 2/));
    vi.mocked(api.submitArmJob).mockResolvedValue({ job_id: "job-1", kind: "arm", status: "queued" });
    await user.click(screen.getByRole("button", { name: "Start mining run" }));
    return user;
  }

  it("polls a queued run until it succeeds and selects the draft it produced", async () => {
    // Testing Library's waitFor only knows how to drive Jest's fake clock, so
    // this test advances the timers itself and asserts synchronously between
    // ticks rather than using any findBy* query.
    vi.useFakeTimers();
    async function tick(ms = 0) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
      });
    }
    vi.mocked(api.submitArmJob).mockResolvedValue({ job_id: "job-1", kind: "arm", status: "queued" });
    vi.mocked(api.getARMJob)
      .mockResolvedValueOnce(job({ status: "queued" }))
      .mockResolvedValue(
        job({
          status: "succeeded",
          completed_at: "2026-03-01T02:10:00Z",
          result: { rule_set_id: "rs-draft" },
        }),
      );

    renderPage();
    await tick();
    fireEvent.click(screen.getByRole("button", { name: /Mine new candidate/ }));
    await tick();
    fireEvent.click(screen.getByLabelText(/AY 2024-2025 · Term 2/));
    fireEvent.click(screen.getByRole("button", { name: "Start mining run" }));
    await tick();

    expect(screen.getByText("Mining run queued")).toBeInTheDocument();

    await tick(2000);

    expect(screen.getByText("Mining run succeeded")).toBeInTheDocument();
    expect(
      screen.getByText(/Draft version 3 is ready for review\. It stays a draft until you validate and activate it\./),
    ).toBeInTheDocument();
    // The produced draft is the version now on screen, without an extra click.
    expect(api.getRuleSet).toHaveBeenLastCalledWith("rs-draft");

    // Polling stops once the job is terminal.
    const callsWhenSettled = vi.mocked(api.getARMJob).mock.calls.length;
    await tick(10_000);
    expect(vi.mocked(api.getARMJob).mock.calls).toHaveLength(callsWhenSettled);
  });

  it("reports a run that retained no rules as an outcome, not a crash", async () => {
    vi.mocked(api.getARMJob).mockResolvedValue(
      job({
        status: "failed",
        completed_at: "2026-03-01T02:10:00Z",
        error: { code: "arm_no_rules_retained", message: "No pattern met the thresholds." },
      }),
    );

    await submitRun();

    expect(await screen.findByText("Mining run retained no rules")).toBeInTheDocument();
    expect(
      screen.getByText(/no draft version was created\. The active rule set is unchanged\./),
    ).toBeInTheDocument();
  });

  it("reports a timed-out run", async () => {
    vi.mocked(api.getARMJob).mockResolvedValue(job({ status: "timed_out" }));

    await submitRun();

    expect(await screen.findByText("Mining run timed out")).toBeInTheDocument();
    expect(screen.getByText(/exceeded its configured time limit/)).toBeInTheDocument();
  });

  it("reports a failed run with the worker's reason", async () => {
    vi.mocked(api.getARMJob).mockResolvedValue(
      job({
        status: "failed",
        error: { code: "execution_failed", message: "Historical record is missing a time slot." },
      }),
    );

    await submitRun();

    expect(await screen.findByText("Mining run failed")).toBeInTheDocument();
    expect(
      screen.getByText(/Historical record is missing a time slot\. The active rule set is unchanged\./),
    ).toBeInTheDocument();
  });

  it("selects the produced draft even when the inventory read resolves after polling stops", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();

    // The terminal status disables job polling the moment it arrives, so the
    // refresh that follows it must not be abandoned when it resolves afterwards.
    let releaseInventory: () => void = () => {};
    vi.mocked(api.listRuleSets).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseInventory = () => resolve({ items: [DRAFT, ACTIVE, SUPERSEDED] });
        }),
    );
    vi.mocked(api.submitArmJob).mockResolvedValue({ job_id: "job-1", kind: "arm", status: "queued" });
    vi.mocked(api.getARMJob).mockResolvedValue(
      job({
        status: "succeeded",
        completed_at: "2026-03-01T02:10:00Z",
        result: { rule_set_id: "rs-draft" },
      }),
    );

    await user.click(screen.getByRole("button", { name: /Mine new candidate/ }));
    await screen.findByText("Historical terms");
    await user.click(screen.getByLabelText(/AY 2024-2025 · Term 2/));
    await user.click(screen.getByRole("button", { name: "Start mining run" }));
    await screen.findByText("Mining run succeeded");

    releaseInventory();

    await waitFor(() => expect(api.getRuleSet).toHaveBeenLastCalledWith("rs-draft"));
  });

  it("offers no second run while the first is still being tracked", async () => {
    // Fake timers for the same reason as the polling test above: the tick that
    // settles the run has to be driven by hand.
    vi.useFakeTimers();
    async function tick(ms = 0) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
      });
    }
    vi.mocked(api.submitArmJob).mockResolvedValue({ job_id: "job-1", kind: "arm", status: "queued" });
    vi.mocked(api.getARMJob)
      .mockResolvedValueOnce(job({ status: "running" }))
      .mockResolvedValue(
        job({
          status: "succeeded",
          completed_at: "2026-03-01T02:10:00Z",
          result: { rule_set_id: "rs-draft" },
        }),
      );

    renderPage();
    await tick();
    fireEvent.click(screen.getByRole("button", { name: /Mine new candidate/ }));
    await tick();
    fireEvent.click(screen.getByLabelText(/AY 2024-2025 · Term 2/));
    fireEvent.click(screen.getByRole("button", { name: "Start mining run" }));
    await tick();

    // A second submission would replace the job being polled while the first
    // run kept going on the server and produced a draft nobody was watching for.
    expect(screen.getByText("Mining run in progress")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Mine new candidate/ })).toBeDisabled();

    await tick(2000);

    expect(screen.getByText("Mining run succeeded")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Mine new candidate/ })).toBeEnabled();
    expect(api.submitArmJob).toHaveBeenCalledTimes(1);
  });

  it("says when the run's progress could not be read", async () => {
    vi.mocked(api.getARMJob).mockRejectedValue(
      new ApiError(503, "SERVICE_UNAVAILABLE", "The worker is not reachable."),
    );

    await submitRun();

    expect(await screen.findByRole("alert")).toHaveTextContent("The worker is not reachable.");
  });
});

describe("out-of-order responses", () => {
  it("keeps the selected version on screen when a slower earlier detail read resolves late", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();

    // Version 3 is asked for first but answers last.
    let releaseDraft: () => void = () => {};
    vi.mocked(api.getRuleSet).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseDraft = () =>
            resolve({
              ...ACTIVE_DETAIL,
              rule_set_id: DRAFT.rule_set_id,
              version: 3,
              status: "draft",
              activated_at: null,
            });
        }),
    );
    await user.click(inventory().getByRole("button", { name: "Inspect version 3" }));

    vi.mocked(api.getRuleSet).mockResolvedValue({
      ...ACTIVE_DETAIL,
      rule_set_id: SUPERSEDED.rule_set_id,
      version: 1,
      status: "superseded",
    });
    await user.click(inventory().getByRole("button", { name: "Inspect version 1" }));
    await detailPanel().findByRole("heading", { name: "Version 1", level: 2 });

    releaseDraft();
    await act(async () => {});

    // Version 1 is what the administrator is looking at, so the late answer to
    // the earlier selection is discarded rather than applied over it.
    expect(detailPanel().getByRole("heading", { name: "Version 1", level: 2 })).toBeInTheDocument();
    expect(detailPanel().queryByRole("heading", { name: "Version 3", level: 2 })).not.toBeInTheDocument();
  });

  it("keeps an explicit selection made while the settled run's inventory read is in flight", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();

    // The run settles, but the inventory read that reconciles it is slow.
    let releaseInventory: () => void = () => {};
    vi.mocked(api.listRuleSets).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseInventory = () => resolve({ items: [DRAFT, ACTIVE, SUPERSEDED] });
        }),
    );
    vi.mocked(api.submitArmJob).mockResolvedValue({ job_id: "job-1", kind: "arm", status: "queued" });
    vi.mocked(api.getARMJob).mockResolvedValue(
      job({
        status: "succeeded",
        completed_at: "2026-03-01T02:10:00Z",
        result: { rule_set_id: "rs-draft" },
      }),
    );

    await user.click(screen.getByRole("button", { name: /Mine new candidate/ }));
    await screen.findByText("Historical terms");
    await user.click(screen.getByLabelText(/AY 2024-2025 · Term 2/));
    await user.click(screen.getByRole("button", { name: "Start mining run" }));
    await screen.findByText("Mining run succeeded");

    // While that read is outstanding, the administrator asks for another version.
    vi.mocked(api.getRuleSet).mockResolvedValue({
      ...ACTIVE_DETAIL,
      rule_set_id: SUPERSEDED.rule_set_id,
      version: 1,
      status: "superseded",
    });
    await user.click(inventory().getByRole("button", { name: "Inspect version 1" }));
    await detailPanel().findByRole("heading", { name: "Version 1", level: 2 });

    releaseInventory();
    await act(async () => {});

    // That choice is newer than the run's result, so the produced draft does not
    // replace it. The banner still reports the run.
    expect(detailPanel().getByRole("heading", { name: "Version 1", level: 2 })).toBeInTheDocument();
    expect(api.getRuleSet).not.toHaveBeenCalledWith("rs-draft");
    expect(screen.getByText("Mining run succeeded")).toBeInTheDocument();
  });

  it("keeps the newly activated statuses when a slower earlier inventory read resolves late", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();

    // A periodic inventory read is already in flight, carrying the statuses that
    // activation is about to supersede.
    let releasePeriodic: () => void = () => {};
    vi.mocked(api.listRuleSets).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releasePeriodic = () => resolve({ items: [DRAFT, ACTIVE, SUPERSEDED] });
        }),
    );
    fireEvent.focus(window);

    vi.mocked(api.activateRuleSet).mockResolvedValue({
      ...ACTIVE_DETAIL,
      rule_set_id: DRAFT.rule_set_id,
      version: 3,
      status: "active",
      activated_at: "2026-03-02T02:00:00Z",
    });
    vi.mocked(api.listRuleSets).mockResolvedValue({
      items: [
        { ...DRAFT, status: "active", activated_at: "2026-03-02T02:00:00Z" },
        { ...ACTIVE, status: "superseded" },
        SUPERSEDED,
      ],
    });

    await user.click(screen.getByRole("button", { name: /Validate and activate/ }));
    await user.click(screen.getByRole("button", { name: "Activate version 3" }));
    await screen.findByRole("button", { name: /Roll back to version 2/ });

    releasePeriodic();
    await act(async () => {});

    expect(screen.getByRole("button", { name: /Roll back to version 2/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Validate and activate/ })).not.toBeInTheDocument();
  });

  it("reconciles the inventory from the lifecycle response when the refresh that follows fails", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();

    vi.mocked(api.activateRuleSet).mockResolvedValue({
      ...ACTIVE_DETAIL,
      rule_set_id: DRAFT.rule_set_id,
      version: 3,
      status: "active",
      activated_at: "2026-03-02T02:00:00Z",
    });
    vi.mocked(api.listRuleSets).mockRejectedValue(
      new ApiError(503, "SERVICE_UNAVAILABLE", "The rule set service is not reachable."),
    );

    await user.click(screen.getByRole("button", { name: /Validate and activate/ }));
    await user.click(screen.getByRole("button", { name: "Activate version 3" }));
    await detailPanel().findByRole("heading", { name: "Version 3", level: 2 });

    // The transition succeeded, so the listed statuses follow it even though the
    // re-read did not answer: version 3 is active and version 2 is superseded.
    // Neither row may still offer the action its old status allowed.
    expect(screen.queryByRole("button", { name: /Validate and activate/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Roll back to version 2/ })).toBeInTheDocument();
    // The failed refresh is still reported rather than passing unnoticed.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The rule set service is not reachable.",
    );
  });

  it("cannot be dismissed while the transition it confirmed is still in flight", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();

    let releaseActivate: () => void = () => {};
    vi.mocked(api.activateRuleSet).mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseActivate = () =>
            resolve({
              ...ACTIVE_DETAIL,
              rule_set_id: DRAFT.rule_set_id,
              version: 3,
              status: "active",
              activated_at: "2026-03-02T02:00:00Z",
            });
        }),
    );

    await user.click(screen.getByRole("button", { name: /Validate and activate/ }));
    await user.click(screen.getByRole("button", { name: "Activate version 3" }));

    // Dismissing would put the inventory back within reach, and a version
    // inspected there would then be replaced by this response.
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(inventory().queryByRole("button", { name: /Inspect version 1/ })).toBeInTheDocument();

    releaseActivate();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await detailPanel().findByRole("heading", { name: "Version 3", level: 2 })).toBeInTheDocument();
  });

  it("leaves the detail panel readable when a lifecycle response overtakes a detail read", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();

    // An inspection is still in flight when the transition answers. The
    // lifecycle response supersedes it, so that read returns without touching
    // the panel and cannot clear the loading state it set.
    let releaseDetail: () => void = () => {};
    vi.mocked(api.getRuleSet).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseDetail = () =>
            resolve({ ...ACTIVE_DETAIL, rule_set_id: SUPERSEDED.rule_set_id, version: 1, status: "superseded" });
        }),
    );
    await user.click(inventory().getByRole("button", { name: /Inspect version 1/ }));
    expect(screen.getByText("Loading version detail...")).toBeInTheDocument();

    vi.mocked(api.activateRuleSet).mockResolvedValue({
      ...ACTIVE_DETAIL,
      rule_set_id: DRAFT.rule_set_id,
      version: 3,
      status: "active",
      activated_at: "2026-03-02T02:00:00Z",
    });

    await user.click(screen.getByRole("button", { name: /Validate and activate/ }));
    await user.click(screen.getByRole("button", { name: "Activate version 3" }));

    expect(await detailPanel().findByRole("heading", { name: "Version 3", level: 2 })).toBeInTheDocument();
    expect(screen.queryByText("Loading version detail...")).not.toBeInTheDocument();

    releaseDetail();
    await act(async () => {});
    expect(detailPanel().getByRole("heading", { name: "Version 3", level: 2 })).toBeInTheDocument();
    expect(screen.queryByText("Loading version detail...")).not.toBeInTheDocument();
  });
});

describe("cross-session lifecycle changes", () => {
  async function tick(ms = 0) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  it("refreshes the inspected version when another administrator changes its status", async () => {
    // The inventory sync runs on a minute timer, so this test drives the clock
    // itself rather than waiting on a findBy* query.
    vi.useFakeTimers();
    renderPage();
    await tick();

    expect(detailPanel().getByRole("heading", { name: "Version 2", level: 2 })).toBeInTheDocument();
    const readsOnOpen = vi.mocked(api.getRuleSet).mock.calls.length;

    // Nothing changed server-side: the panel is left alone rather than re-read
    // on every tick.
    await tick(60_000);
    expect(vi.mocked(api.getRuleSet).mock.calls).toHaveLength(readsOnOpen);

    // Another administrator activates the draft, which supersedes the version
    // being inspected here.
    vi.mocked(api.listRuleSets).mockResolvedValue({
      items: [
        { ...DRAFT, status: "active", activated_at: "2026-03-02T02:00:00Z" },
        { ...ACTIVE, status: "superseded" },
        SUPERSEDED,
      ],
    });
    vi.mocked(api.getRuleSet).mockResolvedValue({ ...ACTIVE_DETAIL, status: "superseded" });

    await tick(60_000);

    expect(api.getRuleSet).toHaveBeenLastCalledWith("rs-active");
    expect(detailPanel().getByRole("heading", { name: "Version 2", level: 2 })).toBeInTheDocument();
    expect(detailPanel().getByText("Superseded")).toBeInTheDocument();
  });

  it("supersedes an in-flight detail read with the latest cross-session state", async () => {
    vi.useFakeTimers();
    renderPage();
    await tick();

    let releaseStaleDetail: () => void = () => {};
    vi.mocked(api.getRuleSet)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            releaseStaleDetail = () =>
              resolve({
                ...ACTIVE_DETAIL,
                rule_set_id: SUPERSEDED.rule_set_id,
                version: 1,
                status: "superseded",
              });
          }),
      )
      .mockResolvedValue({
        ...ACTIVE_DETAIL,
        rule_set_id: SUPERSEDED.rule_set_id,
        version: 1,
        status: "active",
        activated_at: "2026-03-02T02:00:00Z",
      });

    fireEvent.click(inventory().getByRole("button", { name: /Inspect version 1/ }));
    vi.mocked(api.listRuleSets).mockResolvedValue({
      items: [
        DRAFT,
        { ...ACTIVE, status: "superseded" },
        { ...SUPERSEDED, status: "active", activated_at: "2026-03-02T02:00:00Z" },
      ],
    });

    await tick(60_000);

    expect(api.getRuleSet).toHaveBeenLastCalledWith("rs-old");
    expect(detailPanel().getByRole("heading", { name: "Version 1", level: 2 })).toBeInTheDocument();
    expect(detailPanel().getByText("Active")).toBeInTheDocument();

    releaseStaleDetail();
    await tick();
    expect(detailPanel().getByText("Active")).toBeInTheDocument();
  });
});

describe("extracted rule review", () => {
  function rulesPanel() {
    return within(screen.getByRole("region", { name: "Extracted rules" }));
  }

  it("lists the active version's rules with their metrics before any lifecycle action", async () => {
    vi.mocked(api.listRuleSetRules).mockResolvedValue({
      pattern_type: "teacher_subject",
      total: 1,
      limit: 25,
      offset: 0,
      items: [rule(1, { antecedent: ["Dr. Reyes"], consequent: ["CS101"], guidance_score: "0.9100" })],
    });

    renderPage();
    await waitForActiveVersion();

    expect(await rulesPanel().findByText("Dr. Reyes")).toBeInTheDocument();
    expect(rulesPanel().getByText("CS101")).toBeInTheDocument();
    expect(rulesPanel().getByText("0.9100")).toBeInTheDocument();
    expect(rulesPanel().getByText("Showing 1–1 of 1")).toBeInTheDocument();
    expect(api.listRuleSetRules).toHaveBeenCalledWith("rs-active", {
      patternType: "teacher_subject",
      order: "desc",
      limit: 25,
      offset: 0,
    });
  });

  it("explains each metric with the stream's acceptable values and marks each score acceptable or not", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listRuleSetRules).mockResolvedValue({
      pattern_type: "teacher_subject",
      total: 1,
      limit: 25,
      offset: 0,
      items: [rule(1, { support: "0.0060", confidence: "0.7500", lift: "2.4000", term_recurrence: 2, guidance_score: "0.5000" })],
    });

    renderPage();
    await waitForActiveVersion();

    const guide = within(await rulesPanel().findByRole("table", { name: /Acceptable values and interpretation.*Teacher \/ Subject/ }));
    expect(within(guide.getByRole("rowheader", { name: "Support" }).closest("tr")!).getByText("≥ 0.10")).toBeInTheDocument();
    expect(within(guide.getByRole("rowheader", { name: "Confidence" }).closest("tr")!).getByText("≥ 0.60")).toBeInTheDocument();
    expect(within(guide.getByRole("rowheader", { name: "Lift" }).closest("tr")!).getByText("≥ 1.05")).toBeInTheDocument();
    expect(within(guide.getByRole("rowheader", { name: "Terms" }).closest("tr")!).getByText("≥ 2")).toBeInTheDocument();
    expect(within(guide.getByRole("rowheader", { name: "Guidance score" }).closest("tr")!).getByText("Higher guidance score means higher priority in GA")).toBeInTheDocument();
    expect(guide.getByText(/A value above 1 means the pattern is specific to that teacher/)).toBeInTheDocument();
    expect(guide.getByText(/A higher value means the pattern is more reliable for that teacher/)).toBeInTheDocument();

    const support = (await rulesPanel().findByText("0.0060")).closest("td")!;
    expect(support).toHaveTextContent("(below acceptable)");
    expect(support).toHaveClass("text-error");
    const confidence = rulesPanel().getByText("0.7500").closest("td")!;
    expect(confidence).toHaveTextContent("(acceptable)");
    expect(confidence).toHaveClass("text-emerald-700");
    // A low guidance score is still used by the GA, so it is never marked.
    const guidance = rulesPanel().getByText("0.5000").closest("td")!;
    expect(guidance).not.toHaveTextContent("acceptable");
    expect(guidance).not.toHaveClass("text-error");
    expect(rulesPanel().queryByText(/of records/)).not.toBeInTheDocument();

    await user.click(rulesPanel().getByRole("button", { name: "Teacher / Day" }));
    const dayGuide = within(await rulesPanel().findByRole("table", { name: /Teacher \/ Day stream/ }));
    expect(within(dayGuide.getByRole("rowheader", { name: "Support" }).closest("tr")!).getByText("≥ 0.15")).toBeInTheDocument();
  });

  it("says when a stream retained no rules", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForActiveVersion();

    await user.click(rulesPanel().getByRole("button", { name: "Teacher / Time" }));

    await waitFor(() =>
      expect(api.listRuleSetRules).toHaveBeenLastCalledWith("rs-active", expect.objectContaining({ patternType: "teacher_time" })),
    );
    expect(await rulesPanel().findByText("This stream retained no rules in this version.")).toBeInTheDocument();
  });

  it("pages through a stream and re-sorts by guidance score from the first page", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listRuleSetRules).mockImplementation(async (_id, query) => ({
      pattern_type: query.patternType,
      total: 30,
      limit: query.limit,
      offset: query.offset,
      items: [rule(query.offset + 1)],
    }));

    renderPage();
    await waitForActiveVersion();
    expect(await rulesPanel().findByText("Showing 1–25 of 30")).toBeInTheDocument();
    expect(rulesPanel().getByRole("button", { name: "Previous" })).toBeDisabled();

    await user.click(rulesPanel().getByRole("button", { name: "Next" }));
    expect(await rulesPanel().findByText("Showing 26–30 of 30")).toBeInTheDocument();
    expect(rulesPanel().getByRole("button", { name: "Next" })).toBeDisabled();

    await user.click(rulesPanel().getByRole("button", { name: /Guidance score/ }));
    await waitFor(() =>
      expect(api.listRuleSetRules).toHaveBeenLastCalledWith("rs-active", expect.objectContaining({ order: "asc", offset: 0 })),
    );
    expect(await rulesPanel().findByText("Showing 1–25 of 30")).toBeInTheDocument();
    expect(rulesPanel().getByRole("columnheader", { name: /Guidance score/ })).toHaveAttribute("aria-sort", "ascending");
  });

  it("offers no way to change a rule", async () => {
    vi.mocked(api.listRuleSetRules).mockResolvedValue({
      pattern_type: "teacher_subject",
      total: 1,
      limit: 25,
      offset: 0,
      items: [rule(1)],
    });
    renderPage();
    await waitForActiveVersion();
    await rulesPanel().findByText("Faculty 1");

    expect(rulesPanel().queryByRole("textbox")).not.toBeInTheDocument();
    expect(rulesPanel().queryByRole("button", { name: /edit|delete|remove/i })).not.toBeInTheDocument();
  });
});

describe("extracted rule review loading and recovery", () => {
  function rulesPanel() {
    return within(screen.getByRole("region", { name: "Extracted rules" }));
  }

  function pageOf(patternType: RuleSetRule["pattern_type"], label: string, total = 1) {
    return { pattern_type: patternType, total, limit: 25, offset: 0, items: [rule(1, { pattern_type: patternType, antecedent: [label] })] };
  }

  it("never shows the previous query's rows under a newly selected stream", async () => {
    const user = userEvent.setup();
    let releaseDay: () => void = () => {};
    vi.mocked(api.listRuleSetRules).mockImplementation((_id, query) =>
      query.patternType === "teacher_day"
        ? new Promise((resolve) => { releaseDay = () => resolve(pageOf("teacher_day", "Day rule")); })
        : Promise.resolve(pageOf("teacher_subject", "Subject rule")),
    );
    renderPage();
    await waitForActiveVersion();
    await rulesPanel().findByText("Subject rule");

    await user.click(rulesPanel().getByRole("button", { name: "Teacher / Day" }));

    expect(rulesPanel().queryByText("Subject rule")).not.toBeInTheDocument();
    expect(rulesPanel().queryByText(/Showing/)).not.toBeInTheDocument();
    expect(rulesPanel().getByText("Loading rules...")).toBeInTheDocument();
    await act(async () => releaseDay());
    expect(await rulesPanel().findByText("Day rule")).toBeInTheDocument();
  });

  it("retries a failed rule read for the same stream, sort and page", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listRuleSetRules)
      .mockRejectedValueOnce(new ApiError(503, "UNAVAILABLE", "The local server is unavailable."))
      .mockResolvedValue(pageOf("teacher_subject", "Recovered rule"));
    renderPage();
    await waitForActiveVersion();

    expect(await rulesPanel().findByRole("alert")).toHaveTextContent("The local server is unavailable.");
    await user.click(rulesPanel().getByRole("button", { name: "Retry" }));

    expect(await rulesPanel().findByText("Recovered rule")).toBeInTheDocument();
    expect(rulesPanel().queryByRole("alert")).not.toBeInTheDocument();
    expect(vi.mocked(api.listRuleSetRules).mock.calls.map(([, query]) => query)).toEqual([
      { patternType: "teacher_subject", order: "desc", limit: 25, offset: 0 },
      { patternType: "teacher_subject", order: "desc", limit: 25, offset: 0 },
    ]);
  });

  it("keeps the review position when the same version refreshes from another session", async () => {
    vi.useFakeTimers();
    vi.mocked(api.listRuleSetRules).mockImplementation(async (_id, query) => ({
      pattern_type: query.patternType,
      total: 30,
      limit: query.limit,
      offset: query.offset,
      items: [rule(query.offset + 1, { pattern_type: query.patternType })],
    }));
    renderPage();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fireEvent.click(rulesPanel().getByRole("button", { name: "Teacher / Day" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fireEvent.click(rulesPanel().getByRole("button", { name: "Next" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(rulesPanel().getByText("Showing 26–30 of 30")).toBeInTheDocument();

    vi.mocked(api.listRuleSets).mockResolvedValue({
      items: [{ ...DRAFT, status: "active", activated_at: "2026-03-02T02:00:00Z" }, { ...ACTIVE, status: "superseded" }, SUPERSEDED],
    });
    let releaseDetail: () => void = () => {};
    vi.mocked(api.getRuleSet).mockReturnValue(
      new Promise((resolve) => { releaseDetail = () => resolve({ ...ACTIVE_DETAIL, status: "superseded" }); }),
    );
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    // The refresh is still in flight here; the panel must not reset meanwhile.
    expect(rulesPanel().getByRole("button", { name: "Teacher / Day" })).toHaveAttribute("aria-pressed", "true");
    await act(async () => releaseDetail());
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });

    expect(detailPanel().getByText("Superseded")).toBeInTheDocument();
    expect(rulesPanel().getByRole("button", { name: "Teacher / Day" })).toHaveAttribute("aria-pressed", "true");
    expect(rulesPanel().getByText("Showing 26–30 of 30")).toBeInTheDocument();
  });
});

it("shows a retryable load error instead of the no-rule-sets message", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listRuleSets).mockReset();
  vi.mocked(api.listRuleSets).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load rule set versions."));
  vi.mocked(api.listRuleSets).mockResolvedValue({ items: [ACTIVE] });
  render(<MemoryRouter><RuleSetsPage /></MemoryRouter>);

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load rule set versions.");
  expect(screen.queryByText(/No rule sets have been mined yet/)).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("list", { name: "Rule set versions" })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("lands on the active version after a retry succeeds", async () => {
  vi.mocked(api.listRuleSets).mockReset();
  vi.mocked(api.listRuleSets).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load rule set versions."));
  vi.mocked(api.listRuleSets).mockResolvedValue({ items: [DRAFT, ACTIVE, SUPERSEDED] });
  const user = userEvent.setup();
  render(<MemoryRouter><RuleSetsPage /></MemoryRouter>);
  await screen.findByRole("alert");
  expect(api.getRuleSet).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(api.getRuleSet).toHaveBeenCalledWith(ACTIVE.rule_set_id));
});


describe("teacher coverage", () => {
  function coveragePanel() {
    return within(screen.getByRole("region", { name: "Teacher coverage" }));
  }

  // Teacher names also label the relationship diagram, so row lookups stay in the table.
  function coverageTable() {
    return within(coveragePanel().getByRole("table"));
  }

  async function coverageTableAsync() {
    return within(await coveragePanel().findByRole("table"));
  }

  it("folds into a summary of the stream's coverage and rule counts", async () => {
    render(
      <MemoryRouter>
        <RuleSetsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("region", { name: "Teacher coverage" });
    const summary = (await coveragePanel().findByText("1 with a rule")).closest("summary");
    expect(summary).toHaveTextContent("Teacher / Subject: 3 teachers");
    expect(summary?.parentElement).not.toHaveAttribute("open");

    const rulesSummary = within(screen.getByRole("region", { name: "Extracted rules" })).getByText(/rules mined/).closest("summary");
    expect(rulesSummary).not.toBeNull();
  });

  it("draws the teacher-to-pattern links and the confidence/lift scatter", async () => {
    render(
      <MemoryRouter>
        <RuleSetsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("region", { name: "Teacher coverage" });
    expect(await coveragePanel().findByRole("img", { name: /Links from each teacher/ })).toBeInTheDocument();
    expect(coveragePanel().getByRole("img", { name: /confidence against lift/ })).toBeInTheDocument();
  });

  it("highlights an inactive teacher in red", async () => {
    vi.mocked(api.getRuleSetFacultyCoverage).mockResolvedValue({
      ...COVERAGE,
      items: [...COVERAGE.items, coverageItem({ source_faculty_key: "d", faculty_name: "Retired Teacher", is_active: false })],
    });
    render(
      <MemoryRouter>
        <RuleSetsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("region", { name: "Teacher coverage" });
    const name = await (await coverageTableAsync()).findByRole("rowheader", { name: /Retired Teacher/ });
    expect(name).toHaveClass("text-error");
    expect(name.closest("tr")).toHaveClass("bg-error/10");
    expect(within(name).getByText("Inactive")).toHaveClass("bg-error");
    expect(coverageTable().getByRole("rowheader", { name: /Covered Teacher/ })).not.toHaveClass("text-error");
  });

  function cell(teacher: string, column: number) {
    const row = coverageTable().getByText(teacher).closest("tr");
    if (!row) throw new Error(`no row for ${teacher}`);
    return row.querySelectorAll("td")[column];
  }

  it("lists every teacher the same way, colouring each metric by its threshold", async () => {
    render(
      <MemoryRouter>
        <RuleSetsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("region", { name: "Teacher coverage" });
    expect(await (await coverageTableAsync()).findByText("Covered Teacher")).toBeInTheDocument();
    expect(coverageTable().getByText("Rare Teacher")).toBeInTheDocument();
    expect(coverageTable().getByText("Former Teacher")).toBeInTheDocument();
    expect(coveragePanel().getByText("1 with a rule")).toBeInTheDocument();
    expect(coveragePanel().getByText("2 without")).toBeInTheDocument();
    expect(coveragePanel().getByText(/2 teachers have a rule in at least one stream/)).toBeInTheDocument();
    expect(coveragePanel().getByText("Not linked to the faculty roster")).toBeInTheDocument();
    expect(api.getRuleSetFacultyCoverage).toHaveBeenCalledWith(ACTIVE_DETAIL.rule_set_id, "teacher_subject");

    // Columns after the teacher header: pattern, classes, support, confidence, lift, terms, result.
    expect(cell("Covered Teacher", 2)).toHaveClass("text-emerald-700");
    expect(cell("Rare Teacher", 2)).toHaveClass("text-error");
    expect(cell("Rare Teacher", 2)).toHaveTextContent("0.0016");
    expect(cell("Rare Teacher", 3)).toHaveClass("text-emerald-700");
    expect(cell("Rare Teacher", 5)).toHaveClass("text-error");
    expect(cell("Former Teacher", 4)).toHaveClass("text-error");
    expect(cell("Covered Teacher", 6)).toHaveTextContent("Has rule");
    expect(cell("Rare Teacher", 6)).toHaveTextContent("No rule");
  });

  it("loads the selected stream", async () => {
    render(
      <MemoryRouter>
        <RuleSetsPage />
      </MemoryRouter>,
    );
    await screen.findByRole("region", { name: "Teacher coverage" });
    await (await coverageTableAsync()).findByText("Covered Teacher");

    fireEvent.click(
      within(coveragePanel().getByRole("group", { name: "Coverage stream" })).getByRole("button", {
        name: "Teacher / Day",
      }),
    );

    await waitFor(() =>
      expect(api.getRuleSetFacultyCoverage).toHaveBeenCalledWith(ACTIVE_DETAIL.rule_set_id, "teacher_day"),
    );
  });

  it("says when a version predates per-teacher metrics", async () => {
    vi.mocked(api.getRuleSetFacultyCoverage).mockResolvedValue({ ...COVERAGE, metrics_recorded: false });
    render(
      <MemoryRouter>
        <RuleSetsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("region", { name: "Teacher coverage" });
    expect(await coveragePanel().findByText(/created before per-teacher metrics were recorded/)).toBeInTheDocument();
  });
});

describe("rule wording", () => {
  it("hides the import artifact hash from the notes", async () => {
    vi.mocked(api.getRuleSet).mockResolvedValue({
      ...ACTIVE_DETAIL,
      notes: `CHR-100 / CHR-COLAB-004 offline import; artifact sha256=${"a".repeat(64)}`,
    });
    render(
      <MemoryRouter>
        <RuleSetsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("region", { name: "Version detail" });
    expect(await detailPanel().findByText("CHR-100 / CHR-COLAB-004 offline import")).toBeInTheDocument();
    expect(screen.queryByText(/sha256/)).not.toBeInTheDocument();
  });

  it("names the rule columns for what they hold instead of If and Then", async () => {
    vi.mocked(api.listRuleSetRules).mockResolvedValue({
      pattern_type: "teacher_subject",
      total: 1,
      limit: 25,
      offset: 0,
      items: [
        {
          rule_id: "r1",
          pattern_type: "teacher_subject",
          faculty_id: "f-covered",
          antecedent: ["Covered Teacher"],
          consequent: ["CS 1101"],
          support: "0.0100",
          confidence: "0.5000",
          lift: "3.0000",
          term_recurrence: 4,
          guidance_score: "0.4000",
        },
      ],
    });
    render(
      <MemoryRouter>
        <RuleSetsPage />
      </MemoryRouter>,
    );

    const rules = within(await screen.findByRole("region", { name: "Extracted rules" }));
    expect(await rules.findByRole("columnheader", { name: "Subject usually taught" })).toBeInTheDocument();
    expect(rules.queryByRole("columnheader", { name: "If" })).not.toBeInTheDocument();
    expect(rules.queryByRole("columnheader", { name: "Then" })).not.toBeInTheDocument();
  });
});
