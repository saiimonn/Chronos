interface LoadErrorProps {
  message: string;
  onRetry: () => void;
  retrying?: boolean;
}

// A failed first load shows this instead of the page's empty state or table,
// so an outage is never read as "no records". It stays mounted while a retry
// is in flight (the button keeps focus) and the alert is announced afresh each
// time a retry fails.
export default function LoadError({ message, onRetry, retrying = false }: LoadErrorProps) {
  return (
    <div className="flex flex-wrap items-center gap-4 border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">
      <div className="flex-1 min-w-0">
        {retrying ? <p role="status">Retrying...</p> : <p role="alert">{message}</p>}
      </div>
      {/* aria-disabled, not disabled: a disabled button drops keyboard focus to <body>. */}
      <button
        type="button"
        aria-disabled={retrying}
        onClick={() => { if (!retrying) onRetry(); }}
        className="px-3 py-1.5 rounded border border-error/40 text-sm hover:bg-error-container/50 aria-disabled:opacity-60"
      >
        {retrying ? "Retrying…" : "Retry"}
      </button>
    </div>
  );
}
