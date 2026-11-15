import { TriangleAlert } from "lucide-react";
import type { ReadinessBlocker } from "../../api/client";

interface ReadinessWarningsProps {
  warnings: ReadinessBlocker[];
}

// Warnings are findings the GA can schedule around, so they never gate
// generation; they are shown apart from blockers so they are not mistaken for one.
export default function ReadinessWarnings({ warnings }: ReadinessWarningsProps) {
  if (warnings.length === 0) return null;

  return (
    <section aria-label="Readiness warnings" className="border border-tertiary/40 bg-tertiary-fixed/10 rounded-xl p-5 flex gap-3">
      <TriangleAlert className="w-6 h-6 text-tertiary shrink-0" />
      <div className="min-w-0">
        <h2 className="font-headline-md text-lg">Worth reviewing</h2>
        <p className="text-sm text-on-surface-variant mt-1">These do not stop generation.</p>
        <ul className="mt-3 grid gap-2">
          {warnings.map((warning) => (
            <li key={`${warning.code}-${warning.message}`} className="bg-surface-container-lowest/70 border border-outline-variant rounded-lg px-4 py-2.5 text-sm min-w-0 break-words">
              {warning.message}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
