import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { AlertTriangle, CheckCircle2, Download, Upload } from "lucide-react";
import { Link } from "react-router-dom";
import {
  ApiError,
  api,
  type HistoricalUploadBatch,
  type HistoricalUploadBatchDetail,
  type HistoricalUploadRejection,
} from "../api/client";
import LoadError from "../components/LoadError";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";
import Modal from "../components/modals/Modal";
import { saveFile } from "./CommitteeTimetablePage";

const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const WORKBOOK_ACCEPT = `.xlsx,${XLSX_TYPE}`;
const NOT_XLSX_MESSAGE = "Choose the Offered Courses workbook, an .xlsx file. Download the template for the format.";

function isXlsx(file: File): boolean {
  return file.name.toLowerCase().endsWith(".xlsx");
}

function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

const LIST_STALE_MESSAGE = "Could not refresh the upload list; it may be out of date.";

function messageOf(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback;
}

// The exact bytes are sent, so the server's hashes match the files on disk.
async function toBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}

function RowTable({ rows, caption }: { rows: HistoricalUploadRejection[]; caption: string }) {
  return (
    <div className="overflow-x-auto border border-outline-variant rounded-xl">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface-container-low text-on-surface-variant">
          <tr>
            <th scope="col" className="text-left px-4 py-2.5">Sheet</th>
            <th scope="col" className="text-right px-4 py-2.5">Row</th>
            <th scope="col" className="text-left px-4 py-2.5">Reason</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-t border-outline-variant">
              <td className="px-4 py-2.5 font-mono-data">{row.file}</td>
              <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{row.line ?? "—"}</td>
              <td className="px-4 py-2.5">{row.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BatchSummary({ batch }: { batch: HistoricalUploadBatchDetail }) {
  const unknown = (value: number | null) => (value === null ? "not checked" : String(value));
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        {[
          ["Accepted records", String(batch.accepted_count)],
          ["Blocking", String(batch.rejected_count)],
          ["Skipped rows", String(batch.skipped_count)],
          ["Terms with records", String(batch.populated_period_count)],
          ["Empty complete terms", String(batch.empty_period_count)],
          ["Unknown subject codes", unknown(batch.unknown_subject_count)],
          ["Unknown room codes", unknown(batch.unknown_room_count)],
        ].map(([label, value]) => (
          <div key={label} className="border border-outline-variant rounded-lg p-3">
            <dt className="text-on-surface-variant">{label}</dt>
            <dd className="font-mono-data tabular-nums text-lg">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-sm break-all text-on-surface-variant">
        Content hash: <code>{batch.content_sha256}</code>
      </p>

      {batch.rejections.length === 0 ? (
        <p className="text-sm">No blocking rows.</p>
      ) : (
        <RowTable rows={batch.rejections} caption="Blocking rows and why each prevents the import." />
      )}

      {batch.skipped_rows.length > 0 && (
        <details className="space-y-2">
          <summary className="cursor-pointer text-sm font-medium">
            {batch.skipped_rows.length} skipped {batch.skipped_rows.length === 1 ? "row" : "rows"} (not imported, does not block)
          </summary>
          <RowTable rows={batch.skipped_rows} caption="Skipped rows and why each is not history to import." />
        </details>
      )}
    </div>
  );
}

export default function HistoricalUploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [batches, setBatches] = useState<HistoricalUploadBatch[]>([]);
  // Until the first list read succeeds the page cannot say whether any bundle exists.
  const [batchesState, setBatchesState] = useState<"loading" | "ready" | "failed">("loading");
  const [batchesError, setBatchesError] = useState("");
  const [batchesRetrying, setBatchesRetrying] = useState(false);
  // Rows on screen from the last good read, and the newest list request.
  const batchCount = useRef(0);
  const listSeq = useRef(0);
  // Counts newest-page reads whose rows were applied; Load older drops a result they overtook.
  const listLanded = useRef(0);
  const [detail, setDetail] = useState<HistoricalUploadBatchDetail | null>(null);
  const [error, setError] = useState("");
  // A failed list refresh with rows on screen; only a good newest-page read clears it.
  const [refreshError, setRefreshError] = useState("");
  // A failed "Load older"; cleared when it starts and by a good newest-page read.
  const [olderError, setOlderError] = useState("");
  const [notice, setNotice] = useState("");
  // The batch the open confirmation is about, captured when it opened, so
  // approval can never carry over to a batch loaded afterwards.
  const [confirmTarget, setConfirmTarget] = useState<HistoricalUploadBatchDetail | null>(null);
  // Only the latest selection, upload or import may replace the detail.
  const detailRequestRef = useRef(0);
  const [reviewed, setReviewed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState("");

  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);

  // Reloads the newest page; older pages are fetched on request. A failure with
  // rows on screen goes to its own banner, never the upload or inspection state.
  const loadBatches = useCallback(async () => {
    // A slower, older read must not overwrite a newer one.
    const seq = ++listSeq.current;
    try {
      const { items } = await api.listHistoricalUploads({ limit: BATCH_PAGE_SIZE, offset: 0 });
      if (seq !== listSeq.current) return;
      setBatches(items);
      listLanded.current += 1;
      batchCount.current = items.length;
      setHasOlder(items.length === BATCH_PAGE_SIZE);
      setBatchesError("");
      setRefreshError("");
      setOlderError("");
      setBatchesState("ready");
    } catch (caught) {
      if (seq !== listSeq.current) return;
      setBatchesError(messageOf(caught, "Could not load upload batches."));
      setBatchesState("failed");
      // With rows on screen a failed refresh is a banner; with none, LoadError carries it.
      if (batchCount.current > 0) setRefreshError(LIST_STALE_MESSAGE);
    }
  }, []);

  async function loadOlder() {
    if (loadingOlder) return;
    // A newest-page read started or applied meanwhile replaces the rows this offset assumed.
    const seq = listSeq.current;
    const landed = listLanded.current;
    const overtaken = () => seq !== listSeq.current || landed !== listLanded.current;
    setLoadingOlder(true);
    setOlderError("");
    try {
      const { items } = await api.listHistoricalUploads({ limit: BATCH_PAGE_SIZE, offset: batches.length });
      if (overtaken()) return;
      // A batch uploaded meanwhile shifts the offsets by one; skip any repeat.
      setBatches((current) => {
        const seen = new Set(current.map((batch) => batch.batch_id));
        return [...current, ...items.filter((batch) => !seen.has(batch.batch_id))];
      });
      setHasOlder(items.length === BATCH_PAGE_SIZE);
    } catch (caught) {
      if (!overtaken()) setOlderError(messageOf(caught, "Could not load older upload batches."));
    } finally {
      setLoadingOlder(false);
    }
  }

  useEffect(() => {
    void loadBatches();
  }, [loadBatches]);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (uploading || !file) return;
    const request = ++detailRequestRef.current;
    setUploading(true);
    setError("");
    setNotice("");
    try {
      const created = await api.uploadHistoricalWorkbook({ workbook: await toBase64(file) });
      if (request === detailRequestRef.current) setDetail(created);
      setFile(null);
      (event.target as HTMLFormElement).reset();
      await loadBatches();
    } catch (caught) {
      // A malformed bundle is still kept as a rejected batch; show that record.
      const rejectedId = caught instanceof ApiError ? caught.details.batch_id : undefined;
      if (typeof rejectedId === "string") {
        await loadBatches();
        try {
          const rejected = await api.getHistoricalUpload(rejectedId);
          if (request === detailRequestRef.current) setDetail(rejected);
        } catch {
          // The upload error below already tells the administrator what failed.
        }
      }
      // Only while a newer inspection has not taken over the view.
      if (request === detailRequestRef.current) {
        setError(messageOf(caught, "Could not upload the Offered Courses workbook."));
      }
    } finally {
      setUploading(false);
    }
  }

  async function downloadTemplate() {
    if (downloading) return;
    setDownloading(true);
    setError("");
    try {
      const { blob, filename } = await api.downloadHistoricalUploadTemplate();
      saveFile(blob, filename);
    } catch (caught) {
      setError(messageOf(caught, "Could not download the template."));
    } finally {
      setDownloading(false);
    }
  }

  async function inspect(batchId: string) {
    const request = ++detailRequestRef.current;
    setError("");
    setNotice("");
    try {
      const loaded = await api.getHistoricalUpload(batchId);
      if (request === detailRequestRef.current) setDetail(loaded);
    } catch (caught) {
      if (request === detailRequestRef.current) {
        setError(messageOf(caught, "Could not load this upload batch."));
      }
    }
  }

  function openConfirm() {
    if (!detail) return;
    setReviewed(false);
    setConfirmError("");
    setConfirmTarget(detail);
  }

  async function confirm() {
    const target = confirmTarget;
    if (!target || !reviewed || confirming) return;
    setConfirming(true);
    setConfirmError("");
    try {
      const result = await api.confirmHistoricalUpload(target.batch_id, target.content_sha256);
      // A read started before the import would bring back the staged state.
      detailRequestRef.current += 1;
      setDetail((current) =>
        current?.batch_id === target.batch_id ? { ...current, ...result.batch } : current,
      );
      setConfirmTarget(null);
      setNotice(
        `Imported ${result.batch.accepted_count} records into ${result.snapshot_ids.length} historical terms. No mining run or Rule Set activation was started.`,
      );
      await loadBatches();
    } catch (caught) {
      setConfirmError(messageOf(caught, "Could not import this batch."));
    } finally {
      setConfirming(false);
    }
  }

const BATCH_STATUS_LABEL: Record<HistoricalUploadBatch["status"], string> = {
  staged: "Staged",
  committed: "Imported",
  rejected: "Rejected",
};

// Matches the API's default page size for the upload list.
const BATCH_PAGE_SIZE = 50;

  const ready = file !== null;
  const canConfirm = detail?.status === "staged" && detail.rejected_count === 0;

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            <div>
              <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">Administrator only</p>
              <h1 className="font-headline-lg text-[32px] mt-1">Historical Upload</h1>
              <p className="text-on-surface-variant">
                Upload the Offered Courses workbook (.xlsx), review what was accepted, skipped, and blocking, then
                confirm the import. Nothing is written to history until you confirm.
              </p>
              <p className="text-sm text-on-surface-variant">
                Each sheet is one term (for example <span className="font-mono-data">S1 AY2025-26</span> or{" "}
                <span className="font-mono-data">Summer 2026</span>) and is imported only if that Term exists. Create
                them on <Link to="/schedule" className="text-primary underline">Term Setup</Link> first. Courses must be
                in the Subject catalog and teachers on the Roster.
              </p>
            </div>

            {refreshError && (
              <LoadError
                message={refreshError}
                retrying={batchesRetrying}
                onRetry={() => {
                  setBatchesRetrying(true);
                  void loadBatches().finally(() => setBatchesRetrying(false));
                }}
              />
            )}
            {error && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>}
            {notice && (
              <p role="status" className="flex items-start gap-2 border border-primary/30 bg-primary-fixed/20 rounded-lg p-4">
                <CheckCircle2 className="w-5 h-5 shrink-0" /> {notice}
              </p>
            )}

            <form onSubmit={(event) => void upload(event)} aria-label="Upload Offered Courses workbook" className="bg-surface-container-lowest border border-outline-variant rounded-xl p-6 space-y-4">
              <div className="flex flex-wrap items-end justify-between gap-4">
                <label className="min-w-0 flex-1 space-y-1 text-sm">
                  <span className="font-medium">Offered Courses workbook</span>
                  <span className="block text-on-surface-variant">Offered Courses.xlsx, one sheet per term</span>
                  <input
                    type="file"
                    accept={WORKBOOK_ACCEPT}
                    disabled={uploading}
                    aria-describedby={fileError ? "workbook-error" : undefined}
                    onChange={(event) => {
                      const chosen = event.target.files?.[0] ?? null;
                      if (chosen && !isXlsx(chosen)) {
                        setFileError(NOT_XLSX_MESSAGE);
                        setFile(null);
                        event.target.value = "";
                        return;
                      }
                      setFileError("");
                      setFile(chosen);
                    }}
                    className="block w-full min-w-0 text-base md:text-sm text-on-surface-variant file:mr-3 file:rounded-lg file:border file:border-outline-variant file:bg-surface-container-low file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-on-surface file:enabled:hover:bg-surface-container-highest file:disabled:opacity-60"
                  />
                </label>
                <button
                  type="button"
                  onClick={() => void downloadTemplate()}
                  disabled={downloading}
                  className="inline-flex items-center gap-2 px-4 py-2 border border-outline-variant rounded-lg bg-surface-container-lowest hover:bg-surface-container-highest disabled:opacity-60"
                >
                  <Download className="w-4 h-4" /> {downloading ? "Downloading..." : "Download template"}
                </button>
              </div>
              {fileError && <p id="workbook-error" role="alert" className="text-sm text-error">{fileError}</p>}
              <button
                type="submit"
                disabled={!ready || uploading}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-tint text-white font-bold disabled:opacity-60"
              >
                <Upload className="w-4 h-4" /> {uploading ? "Validating..." : "Upload and validate"}
              </button>
            </form>

            {detail && (
              <section aria-label="Upload batch" className="bg-surface-container-lowest border border-outline-variant rounded-xl p-6 space-y-4">
                <div className="flex flex-wrap justify-between items-start gap-3">
                  <div>
                    <h2 className="font-headline-md text-xl">Uploaded {formatTimestamp(detail.uploaded_at)}</h2>
                    <p className="text-sm text-on-surface-variant">
                      {detail.status === "committed"
                        ? `Imported ${formatTimestamp(detail.committed_at as string)}`
                        : detail.status === "rejected"
                          ? "Rejected, could not be read"
                          : "Staged, not yet imported"}
                    </p>
                  </div>
                  {canConfirm && (
                    <button onClick={openConfirm} className="px-4 py-2 rounded-lg bg-surface-tint text-white font-bold">
                      Confirm import
                    </button>
                  )}
                </div>
                {detail.status !== "committed" && detail.rejected_count > 0 && (
                  <p className="flex items-start gap-2 border border-tertiary/40 bg-tertiary-fixed/20 rounded-lg p-4">
                    <AlertTriangle className="w-5 h-5 shrink-0" />
                    A batch with blocking rows cannot be imported. Correct the workbook or the Roster and upload it again.
                  </p>
                )}
                <BatchSummary batch={detail} />
              </section>
            )}

            <section aria-label="Previous uploads" className="space-y-2">
              <h2 className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Previous uploads</h2>
              {batchesState === "failed" && batches.length === 0 ? (
                <LoadError
                  message={batchesError}
                  retrying={batchesRetrying}
                  onRetry={() => {
                    setBatchesRetrying(true);
                    void loadBatches().finally(() => setBatchesRetrying(false));
                  }}
                />
              ) : batchesState === "loading" ? (
                <p className="text-on-surface-variant">Loading previous uploads...</p>
              ) : batches.length === 0 ? (
                <p className="text-on-surface-variant">No Offered Courses workbooks have been uploaded.</p>
              ) : (
                <ul className="space-y-2">
                  {batches.map((batch) => (
                    <li key={batch.batch_id}>
                      <button
                        onClick={() => void inspect(batch.batch_id)}
                        className="w-full text-left border border-outline-variant rounded-lg p-3 hover:bg-surface-container-highest"
                      >
                        <span className="font-medium">{formatTimestamp(batch.uploaded_at)}</span>
                        <span className="text-sm text-on-surface-variant">
                          {" "}· {BATCH_STATUS_LABEL[batch.status]} · {batch.accepted_count} accepted · {batch.skipped_count} skipped · {batch.rejected_count} blocking
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {hasOlder && (
                <button
                  onClick={() => void loadOlder()}
                  disabled={loadingOlder || batchesRetrying}
                  className="px-4 py-2 border border-outline-variant rounded-lg bg-surface-container-lowest disabled:opacity-60"
                >
                  {loadingOlder ? "Loading..." : "Load older uploads"}
                </button>
              )}
              {olderError && <p role="alert" className="text-sm text-error">{olderError}</p>}
            </section>
          </div>
        </main>
      </div>

      {confirmTarget && (
        <Modal title="Import reviewed historical batch?" subtitle={`${confirmTarget.accepted_count} records`} onClose={() => { if (!confirming) setConfirmTarget(null); }}>
          <div className="flex-1 min-h-0 overflow-y-auto p-6 space-y-4">
            <p>The accepted records will be written as immutable history in one transaction. This cannot be edited or undone.</p>
            <p className="text-sm">Uploaded {formatTimestamp(confirmTarget.uploaded_at)}</p>
            <p className="text-sm break-all">Content hash: <code>{confirmTarget.content_sha256}</code></p>
            <p className="text-sm text-on-surface-variant">Importing does not run mining or activate a Rule Set.</p>
            <label className="flex items-start gap-3 border border-outline-variant rounded-lg p-3 cursor-pointer">
              <input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} disabled={confirming} className="mt-1" />
              <span>I reviewed this batch and approve importing it.</span>
            </label>
            {confirmError && <p role="alert" className="text-sm text-error">{confirmError}</p>}
          </div>
          <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
            <button type="button" onClick={() => setConfirmTarget(null)} disabled={confirming} className="px-4 py-2 disabled:opacity-60">Cancel</button>
            <button type="button" onClick={() => void confirm()} disabled={!reviewed || confirming} className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60">
              {confirming ? "Importing..." : "Import history"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
