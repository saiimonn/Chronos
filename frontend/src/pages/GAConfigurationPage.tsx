import { useEffect, useRef, useState, type FormEvent } from "react";
import { History, RefreshCw, RotateCcw, Save } from "lucide-react";
import { ApiError, api, type GAConfig, type GAConfiguration, type GAConfigurationVersion } from "../api/client";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";

export type NumericField = Exclude<keyof GAConfig, "stagnation_limit" | "meeting_day_patterns">;

export const NUMERIC_FIELDS: { key: NumericField; label: string; hint: string; step: string }[] = [
  { key: "population_size", label: "Population size", hint: "Greater than 0", step: "1" },
  { key: "max_generations", label: "Max generations", hint: "Greater than 0", step: "1" },
  { key: "crossover_rate", label: "Crossover rate", hint: "0.0 to 1.0", step: "0.01" },
  { key: "mutation_rate", label: "Mutation rate", hint: "0.0 to 1.0", step: "0.01" },
  { key: "gene_mutation_rate", label: "Gene mutation rate", hint: "0.0 to 1.0", step: "0.01" },
  { key: "tournament_size", label: "Tournament size", hint: "At least 2, at most the population", step: "1" },
  { key: "elite_count", label: "Elite count", hint: "0 or more, below the population", step: "1" },
  { key: "random_seed", label: "Random seed", hint: "Any whole number", step: "1" },
];

export type Draft = Record<NumericField | "stagnation_limit" | "meeting_day_patterns", string>;

export function toDraft(config: GAConfig): Draft {
  const draft = {} as Draft;
  for (const { key } of NUMERIC_FIELDS) draft[key] = String(config[key]);
  draft.stagnation_limit = config.stagnation_limit === null ? "" : String(config.stagnation_limit);
  draft.meeting_day_patterns = config.meeting_day_patterns.join(", ");
  return draft;
}

export function fromDraft(draft: Draft): GAConfig {
  const config = {} as GAConfig;
  for (const { key } of NUMERIC_FIELDS) config[key] = Number(draft[key]);
  config.stagnation_limit = draft.stagnation_limit.trim() === "" ? null : Number(draft.stagnation_limit);
  config.meeting_day_patterns = draft.meeting_day_patterns
    .split(",")
    .map((pattern) => pattern.trim())
    .filter((pattern) => pattern !== "");
  return config;
}

// The server reports each refused field as "body.<field>"; checks spanning
// fields (tournament or elite versus population) are reported against "body".
function fieldErrors(caught: ApiError): Record<string, string> {
  const errors = caught.details.errors;
  if (!Array.isArray(errors)) return {};
  const byField: Record<string, string> = {};
  for (const error of errors) {
    const field = typeof error?.field === "string" ? error.field.replace(/^body\.?/, "") : "";
    const key = field.split(".")[0] || "form";
    byField[key] = key === "meeting_day_patterns"
      ? "Use MW, TTh, or F (Friday only, both sessions back to back). Term 3 always meets MTWTh."
      : "Value is out of range.";
  }
  return byField;
}

const FIELD_LABELS: { key: keyof GAConfig; label: string }[] = [
  ...NUMERIC_FIELDS.map(({ key, label }) => ({ key, label })),
  { key: "stagnation_limit", label: "Stagnation limit" },
  { key: "meeting_day_patterns", label: "Meeting day patterns" },
];

export function formatValue(config: GAConfig, key: keyof GAConfig): string {
  const value = config[key];
  if (Array.isArray(value)) return value.join(", ");
  return value === null ? "No limit" : String(value);
}

/** Fields whose value differs from the version saved before this one. */
export function changedFields(config: GAConfig, previous: GAConfig | undefined) {
  if (!previous) return [];
  return FIELD_LABELS.filter(({ key }) => formatValue(config, key) !== formatValue(previous, key)).map(
    ({ key, label }) => ({ label, from: formatValue(previous, key), to: formatValue(config, key) }),
  );
}

function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export default function GAConfigurationPage() {
  const [active, setActive] = useState<GAConfiguration | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [invalid, setInvalid] = useState<Record<string, string>>({});
  const [savedMessage, setSavedMessage] = useState("");
  const requestRef = useRef(0);
  const [versions, setVersions] = useState<GAConfigurationVersion[] | null>(null);
  const [historyError, setHistoryError] = useState("");
  const historyRef = useRef(0);

  // History is loaded separately so a failure here never blocks editing.
  async function loadHistory() {
    const request = ++historyRef.current;
    setHistoryError("");
    try {
      const list = await api.listGAConfigurationVersions();
      if (request === historyRef.current) setVersions(list.items);
    } catch (caught) {
      if (request !== historyRef.current) return;
      setHistoryError(caught instanceof ApiError ? caught.message : "Could not load the version history.");
    }
  }

  async function load() {
    const request = ++requestRef.current;
    setLoading(true);
    setError("");
    try {
      const configuration = await api.getGAConfiguration();
      if (request !== requestRef.current) return;
      setActive(configuration);
      setDraft(toDraft(configuration.config));
      setInvalid({});
    } catch (caught) {
      if (request !== requestRef.current) return;
      setError(caught instanceof ApiError ? caught.message : "Could not load the GA configuration.");
    } finally {
      if (request === requestRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    void loadHistory();
    return () => { requestRef.current += 1; historyRef.current += 1; };
  }, []);

  function loadVersion(version: GAConfigurationVersion) {
    setDraft(toDraft(version.config));
    setInvalid({});
    setError("");
    setSavedMessage(`Loaded version ${version.version} into the form. Save to make it the active version.`);
    window.scrollTo?.({ top: 0, behavior: "smooth" });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft || saving) return;
    const request = ++requestRef.current;
    setSaving(true);
    setError("");
    setInvalid({});
    setSavedMessage("");
    try {
      const saved = await api.saveGAConfiguration(fromDraft(draft));
      if (request !== requestRef.current) return;
      setActive(saved);
      setDraft(toDraft(saved.config));
      setSavedMessage(`Saved as version ${saved.version}. New GA runs use it; queued runs keep their settings.`);
      void loadHistory();
    } catch (caught) {
      if (request !== requestRef.current) return;
      if (caught instanceof ApiError && caught.status === 422) {
        setInvalid(fieldErrors(caught));
        setError("Some values are invalid. Nothing was saved.");
      } else {
        setError(caught instanceof ApiError ? caught.message : "Could not save the GA configuration.");
      }
    } finally {
      if (request === requestRef.current) setSaving(false);
    }
  }

  function update(key: keyof Draft, value: string) {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
    setSavedMessage("");
  }

  const inputClass = (key: string) =>
    `w-full mt-2 border rounded-lg px-3 py-2.5 bg-surface-container-lowest ${invalid[key] ? "border-error" : "border-outline-variant"}`;

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="flex flex-wrap justify-between items-end gap-4">
              <div>
                <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">Administrator only</p>
                <h1 className="font-headline-lg text-[32px] mt-1">GA Configuration</h1>
                <p className="text-on-surface-variant mt-1">Every save creates a new version. GA runs copy the active version when submitted, so queued runs are unaffected by later edits.</p>
              </div>
              <button onClick={() => { void load(); void loadHistory(); }} disabled={loading || saving} className="flex items-center gap-2 px-4 py-2 border border-outline-variant rounded-lg bg-surface-container-lowest disabled:opacity-60">
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Refresh
              </button>
            </div>

            {error && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>}
            {invalid.form && <p className="text-sm text-error">Tournament size must not exceed the population, and elite count must be below it.</p>}
            {savedMessage && <p role="status" className="border border-primary/30 bg-primary/5 rounded-lg p-4">{savedMessage}</p>}
            {loading && !draft && <p className="border border-outline-variant rounded-xl bg-surface-container-lowest p-10 text-center text-on-surface-variant">Loading the active configuration...</p>}

            {draft && (
              <form onSubmit={(event) => void save(event)} className="border border-outline-variant rounded-xl bg-surface-container-lowest p-6 space-y-5">
                <p className="text-sm text-on-surface-variant">
                  {active?.version != null
                    ? `Active version ${active.version}${active.created_at ? `, saved ${formatTimestamp(active.created_at)}` : ""}.`
                    : "No version saved yet; showing the built-in defaults."}
                </p>
                <div className="grid gap-4 sm:grid-cols-2">
                  {NUMERIC_FIELDS.map(({ key, label, hint, step }) => (
                    <label key={key} className="block">
                      <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">{label}</span>
                      <input type="number" step={step} required value={draft[key]} onChange={(event) => update(key, event.target.value)} disabled={saving} aria-invalid={Boolean(invalid[key])} className={inputClass(key)} />
                      <span className={`block text-xs mt-1 ${invalid[key] ? "text-error" : "text-on-surface-variant"}`}>{invalid[key] ?? hint}</span>
                    </label>
                  ))}
                  <label className="block">
                    <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Stagnation limit</span>
                    <input type="number" step="1" value={draft.stagnation_limit} onChange={(event) => update("stagnation_limit", event.target.value)} disabled={saving} aria-invalid={Boolean(invalid.stagnation_limit)} className={inputClass("stagnation_limit")} />
                    <span className={`block text-xs mt-1 ${invalid.stagnation_limit ? "text-error" : "text-on-surface-variant"}`}>{invalid.stagnation_limit ?? "Greater than 0; leave blank for no limit"}</span>
                  </label>
                  <label className="block">
                    <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Meeting day patterns</span>
                    <input type="text" value={draft.meeting_day_patterns} onChange={(event) => update("meeting_day_patterns", event.target.value)} disabled={saving} aria-invalid={Boolean(invalid.meeting_day_patterns)} className={inputClass("meeting_day_patterns")} />
                    <span className={`block text-xs mt-1 ${invalid.meeting_day_patterns ? "text-error" : "text-on-surface-variant"}`}>{invalid.meeting_day_patterns ?? "Comma-separated: MW, TTh, F (terms 1-2). F is used only for subjects that prefer it on the Subjects page. Term 3 (summer) is always MTWTh."}</span>
                  </label>
                </div>
                <div className="flex justify-end">
                  <button type="submit" disabled={saving} className="flex items-center gap-2 px-5 py-2 rounded-lg bg-surface-tint text-white font-bold disabled:opacity-60">
                    <Save className="w-4 h-4" /> {saving ? "Saving..." : "Save as new version"}
                  </button>
                </div>
              </form>
            )}

            <section aria-labelledby="ga-history-heading" className="border border-outline-variant rounded-xl bg-surface-container-lowest p-6 space-y-4">
              <div>
                <h2 id="ga-history-heading" className="font-headline-sm text-[20px] flex items-center gap-2">
                  <History className="w-5 h-5" /> Version history
                </h2>
                <p className="text-sm text-on-surface-variant mt-1">Saved versions are never changed. Loading a past version fills the form; saving it makes a new version.</p>
              </div>
              {historyError && <p role="alert" className="text-sm text-error">{historyError}</p>}
              {!versions && !historyError && <p className="text-sm text-on-surface-variant">Loading the version history...</p>}
              {versions?.length === 0 && <p className="text-sm text-on-surface-variant">No versions saved yet.</p>}
              {versions && versions.length > 0 && (
                <ol className="divide-y divide-outline-variant">
                  {versions.map((item, index) => {
                    const changes = changedFields(item.config, versions[index + 1]?.config);
                    return (
                      <li key={item.version} className="py-3">
                        <details>
                          <summary className="cursor-pointer list-none flex flex-wrap items-center gap-x-3 gap-y-1">
                            <span className="font-bold">Version {item.version}</span>
                            {item.is_active && <span className="text-[11px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-primary/10 text-primary font-bold">Active</span>}
                            <span className="text-sm text-on-surface-variant">
                              {formatTimestamp(item.created_at)} by {item.created_by_username ?? "system"}
                            </span>
                            <span className="text-sm text-on-surface-variant basis-full">
                              {index === versions.length - 1
                                ? "First version."
                                : changes.length === 0
                                  ? "No changes from the previous version."
                                  : changes.map((change) => `${change.label}: ${change.from} → ${change.to}`).join("; ")}
                            </span>
                          </summary>
                          <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2 mt-3 text-sm">
                            {FIELD_LABELS.map(({ key, label }) => (
                              <div key={key} className="flex justify-between gap-4 border-b border-outline-variant/50 py-1">
                                <dt className="text-on-surface-variant">{label}</dt>
                                <dd className="font-medium text-right">{formatValue(item.config, key)}</dd>
                              </div>
                            ))}
                          </dl>
                          {!item.is_active && (
                            <button type="button" onClick={() => loadVersion(item)} disabled={saving || !draft} className="mt-3 flex items-center gap-2 px-4 py-2 border border-outline-variant rounded-lg disabled:opacity-60">
                              <RotateCcw className="w-4 h-4" /> Load version {item.version} into form
                            </button>
                          )}
                        </details>
                      </li>
                    );
                  })}
                </ol>
              )}
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}
