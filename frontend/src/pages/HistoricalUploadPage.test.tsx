import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api, type HistoricalUploadBatchDetail } from "../api/client";
import { saveFile } from "./CommitteeTimetablePage";
import HistoricalUploadPage from "./HistoricalUploadPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      listHistoricalUploads: vi.fn(),
      getHistoricalUpload: vi.fn(),
      uploadHistoricalWorkbook: vi.fn(),
      downloadHistoricalUploadTemplate: vi.fn(),
      confirmHistoricalUpload: vi.fn(),
    },
  };
});

vi.mock("./CommitteeTimetablePage", () => ({ saveFile: vi.fn() }));

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { user_id: "admin-1", username: "chair", role: "department_administrator" }, logout: vi.fn() }),
}));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: null, loading: false }),
}));

const STALE_LIST = "Could not refresh the upload list; it may be out of date.";
const HASH = "a".repeat(64);

function batch(overrides: Partial<HistoricalUploadBatchDetail> = {}): HistoricalUploadBatchDetail {
  return {
    batch_id: "batch-1",
    status: "staged",
    content_sha256: HASH,
    accepted_count: 2,
    rejected_count: 0,
    skipped_count: 0,
    populated_period_count: 2,
    empty_period_count: 1,
    unknown_subject_count: 0,
    unknown_room_count: 1,
    uploaded_at: "2026-10-10T02:00:00Z",
    committed_at: null,
    rejections: [],
    skipped_rows: [],
    ...overrides,
  };
}

const WORKBOOK_LABEL = /^Offered Courses workbook/;
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

async function chooseWorkbook(user: ReturnType<typeof userEvent.setup>) {
  await user.upload(screen.getByLabelText(WORKBOOK_LABEL), new File(["workbook bytes"], "Offered Courses.xlsx", { type: XLSX }));
}

function renderPage() {
  return render(<MemoryRouter><HistoricalUploadPage /></MemoryRouter>);
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [] });
});

it("tells the admin each sheet is a term that must already exist", () => {
  renderPage();

  expect(screen.getByText(/imported only if that Term exists/)).toBeInTheDocument();
  expect(within(screen.getByRole("main")).getByRole("link", { name: "Term Setup" })).toHaveAttribute("href", "/schedule");
});

it("keeps the file input at 16px below md so iOS does not zoom", () => {
  renderPage();

  expect(screen.getByLabelText(WORKBOOK_LABEL)).toHaveClass("text-base", "md:text-sm");
});

it("accepts only an .xlsx workbook", async () => {
  const user = userEvent.setup({ applyAccept: false });
  renderPage();
  const input = screen.getByLabelText(WORKBOOK_LABEL);
  expect(input).toHaveAttribute("accept", expect.stringContaining(".xlsx"));
  expect(screen.queryAllByLabelText(/manifest|crosswalk|canonical/i)).toHaveLength(0);

  await user.upload(input, new File(["a,b"], "canonical_records.csv", { type: "text/csv" }));

  expect(screen.getByRole("alert")).toHaveTextContent("Choose the Offered Courses workbook, an .xlsx file.");
  expect(screen.getByRole("button", { name: "Upload and validate" })).toBeDisabled();

  await chooseWorkbook(user);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Upload and validate" })).toBeEnabled();
});

it("downloads the Offered Courses template", async () => {
  const user = userEvent.setup();
  const blob = new Blob(["xlsx"], { type: XLSX });
  vi.mocked(api.downloadHistoricalUploadTemplate).mockResolvedValue({ blob, filename: "Offered Courses template.xlsx" });
  renderPage();

  await user.click(screen.getByRole("button", { name: "Download template" }));

  await waitFor(() => expect(saveFile).toHaveBeenCalledWith(blob, "Offered Courses template.xlsx"));
});

it("reports a failed template download", async () => {
  const user = userEvent.setup();
  vi.mocked(api.downloadHistoricalUploadTemplate).mockRejectedValue(new ApiError(500, "ERROR", "Server unavailable."));
  renderPage();

  await user.click(screen.getByRole("button", { name: "Download template" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Server unavailable.");
  expect(saveFile).not.toHaveBeenCalled();
});

it("lists skipped rows apart from blocking ones and still offers the import", async () => {
  const user = userEvent.setup();
  vi.mocked(api.uploadHistoricalWorkbook).mockResolvedValue(
    batch({
      skipped_count: 2,
      skipped_rows: [
        { file: "S1 AY2023-24", line: 3, reason: "dissolved course" },
        { file: "S1 AY2026-27", line: null, reason: "2026-2027 Term 1 is the active term, not history" },
      ],
    }),
  );
  renderPage();
  await chooseWorkbook(user);
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));

  const section = within(await screen.findByRole("region", { name: "Upload batch" }));
  expect(section.getByText("No blocking rows.")).toBeInTheDocument();
  await user.click(section.getByText(/2 skipped rows/));
  const rows = section.getAllByRole("row").slice(1);
  expect(rows.map((row) => row.textContent)).toEqual([
    "S1 AY2023-243dissolved course",
    "S1 AY2026-27—2026-2027 Term 1 is the active term, not history",
  ]);
  expect(section.getByRole("button", { name: "Confirm import" })).toBeInTheDocument();
});

it("uploads the exact bytes of the workbook only once it is chosen", async () => {
  const user = userEvent.setup();
  vi.mocked(api.uploadHistoricalWorkbook).mockResolvedValue(batch());
  renderPage();
  const submit = screen.getByRole("button", { name: "Upload and validate" });
  expect(submit).toBeDisabled();

  await chooseWorkbook(user);
  await user.click(submit);

  await waitFor(() => expect(api.uploadHistoricalWorkbook).toHaveBeenCalledOnce());
  expect(api.uploadHistoricalWorkbook).toHaveBeenCalledWith({ workbook: btoa("workbook bytes") });
  const section = within(await screen.findByRole("region", { name: "Upload batch" }));
  expect(section.getByText("No blocking rows.")).toBeInTheDocument();
  expect(section.getByRole("button", { name: "Confirm import" })).toBeInTheDocument();
});

it("shows every blocking row and offers no import for a batch with any", async () => {
  const user = userEvent.setup();
  vi.mocked(api.uploadHistoricalWorkbook).mockResolvedValue(
    batch({
      accepted_count: 1,
      rejected_count: 2,
      rejections: [
        { file: "S1 AY2023-24", line: 3, reason: "no Faculty record matches teacher 'DOE, JANE'" },
        { file: "database", line: null, reason: "reviewed faculty IDs are not present in the local catalog" },
      ],
    }),
  );
  renderPage();
  await chooseWorkbook(user);
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));

  const section = within(await screen.findByRole("region", { name: "Upload batch" }));
  const rows = section.getAllByRole("row").slice(1);
  expect(rows.map((row) => row.textContent)).toEqual([
    "S1 AY2023-243no Faculty record matches teacher 'DOE, JANE'",
    "database—reviewed faculty IDs are not present in the local catalog",
  ]);
  expect(section.getByText(/cannot be imported/)).toBeInTheDocument();
  expect(section.queryByRole("button", { name: "Confirm import" })).not.toBeInTheDocument();
});

it.each([
  ["the rejected batch also fails to load", false],
  ["the rejected batch still loads", true],
])("keeps the parser reason when the inventory refresh fails and %s", async (_label, detailLoads) => {
  const user = userEvent.setup();
  const reason = "Offered Courses.xlsx: the file is not a readable .xlsx workbook";
  vi.mocked(api.uploadHistoricalWorkbook).mockRejectedValue(
    new ApiError(422, "HISTORY_UPLOAD_INVALID", reason, { batch_id: "batch-rejected" }),
  );
  if (detailLoads) {
    vi.mocked(api.getHistoricalUpload).mockResolvedValue(
      batch({ batch_id: "batch-rejected", status: "rejected", rejections: [{ file: "workbook", line: null, reason }] }),
    );
  } else {
    vi.mocked(api.getHistoricalUpload).mockRejectedValue(new Error("network down"));
  }
  renderPage();
  await chooseWorkbook(user);
  vi.mocked(api.listHistoricalUploads).mockRejectedValue(new Error("network down"));
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));

  await waitFor(() => expect(api.getHistoricalUpload).toHaveBeenCalledWith("batch-rejected"));
  await waitFor(() => expect(screen.getAllByRole("alert")[0]).toHaveTextContent(reason));
});

it("reports a refused upload such as duplicate content", async () => {
  const user = userEvent.setup();
  vi.mocked(api.uploadHistoricalWorkbook).mockRejectedValue(
    new ApiError(409, "HISTORY_UPLOAD_DUPLICATE", "This exact content was already uploaded."),
  );
  renderPage();
  await chooseWorkbook(user);
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("This exact content was already uploaded.");
  expect(screen.queryByRole("region", { name: "Upload batch" })).not.toBeInTheDocument();
});

it("shows the rejected batch an unreadable workbook leaves behind", async () => {
  const user = userEvent.setup();
  const reason = "Offered Courses.xlsx: no sheet has the Offered Courses columns";
  const rejected = batch({
    batch_id: "batch-rejected",
    status: "rejected",
    accepted_count: 0,
    rejected_count: 1,
    populated_period_count: 0,
    empty_period_count: 0,
    unknown_subject_count: null,
    unknown_room_count: null,
    rejections: [{ file: "workbook", line: null, reason }],
  });
  vi.mocked(api.uploadHistoricalWorkbook).mockRejectedValue(
    new ApiError(422, "HISTORY_UPLOAD_INVALID", reason, { batch_id: "batch-rejected" }),
  );
  vi.mocked(api.getHistoricalUpload).mockResolvedValue(rejected);
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [] });
  renderPage();
  await chooseWorkbook(user);
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [rejected] });
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(reason);
  const section = within(await screen.findByRole("region", { name: "Upload batch" }));
  expect(api.getHistoricalUpload).toHaveBeenCalledWith("batch-rejected");
  expect(section.getByText("Rejected, could not be read")).toBeInTheDocument();
  expect(section.getAllByRole("row").slice(1).map((row) => row.textContent)).toEqual([`workbook—${reason}`]);
  expect(section.queryByRole("button", { name: "Confirm import" })).not.toBeInTheDocument();
  const history = within(screen.getByRole("region", { name: "Previous uploads" }));
  expect(await history.findByText(/Rejected · 0 accepted · 0 skipped · 1 blocking/)).toBeInTheDocument();
});

it("imports only after explicit review, pinned to the reviewed hash", async () => {
  const user = userEvent.setup();
  vi.mocked(api.uploadHistoricalWorkbook).mockResolvedValue(batch());
  vi.mocked(api.confirmHistoricalUpload).mockResolvedValue({
    batch: { ...batch(), status: "committed", committed_at: "2026-10-10T03:00:00Z" },
    snapshot_ids: ["s1", "s2"],
    mining_or_activation_performed: false,
  });
  renderPage();
  await chooseWorkbook(user);
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));
  await user.click(await screen.findByRole("button", { name: "Confirm import" }));

  const dialog = within(screen.getByRole("dialog"));
  const importButton = dialog.getByRole("button", { name: "Import history" });
  expect(importButton).toBeDisabled();
  await user.click(dialog.getByRole("checkbox"));
  await user.click(importButton);

  expect(api.confirmHistoricalUpload).toHaveBeenCalledWith("batch-1", HASH);
  expect(await screen.findByRole("status")).toHaveTextContent("No mining run or Rule Set activation was started.");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Confirm import" })).not.toBeInTheDocument();
});

it("keeps the dialog open with the reason when a stale batch is refused", async () => {
  const user = userEvent.setup();
  vi.mocked(api.uploadHistoricalWorkbook).mockResolvedValue(batch());
  vi.mocked(api.confirmHistoricalUpload).mockRejectedValue(
    new ApiError(409, "HISTORY_UPLOAD_STALE", "The batch no longer matches the database."),
  );
  renderPage();
  await chooseWorkbook(user);
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));
  await user.click(await screen.findByRole("button", { name: "Confirm import" }));
  const dialog = within(screen.getByRole("dialog"));
  await user.click(dialog.getByRole("checkbox"));
  await user.click(dialog.getByRole("button", { name: "Import history" }));

  expect(await dialog.findByRole("alert")).toHaveTextContent("The batch no longer matches the database.");
  expect(dialog.getByRole("button", { name: "Cancel" })).toBeEnabled();
});

it("lists previous uploads and inspects the one selected", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [batch({ status: "committed", committed_at: "2026-10-10T03:00:00Z" })] });
  vi.mocked(api.getHistoricalUpload).mockResolvedValue(batch({ status: "committed", committed_at: "2026-10-10T03:00:00Z" }));
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Imported · 2 accepted · 0 skipped · 0 blocking/ }));

  expect(api.getHistoricalUpload).toHaveBeenCalledWith("batch-1");
  const section = within(await screen.findByRole("region", { name: "Upload batch" }));
  expect(section.getByText(/^Imported /)).toBeInTheDocument();
  expect(section.queryByRole("button", { name: "Confirm import" })).not.toBeInTheDocument();
});


it("pages through older uploads beyond the first 50 and opens one", async () => {
  const user = userEvent.setup();
  const firstPage = Array.from({ length: 50 }, (_, index) =>
    batch({ batch_id: `batch-${index}`, uploaded_at: "2026-10-10T02:00:00Z" }),
  );
  const oldest = batch({ batch_id: "batch-oldest", uploaded_at: "2026-09-01T02:00:00Z", rejected_count: 3 });
  vi.mocked(api.listHistoricalUploads).mockImplementation(async (page) =>
    ({ items: page?.offset ? [firstPage[49], oldest] : firstPage }),
  );
  vi.mocked(api.getHistoricalUpload).mockResolvedValue(oldest);
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Load older uploads" }));

  expect(api.listHistoricalUploads).toHaveBeenLastCalledWith({ limit: 50, offset: 50 });
  const list = within(screen.getByRole("region", { name: "Previous uploads" }));
  // The repeated batch from a shifted page is shown once.
  expect(list.getAllByRole("listitem")).toHaveLength(51);
  expect(list.queryByRole("button", { name: "Load older uploads" })).not.toBeInTheDocument();
  await user.click(list.getByRole("button", { name: /3 blocking/ }));
  expect(api.getHistoricalUpload).toHaveBeenCalledWith("batch-oldest");
});

it("offers no older page when the first page is not full", async () => {
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [batch()] });
  renderPage();

  await screen.findByRole("button", { name: /Staged · 2 accepted/ });
  expect(api.listHistoricalUploads).toHaveBeenCalledWith({ limit: 50, offset: 0 });
  expect(screen.queryByRole("button", { name: "Load older uploads" })).not.toBeInTheDocument();
});

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  let reject: (reason: unknown) => void = () => {};
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

const BATCH_A = batch({ batch_id: "batch-a", content_sha256: "a".repeat(64), uploaded_at: "2026-10-10T01:00:00Z" });
const BATCH_B = batch({ batch_id: "batch-b", content_sha256: "b".repeat(64), uploaded_at: "2026-10-10T02:00:00Z", accepted_count: 7 });

it("imports the batch whose confirmation was approved, even if another loads meanwhile", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [BATCH_A, BATCH_B] });
  const readB = deferred<HistoricalUploadBatchDetail>();
  vi.mocked(api.getHistoricalUpload).mockImplementation((id) => (id === "batch-a" ? Promise.resolve(BATCH_A) : readB.promise));
  vi.mocked(api.confirmHistoricalUpload).mockResolvedValue({
    batch: { ...BATCH_A, status: "committed", committed_at: "2026-10-10T03:00:00Z" },
    snapshot_ids: ["s1"],
    mining_or_activation_performed: false,
  });
  renderPage();
  const [buttonA, buttonB] = await screen.findAllByRole("button", { name: /accepted/ });
  await user.click(buttonA);
  await screen.findByRole("region", { name: "Upload batch" });

  // Start loading B, then open A's confirmation and approve it while B is pending.
  await user.click(buttonB);
  await user.click(screen.getByRole("button", { name: "Confirm import" }));
  const dialog = within(screen.getByRole("dialog"));
  await user.click(dialog.getByRole("checkbox"));
  await act(async () => readB.resolve(BATCH_B));

  expect(dialog.getByText("a".repeat(64))).toBeInTheDocument();
  await user.click(dialog.getByRole("button", { name: "Import history" }));
  expect(api.confirmHistoricalUpload).toHaveBeenCalledWith("batch-a", "a".repeat(64));
});

it("keeps the latest selection when an older inspection read resolves last", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [BATCH_A, BATCH_B] });
  const readA = deferred<HistoricalUploadBatchDetail>();
  vi.mocked(api.getHistoricalUpload).mockImplementation((id) => (id === "batch-a" ? readA.promise : Promise.resolve(BATCH_B)));
  renderPage();
  const [buttonA, buttonB] = await screen.findAllByRole("button", { name: /accepted/ });

  await user.click(buttonA);
  await user.click(buttonB);
  const section = within(await screen.findByRole("region", { name: "Upload batch" }));
  expect(section.getByText("b".repeat(64))).toBeInTheDocument();
  await act(async () => readA.resolve(BATCH_A));

  expect(section.getByText("b".repeat(64))).toBeInTheDocument();
  expect(section.queryByText("a".repeat(64))).not.toBeInTheDocument();
});

it.each([
  ["fails with its own reason", "The selected batch is no longer available."],
  ["succeeds", null],
])("does not restore an older upload error over a newer inspection that %s", async (_label, newerError) => {
  const user = userEvent.setup();
  const parserReason = "source_manifest.json: invalid bootstrap manifest";
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [BATCH_B] });
  vi.mocked(api.uploadHistoricalWorkbook).mockRejectedValue(
    new ApiError(422, "HISTORY_UPLOAD_INVALID", parserReason, { batch_id: "batch-rejected" }),
  );
  const readRejected = deferred<HistoricalUploadBatchDetail>();
  vi.mocked(api.getHistoricalUpload).mockImplementation((id) => {
    if (id === "batch-rejected") return readRejected.promise;
    return newerError ? Promise.reject(new ApiError(404, "NOT_FOUND", newerError)) : Promise.resolve(BATCH_B);
  });
  renderPage();
  const buttonB = await screen.findByRole("button", { name: /7 accepted/ });
  await chooseWorkbook(user);

  await user.click(screen.getByRole("button", { name: "Upload and validate" }));
  await waitFor(() => expect(api.getHistoricalUpload).toHaveBeenCalledWith("batch-rejected"));
  await user.click(buttonB);
  await waitFor(() => expect(api.getHistoricalUpload).toHaveBeenCalledWith("batch-b"));
  await act(async () => readRejected.resolve(batch({ batch_id: "batch-rejected", status: "rejected" })));

  if (newerError) {
    expect(screen.getByRole("alert")).toHaveTextContent(newerError);
  } else {
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Upload batch" })).getByText("b".repeat(64))).toBeInTheDocument();
  }
  expect(screen.queryByText(parserReason)).not.toBeInTheDocument();
});

it.each([
  ["fails with its own reason", "The selected batch is no longer available."],
  ["succeeds", null],
])("does not let an older upload's inventory refresh failure replace a newer inspection that %s", async (_label, newerError) => {
  const user = userEvent.setup();
  const parserReason = "source_manifest.json: invalid bootstrap manifest";
  const refresh = deferred<{ items: HistoricalUploadBatchDetail[] }>();
  vi.mocked(api.listHistoricalUploads)
    .mockResolvedValueOnce({ items: [BATCH_B] })
    .mockReturnValueOnce(refresh.promise);
  vi.mocked(api.uploadHistoricalWorkbook).mockRejectedValue(
    new ApiError(422, "HISTORY_UPLOAD_INVALID", parserReason, { batch_id: "batch-rejected" }),
  );
  vi.mocked(api.getHistoricalUpload).mockImplementation((id) => {
    if (id === "batch-rejected") return Promise.resolve(batch({ batch_id: "batch-rejected", status: "rejected" }));
    return newerError ? Promise.reject(new ApiError(404, "NOT_FOUND", newerError)) : Promise.resolve(BATCH_B);
  });
  renderPage();
  const buttonB = await screen.findByRole("button", { name: /7 accepted/ });
  await chooseWorkbook(user);

  await user.click(screen.getByRole("button", { name: "Upload and validate" }));
  await waitFor(() => expect(api.listHistoricalUploads).toHaveBeenCalledTimes(2));
  await user.click(buttonB);
  await waitFor(() => expect(api.getHistoricalUpload).toHaveBeenCalledWith("batch-b"));
  await act(async () => refresh.reject(new Error("network down")));

  // The stale-list banner shows, and the inspection's own state is untouched.
  expect(screen.getByText(STALE_LIST)).toBeInTheDocument();
  if (newerError) {
    expect(screen.getByText(newerError)).toBeInTheDocument();
  } else {
    expect(within(screen.getByRole("region", { name: "Upload batch" })).getByText("b".repeat(64))).toBeInTheDocument();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  }
  expect(screen.queryByText(parserReason)).not.toBeInTheDocument();
});

it("keeps the confirmation actions reachable on a short viewport", async () => {
  const user = userEvent.setup();
  vi.mocked(api.uploadHistoricalWorkbook).mockResolvedValue(batch());
  renderPage();
  await chooseWorkbook(user);
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));
  await user.click(await screen.findByRole("button", { name: "Confirm import" }));

  const importButton = within(screen.getByRole("dialog")).getByRole("button", { name: "Import history" });
  // jsdom has no layout: assert the body scrolls and the footer cannot shrink.
  expect(importButton.parentElement?.className).toContain("shrink-0");
  expect(importButton.parentElement?.previousElementSibling?.className).toContain("min-h-0 overflow-y-auto");
});

it("does not claim no bundles were uploaded while the first list read is pending", () => {
  vi.mocked(api.listHistoricalUploads).mockReturnValue(new Promise(() => {}));
  renderPage();

  expect(screen.queryByText("No Offered Courses workbooks have been uploaded.")).not.toBeInTheDocument();
  expect(screen.getByText("Loading previous uploads...")).toBeInTheDocument();
});

it("shows a retryable error, not the no-bundles claim, when the first list read fails", async () => {
  vi.mocked(api.listHistoricalUploads).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load upload batches."));
  const user = userEvent.setup();
  renderPage();

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load upload batches.");
  expect(screen.queryByText("No Offered Courses workbooks have been uploaded.")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("No Offered Courses workbooks have been uploaded.")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("does not claim nothing was uploaded when an upload succeeded but the list refresh failed", async () => {
  const user = userEvent.setup();
  vi.mocked(api.uploadHistoricalWorkbook).mockResolvedValue(batch());
  renderPage();
  await screen.findByText("No Offered Courses workbooks have been uploaded.");
  await chooseWorkbook(user);
  vi.mocked(api.listHistoricalUploads).mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "Could not load upload batches."));
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));

  await waitFor(() => expect(screen.queryByText("No Offered Courses workbooks have been uploaded.")).not.toBeInTheDocument());
  const previous = screen.getByRole("region", { name: "Previous uploads" });
  expect(within(previous).getByRole("alert")).toHaveTextContent("Could not load upload batches.");
  expect(within(previous).getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

it("lets a newer list read win over a slower older one", async () => {
  const user = userEvent.setup();
  let resolveSlow: (value: { items: [] }) => void = () => {};
  vi.mocked(api.listHistoricalUploads).mockReturnValueOnce(new Promise((resolve) => { resolveSlow = resolve; }));
  vi.mocked(api.uploadHistoricalWorkbook).mockResolvedValue(batch());
  renderPage();
  await chooseWorkbook(user);
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [batch()] });
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));
  const previous = screen.getByRole("region", { name: "Previous uploads" });
  await waitFor(() => expect(within(previous).getByRole("button", { name: /accepted/ })).toBeInTheDocument());

  await act(async () => resolveSlow({ items: [] }));

  expect(within(previous).getByRole("button", { name: /accepted/ })).toBeInTheDocument();
  expect(within(previous).queryByText("No Offered Courses workbooks have been uploaded.")).not.toBeInTheDocument();
});

it("styles the file input with project tokens and uses the shared page header", async () => {
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [] });
  renderPage();

  const input = screen.getByLabelText(WORKBOOK_LABEL);
  expect(input).toHaveAttribute("type", "file");
  expect(input).toHaveAttribute("accept", `.xlsx,${XLSX}`);
  expect(input).toHaveClass("min-w-0", "text-base", "md:text-sm", "file:bg-surface-container-low", "file:border-outline-variant", "file:text-on-surface", "file:disabled:opacity-60", "file:enabled:hover:bg-surface-container-highest");

  expect(screen.getByText("Administrator only")).toHaveClass("uppercase");
  expect(screen.getByRole("heading", { level: 1, name: "Historical Upload" })).toHaveClass("font-headline-lg");
  expect(screen.getByRole("main").firstElementChild).toHaveClass("max-w-7xl");
});

it("keeps the list banner through an inspection, and a good read clears only it", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [BATCH_A, BATCH_B] });
  vi.mocked(api.getHistoricalUpload).mockImplementation((id) => Promise.resolve(id === "batch-a" ? BATCH_A : BATCH_B));
  vi.mocked(api.confirmHistoricalUpload).mockResolvedValue({
    batch: { ...BATCH_A, status: "committed", committed_at: "2026-10-10T03:00:00Z" },
    snapshot_ids: ["s1"],
    mining_or_activation_performed: false,
  });
  renderPage();
  const [buttonA, buttonB] = await screen.findAllByRole("button", { name: /accepted/ });
  await user.click(buttonA);
  await user.click(await screen.findByRole("button", { name: "Confirm import" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("checkbox"));
  vi.mocked(api.listHistoricalUploads).mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "down"));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Import history" }));
  expect(await screen.findByText(STALE_LIST)).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Imported");

  // Inspecting another batch leaves the stale list banner alone.
  await user.click(buttonB);
  await screen.findByRole("region", { name: "Upload batch" });
  expect(screen.getByText(STALE_LIST)).toBeInTheDocument();

  // An inspection error is not the banner's to clear.
  vi.mocked(api.getHistoricalUpload).mockRejectedValueOnce(new ApiError(404, "NOT_FOUND", "Gone."));
  await user.click(buttonA);
  expect(await screen.findByText("Gone.")).toBeInTheDocument();
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: [BATCH_A, BATCH_B] });
  await user.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.queryByText(STALE_LIST)).not.toBeInTheDocument());
  expect(screen.getByText("Gone.")).toBeInTheDocument();
});

const FULL_PAGE = Array.from({ length: 50 }, (_, i) =>
  batch({ batch_id: `b${i}`, content_sha256: String(i).padStart(64, "0") }));

it("shows a failed Load older beside its button, clears it on the next try, and never uses the banner", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listHistoricalUploads).mockResolvedValueOnce({ items: FULL_PAGE });
  renderPage();
  await screen.findAllByRole("button", { name: /accepted/ });
  vi.mocked(api.listHistoricalUploads).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Older failed."));
  await user.click(screen.getByRole("button", { name: "Load older uploads" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Older failed.");
  expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();

  vi.mocked(api.listHistoricalUploads).mockResolvedValueOnce({ items: [] });
  await user.click(screen.getByRole("button", { name: "Load older uploads" }));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

it("keeps the stale-list banner through a good older read, and a good newest read clears the older message", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listHistoricalUploads).mockResolvedValueOnce({ items: FULL_PAGE });
  renderPage();
  await screen.findAllByRole("button", { name: /accepted/ });
  vi.mocked(api.listHistoricalUploads).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "down"));
  vi.mocked(api.confirmHistoricalUpload).mockResolvedValue({
    batch: { ...FULL_PAGE[0], status: "committed", committed_at: "2026-10-10T03:00:00Z" },
    snapshot_ids: ["s1"],
    mining_or_activation_performed: false,
  });
  vi.mocked(api.getHistoricalUpload).mockResolvedValue(FULL_PAGE[0]);
  await user.click(screen.getAllByRole("button", { name: /accepted/ })[0]);
  await user.click(await screen.findByRole("button", { name: "Confirm import" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("checkbox"));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Import history" }));
  expect(await screen.findByText(STALE_LIST)).toBeInTheDocument();

  // A good older read does not clear the stale banner.
  const olderPage = Array.from({ length: 50 }, (_, i) =>
    batch({ batch_id: `o${i}`, content_sha256: String(i + 100).padStart(64, "0") }));
  vi.mocked(api.listHistoricalUploads).mockResolvedValueOnce({ items: olderPage });
  await user.click(screen.getByRole("button", { name: "Load older uploads" }));
  await waitFor(() => expect(screen.getAllByRole("button", { name: /accepted/ })).toHaveLength(100));
  expect(screen.getByText(STALE_LIST)).toBeInTheDocument();

  // A failed older read shows beside the button; a good newest read clears it and the banner.
  vi.mocked(api.listHistoricalUploads).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Older failed."));
  await user.click(screen.getByRole("button", { name: "Load older uploads" }));
  expect(await screen.findByText("Older failed.")).toBeInTheDocument();
  expect(screen.getByText(STALE_LIST)).toBeInTheDocument();
  vi.mocked(api.listHistoricalUploads).mockResolvedValue({ items: FULL_PAGE });
  await user.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.queryByText(STALE_LIST)).not.toBeInTheDocument());
  expect(screen.queryByText("Older failed.")).not.toBeInTheDocument();
});

it("drops a Load older result that a newest-page read overtook, leaving no gap", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listHistoricalUploads).mockResolvedValueOnce({ items: FULL_PAGE });
  renderPage();
  await screen.findAllByRole("button", { name: /accepted/ });
  const older = deferred<{ items: HistoricalUploadBatchDetail[] }>();
  vi.mocked(api.listHistoricalUploads).mockReturnValueOnce(older.promise);
  await user.click(screen.getByRole("button", { name: "Load older uploads" }));
  expect(screen.getByRole("button", { name: "Loading..." })).toBeDisabled();

  // An upload's newest-page read lands first, then the older page resolves with offset-50 rows.
  const stale = batch({ batch_id: "stale-older", content_sha256: "c".repeat(64) });
  vi.mocked(api.uploadHistoricalWorkbook).mockResolvedValue(batch());
  vi.mocked(api.listHistoricalUploads).mockResolvedValueOnce({ items: FULL_PAGE });
  await chooseWorkbook(user);
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));
  await waitFor(() => expect(api.listHistoricalUploads).toHaveBeenCalledTimes(3));
  await act(async () => older.resolve({ items: [stale] }));

  expect(screen.getAllByRole("button", { name: /accepted/ })).toHaveLength(50);
  expect(screen.getByRole("button", { name: "Load older uploads" })).toBeEnabled();
});

it("drops a Load older result when a newest-page read already in flight lands first", async () => {
  const user = userEvent.setup();
  const olderPage = Array.from({ length: 50 }, (_, i) =>
    batch({ batch_id: `o${i}`, content_sha256: String(i + 100).padStart(64, "0") }));
  vi.mocked(api.listHistoricalUploads).mockResolvedValueOnce({ items: FULL_PAGE }).mockResolvedValueOnce({ items: olderPage });
  renderPage();
  await screen.findAllByRole("button", { name: /accepted/ });
  await user.click(screen.getByRole("button", { name: "Load older uploads" }));
  await waitFor(() => expect(screen.getAllByRole("button", { name: /accepted/ })).toHaveLength(100));

  // An upload's newest-page read is in flight when Load older starts at offset 100.
  const newest = deferred<{ items: HistoricalUploadBatchDetail[] }>();
  const older = deferred<{ items: HistoricalUploadBatchDetail[] }>();
  vi.mocked(api.listHistoricalUploads).mockReturnValueOnce(newest.promise).mockReturnValueOnce(older.promise);
  vi.mocked(api.uploadHistoricalWorkbook).mockResolvedValue(batch());
  await chooseWorkbook(user);
  await user.click(screen.getByRole("button", { name: "Upload and validate" }));
  await waitFor(() => expect(api.listHistoricalUploads).toHaveBeenCalledTimes(3));
  await user.click(screen.getByRole("button", { name: "Load older uploads" }));
  await waitFor(() => expect(api.listHistoricalUploads).toHaveBeenCalledTimes(4));

  await act(async () => newest.resolve({ items: FULL_PAGE }));
  expect(screen.getAllByRole("button", { name: /accepted/ })).toHaveLength(50);
  await act(async () => older.resolve({ items: [batch({ batch_id: "late-older", content_sha256: "d".repeat(64) })] }));

  expect(screen.getAllByRole("button", { name: /accepted/ })).toHaveLength(50);
  expect(screen.getByRole("button", { name: "Load older uploads" })).toBeEnabled();
});

it("drops a late Load older error after a Retry replaced the list", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listHistoricalUploads).mockResolvedValueOnce({ items: FULL_PAGE });
  vi.mocked(api.getHistoricalUpload).mockResolvedValue(FULL_PAGE[0]);
  vi.mocked(api.confirmHistoricalUpload).mockResolvedValue({
    batch: { ...FULL_PAGE[0], status: "committed", committed_at: "2026-10-10T03:00:00Z" },
    snapshot_ids: ["s1"],
    mining_or_activation_performed: false,
  });
  renderPage();
  await user.click((await screen.findAllByRole("button", { name: /accepted/ }))[0]);
  await user.click(await screen.findByRole("button", { name: "Confirm import" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("checkbox"));
  vi.mocked(api.listHistoricalUploads).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "down"));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Import history" }));
  expect(await screen.findByText(STALE_LIST)).toBeInTheDocument();

  // Load older is pending when Retry replaces the list; then the older read rejects.
  const older = deferred<{ items: HistoricalUploadBatchDetail[] }>();
  vi.mocked(api.listHistoricalUploads).mockReturnValueOnce(older.promise).mockResolvedValueOnce({ items: FULL_PAGE });
  await user.click(screen.getByRole("button", { name: "Load older uploads" }));
  await user.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.queryByText(STALE_LIST)).not.toBeInTheDocument());
  await act(async () => older.reject(new ApiError(500, "INTERNAL_ERROR", "Late older.")));

  expect(screen.queryByText("Late older.")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Load older uploads" })).toBeEnabled();
});
