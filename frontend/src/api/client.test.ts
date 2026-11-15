import { afterEach, describe, expect, it, vi } from "vitest";
import { subscribeTermChanged, subscribeUnauthorized } from "../state/appEvents";
import {
  FULL_TIME_MAX_WORKLOAD,
  PART_TIME_MAX_WORKLOAD,
  PATTERN_TYPES,
  ROOM_TYPES,
  ROOM_TYPE_LABEL,
  TERMINAL_JOB_STATES,
  api,
  conflictLabel,
  deriveRoomCode,
  formatMeetingDays,
  isEmptyRuleOutcome,
  isTerminalJobState,
  maxWorkloadFor,
  normalizeRoomNumber,
  parseMeetingDays,
} from "./client";

describe("central API client", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uses cookie credentials and JSON for login", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      user_id: "user-1",
      username: "operator",
      role: "scheduling_committee",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await api.login("operator", "secret");

    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v1/auth/login"), expect.objectContaining({
      method: "POST",
      credentials: "include",
      body: JSON.stringify({ username: "operator", password: "secret" }),
    }));
  });

  it("handles empty 204 responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.logout()).resolves.toBeUndefined();
  });

  it("binds an availability save, including an empty selection, to the displayed term", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", {
      status: 200, headers: { "Content-Type": "application/json" },
    }));

    await api.updateAvailability("faculty-1", "term-1", []);

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/faculty/faculty-1/availability"),
      expect.objectContaining({
        method: "PUT", credentials: "include",
        body: JSON.stringify({ term_id: "term-1", time_slot_ids: [] }),
      }),
    );
  });

  it("downloads a schedule export with the backend filename", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("a,b\n", {
      status: 200,
      headers: {
        "Content-Type": "text/csv",
        "Content-Disposition": 'attachment; filename="chronos_2025-2026_term1_20261113-1430.csv"',
      },
    }));

    const file = await api.exportSchedule("s1", "csv");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/schedules/s1/export?format=csv"),
      expect.objectContaining({ credentials: "include" }),
    );
    expect(file.filename).toBe("chronos_2025-2026_term1_20261113-1430.csv");
    expect(await file.blob.text()).toBe("a,b\n");
  });

  it("passes the timetable filters through to export", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("a,b\n", { status: 200 }));

    await api.exportSchedule("s1", "xlsx", { blockId: "block-1", roomCode: "LB 441" });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/schedules/s1/export?format=xlsx&block_id=block-1&room_code=LB+441"),
      expect.anything(),
    );
  });

  it("names an export after the backend convention when the filename header is hidden", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("xlsx-bytes", { status: 200 }));

    const file = await api.exportSchedule("s2", "xlsx");

    expect(file.filename).toBe("chronos_s2.xlsx");
  });

  it.each([
    ["csv", { blockId: "block-1" }, "chronos_s2_block_block-1.csv"],
    ["xlsx", { blockId: "block-1" }, "chronos_s2_block_block-1.xlsx"],
    ["csv", { roomCode: "LB 441" }, "chronos_s2_room_LB_441.csv"],
    ["xlsx", { roomCode: "LB 441" }, "chronos_s2_room_LB_441.xlsx"],
    ["csv", { blockId: "block-1", roomCode: "LB 441" }, "chronos_s2_block_block-1_room_LB_441.csv"],
  ] as const)("keeps the filter scope in a %s filename when the header is hidden (%o)", async (format, filters, expected) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("bytes", { status: 200 }));

    const file = await api.exportSchedule("s2", format, filters);

    expect(file.filename).toBe(expected);
  });

  it("raises the backend refusal when a schedule cannot be exported", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      code: "SCHEDULE_NOT_FINALIZED",
      message: "The schedule must be finalized before export.",
      details: {},
      correlation_id: "request-2",
    }), { status: 409, headers: { "Content-Type": "application/json" } }));

    await expect(api.exportSchedule("s3", "csv")).rejects.toMatchObject({
      status: 409,
      code: "SCHEDULE_NOT_FINALIZED",
      message: "The schedule must be finalized before export.",
    });
  });

  it("preserves the backend error envelope", async () => {
    const unauthorized = vi.fn();
    const unsubscribe = subscribeUnauthorized(unauthorized);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      code: "INVALID_CREDENTIALS",
      message: "Invalid username or password.",
      details: {},
      correlation_id: "request-1",
    }), { status: 401, headers: { "Content-Type": "application/json" } }));

    await expect(api.login("operator", "wrong")).rejects.toMatchObject({
      status: 401,
      code: "INVALID_CREDENTIALS",
      correlationId: "request-1",
    });
    expect(unauthorized).toHaveBeenCalledOnce();
    unsubscribe();
  });

  it("creates an offering from Subject identity without a units value", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      offering_id: "offering-1",
      term_id: "term-1",
      subject_code: "CS101",
      group_no: 1,
      group_label: "CS101 G1",
    }), { status: 201, headers: { "Content-Type": "application/json" } }));

    await expect(api.createOffering("term-1", "CS101")).resolves.toMatchObject({ group_label: "CS101 G1" });

    const body = String(vi.mocked(fetchMock).mock.calls[0][1]?.body);
    expect(JSON.parse(body)).toEqual({ term_id: "term-1", subject_code: "CS101" });
    expect(body).not.toContain("units");
  });

  it("scopes an offering listing to the requested term", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await api.listOfferings("term 1/2");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/subject-offerings?term_id=term%201%2F2"),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("leaves term-change announcements to the caller", async () => {
    const termChanged = vi.fn();
    const unsubscribe = subscribeTermChanged(termChanged);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      term_id: "term-1",
      academic_year: "2026-2027",
      term_number: 1,
      is_active: true,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await api.selectTerm("term-1");

    expect(termChanged).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("derives the approved workload cap from the classification", () => {
    expect(maxWorkloadFor(false)).toBe(PART_TIME_MAX_WORKLOAD);
    expect(maxWorkloadFor(true)).toBe(FULL_TIME_MAX_WORKLOAD);
    expect([PART_TIME_MAX_WORKLOAD, FULL_TIME_MAX_WORKLOAD]).toEqual([17, 24]);
  });

  it("sends the faculty classification instead of a workload figure", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({
      faculty_id: "faculty-1",
      full_name: "A. Rivera",
      is_full_time: true,
      availability_mask: "00",
    }), { status: 201, headers: { "Content-Type": "application/json" } }));

    await api.createFaculty({ full_name: "A. Rivera", is_full_time: true, is_active: true });
    await api.updateFaculty("faculty-1", { full_name: "A. Rivera", is_full_time: false, is_active: true });

    expect(fetchMock).toHaveBeenNthCalledWith(1, expect.stringContaining("/api/v1/faculty"), expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ full_name: "A. Rivera", is_full_time: true, is_active: true }),
    }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, expect.stringContaining("/api/v1/faculty/faculty-1"), expect.objectContaining({
      method: "PATCH",
      body: JSON.stringify({ full_name: "A. Rivera", is_full_time: false, is_active: true }),
    }));
  });

  it.each([
    ["LB", "483", "LB483"],
    ["lb", " 4 8 3 ", "LB483"],
    ["LB", "", ""],
    ["", "483", ""],
    // The building code typed into the number field is not repeated.
    ["LB", "LB486", "LB486"],
    ["LB", "lb 486", "LB486"],
    ["LB", "LB-486", "LB486"],
    ["LB", "LB", "LBLB"],
    ["LB", "LBA", "LBLBA"],
    ["S", "S2", "S2"],
    ["LB", "486LB", "LB486LB"],
  ])("derives the room code for %s + %s", (buildingCode, roomNumber, expected) => {
    expect(deriveRoomCode(buildingCode, roomNumber)).toBe(expected);
  });

  it("normalizes a room number to one uppercase token", () => {
    expect(normalizeRoomNumber("  4 8 3b ")).toBe("483B");
  });

  it("labels every room type with its canonical LAB or LEC value", () => {
    expect(ROOM_TYPES.map((type) => ROOM_TYPE_LABEL[type])).toEqual(["LEC", "LAB"]);
  });

  it("posts a room without a capacity of its own", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      room_code: "LB 483",
      building_name: "Library",
      capacity: 40,
      room_type: "lecture",
      expanded: false,
    }), { status: 201, headers: { "Content-Type": "application/json" } }));

    await api.createRoom({
      room_code: "LB 483",
      building_name: "Library",
      room_type: "lecture",
      expanded: false,
    });

    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v1/rooms"), expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        room_code: "LB 483",
        building_name: "Library",
        room_type: "lecture",
        expanded: false,
      }),
    }));
  });

  it("generates canonical slots for the requested term", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await api.generateCanonicalSlots("term-1");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/time-slots/generate?term_id=term-1"),
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });

  it("submits a selected Block GA job against a base schedule", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      job_id: "job-1",
      kind: "ga",
      status: "queued",
    }), { status: 202, headers: { "Content-Type": "application/json" } }));
    const payload = {
      term_id: "term-1",
      block_id: "block-1",
      base_schedule_id: "schedule-1",
    };

    await api.submitGAJob(payload);

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/jobs/ga"),
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify(payload),
      }),
    );
  });

  it("patches only the assignment fields an edit changed", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      schedule: {
        schedule_id: "master-1",
        term_id: "term-1",
        schedule_type: "master",
        created_at: "2026-09-01T00:00:00Z",
        finalized_at: null,
        assignments: [],
      },
      validation: {
        required_class_count: 1,
        assigned_class_count: 1,
        completeness_percentage: 100,
        unassigned_class_ids: [],
        hard_conflict_count: 0,
        conflicts: [],
        export_allowed: true,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    const result = await api.updateAssignment("master-1", "record-1", { room_code: "LB 484", meeting_days: "MW" });

    expect(result.validation.export_allowed).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/schedules/master-1/assignments/record-1"),
      expect.objectContaining({
        method: "PATCH",
        credentials: "include",
        body: JSON.stringify({ room_code: "LB 484", meeting_days: "MW" }),
      }),
    );
  });

  it("polls the durable status of a GA job", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      job_id: "job-1",
      kind: "ga",
      status: "running",
      submitted_at: "2026-09-08T00:00:00Z",
      started_at: "2026-09-08T00:00:01Z",
      completed_at: null,
      result: null,
      error: null,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(api.getGAJob("job-1")).resolves.toMatchObject({ status: "running" });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/jobs/job-1"),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("asks the server to validate a schedule rather than judging it here", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      required_class_count: 2,
      assigned_class_count: 1,
      completeness_percentage: 50,
      unassigned_class_ids: ["class-2"],
      hard_conflict_count: 0,
      conflicts: [],
      export_allowed: false,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(api.validateSchedule("master-1")).resolves.toMatchObject({ export_allowed: false });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/schedules/master-1/validate"),
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });

  it("finalizes and captures history through the server-owned lifecycle endpoints", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({
      snapshot_id: "snapshot-1",
      source_term_id: "term-1",
      source_schedule_id: "master-1",
      approved_by_user_id: "admin-1",
      academic_year: "2026-2027",
      term_number: 1,
      captured_at: "2026-10-09T00:00:00Z",
      record_count: 12,
    }), { status: 201, headers: { "Content-Type": "application/json" } }));

    await api.finalizeSchedule("master-1");
    await api.captureHistoricalSnapshot("term-1", {
      schedule_id: "master-1",
      confirm_completed_and_approved: true,
    });

    expect(fetchMock).toHaveBeenNthCalledWith(1, expect.stringContaining("/api/v1/schedules/master-1/finalize"), expect.objectContaining({ method: "POST", credentials: "include" }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, expect.stringContaining("/api/v1/terms/term-1/historical-snapshot"), expect.objectContaining({
      method: "POST",
      credentials: "include",
      body: JSON.stringify({ schedule_id: "master-1", confirm_completed_and_approved: true }),
    }));
  });

  it.each([
    ["MWF", ["M", "W", "F"]],
    ["TTh", ["T", "Th"]],
    ["ThSa", ["Th", "Sa"]],
    ["", []],
    ["MX", []],
  ])("reads the days out of the %s pattern", (pattern, expected) => {
    expect(parseMeetingDays(pattern)).toEqual(expected);
  });

  it("spells one set of days as one canonical pattern", () => {
    expect(formatMeetingDays(["F", "M", "W"])).toBe("MWF");
    expect(formatMeetingDays(["Th", "T", "Th"])).toBe("TTh");
    expect(parseMeetingDays(formatMeetingDays(["Sa", "M"]))).toEqual(["M", "Sa"]);
  });

  it("names a known conflict code and passes an unknown one through", () => {
    expect(conflictLabel("ROOM_OVERLAP")).toBe("Room double-booked");
    expect(conflictLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
  it("calls preview block replacement endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          schedule_id: "sched-1",
          block_id: "block-1",
          preview_token: "token.sig",
          proposed_assignments: [],
          changed_values: [],
          validation: {
            required_class_count: 1,
            assigned_class_count: 1,
            completeness_percentage: 100,
            unassigned_class_ids: [],
            hard_conflict_count: 0,
            conflicts: [],
            export_allowed: true,
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const payload = { assignments: [] };
    const res = await api.previewBlockReplacement("sched-1", "block-1", payload);

    expect(res.preview_token).toBe("token.sig");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/schedules/sched-1/blocks/block-1/replacement/preview"),
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify(payload),
      }),
    );
  });

  it("calls confirm block replacement endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          schedule: {
            schedule_id: "sched-1",
            term_id: "term-1",
            schedule_type: "routine",
            created_at: "2026-09-07T00:00:00Z",
            finalized_at: null,
            assignments: [],
          },
          validation: {
            required_class_count: 1,
            assigned_class_count: 1,
            completeness_percentage: 100,
            unassigned_class_ids: [],
            hard_conflict_count: 0,
            conflicts: [],
            export_allowed: true,
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const payload = { preview_token: "token.sig" };
    const res = await api.confirmBlockReplacement("sched-1", "block-1", payload);

    expect(res.schedule.schedule_id).toBe("sched-1");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/schedules/sched-1/blocks/block-1/replacement/confirm"),
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify(payload),
      }),
    );
  });

  it("lists schedules filtered by term and type", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.listSchedules("term-1", "master");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/schedules?term_id=term-1&schedule_type=master"),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("retrieves a schedule by id", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ schedule_id: "sched-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.getSchedule("sched-1");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/schedules/sched-1"),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("fetches timetable projection with optional block and room filters", async () => {
    const mockProjection = {
      schedule_id: "sched-1",
      term_id: "term-1",
      schedule_type: "routine",
      created_at: "2026-09-07T00:00:00Z",
      finalized_at: null,
      finalization_state: "candidate",
      calendar: {
        teaching_days: ["MON", "TUE", "WED", "THU", "FRI", "SAT"],
        opening_time: "07:30:00",
        closing_time: "21:00:00",
        interval_minutes: 30,
      },
      assignments: [],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(mockProjection), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await api.getTimetableProjection("sched-1", "term-1", {
      blockId: "block-1",
      roomCode: "LB441",
    });

    expect(result.schedule_id).toBe("sched-1");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/v1/schedules/sched-1/timetable?term_id=term-1&block_id=block-1&room_code=LB441",
      ),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("reads the rule set inventory and one immutable version", async () => {
    // A Response body may only be read once, so each call gets a fresh one.
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await api.listRuleSets();
    await api.getRuleSet("rule set/1");

    expect(fetchMock).toHaveBeenNthCalledWith(1, expect.stringContaining("/api/v1/rule-sets"), expect.objectContaining({ credentials: "include" }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, expect.stringContaining("/api/v1/rule-sets/rule%20set%2F1"), expect.objectContaining({ credentials: "include" }));
  });

  it("asks for the eligible historical terms on the literal path", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await api.listHistoricalTerms();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/rule-sets/historical-terms"),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("posts activation and rollback without a body of its own", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ rule_set_id: "rs-1" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await api.activateRuleSet("rs-1");
    await api.rollbackRuleSet("rs-1");

    expect(fetchMock).toHaveBeenNthCalledWith(1, expect.stringContaining("/api/v1/rule-sets/rs-1/activate"), expect.objectContaining({ method: "POST", body: undefined }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, expect.stringContaining("/api/v1/rule-sets/rs-1/rollback"), expect.objectContaining({ method: "POST", body: undefined }));
  });

  it("exposes no call that edits a rule set's contents", () => {
    // A version is immutable once mined, so the client must not offer a way to
    // change one; a page cannot reach the backend by any other route.
    const ruleSetCalls = Object.keys(api).filter((name) => /RuleSet/.test(name));

    expect(ruleSetCalls.sort()).toEqual([
      "activateRuleSet",
      "getRuleSet",
      "getRuleSetFacultyCoverage",
      "listRuleSetRules",
      "listRuleSets",
      "rollbackRuleSet",
    ]);
  });

  it("reads one stream's rules with its sort and page in the query", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await api.listRuleSetRules("rs-1", { patternType: "teacher_day", order: "asc", limit: 25, offset: 50 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/v1/rule-sets/rs-1/rules?pattern_type=teacher_day&order=asc&limit=25&offset=50");
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("submits an ARM run with the selected snapshots and per-stream thresholds", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      job_id: "job-1",
      kind: "arm",
      status: "queued",
    }), { status: 202, headers: { "Content-Type": "application/json" } }));
    const stream = { min_support: "0.10", min_confidence: "0.60", min_lift: "1.00", min_term_recurrence: 2 };

    await api.submitArmJob({
      selected_snapshot_ids: ["snap-1"],
      thresholds: {
        teacher_subject: stream,
        teacher_day: stream,
        teacher_time: stream,
        teacher_subject_room: stream,
      },
      notes: null,
    });

    const body = JSON.parse(String(vi.mocked(fetchMock).mock.calls[0][1]?.body));
    expect(body.selected_snapshot_ids).toEqual(["snap-1"]);
    expect(Object.keys(body.thresholds).sort()).toEqual([...PATTERN_TYPES].sort());
    // Decimal thresholds stay strings so the server stores what was typed.
    expect(body.thresholds.teacher_subject.min_support).toBe("0.10");
  });

  it("names the job states that end a mining run", () => {
    expect(TERMINAL_JOB_STATES.every(isTerminalJobState)).toBe(true);
    expect(isTerminalJobState("queued")).toBe(false);
    expect(isTerminalJobState("running")).toBe(false);
  });

  it("separates a run that retained no rules from a run that broke", () => {
    expect(
      isEmptyRuleOutcome({ status: "failed", error: { code: "arm_no_rules_retained", message: "No pattern met the thresholds." } }),
    ).toBe(true);
    expect(
      isEmptyRuleOutcome({ status: "failed", error: { code: "execution_failed", message: "Historical record is missing a time slot." } }),
    ).toBe(false);
    expect(isEmptyRuleOutcome({ status: "succeeded", error: null })).toBe(false);
  });
});
