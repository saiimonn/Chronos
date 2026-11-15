// Terms before this academic year were scheduled before Chronos and are done.
// Mirrors SETUP_CUTOFF_ACADEMIC_YEAR in backend/app/services/catalog/terms.py,
// which refuses to create, rename to, or activate them.
export const SETUP_CUTOFF_ACADEMIC_YEAR = "2026-2027";

// The server only requires a term number of 1 or more. The setup form offers
// the terms DCISM runs in a year; anything else stays editable through Edit.
export const TERM_NUMBERS = [1, 2, 3];

export function academicYearStart(academicYear: string): number {
  return Number(academicYear.slice(0, 4));
}

/** True for a term from before the first academic year Chronos sets up. */
export function isBeforeSetupCutoff(academicYear: string): boolean {
  return /^\d{4}-\d{4}$/.test(academicYear) && academicYearStart(academicYear) < academicYearStart(SETUP_CUTOFF_ACADEMIC_YEAR);
}

/** Why an academic year can't be set up, or "" when it can. Mirrors the server's checks. */
export function academicYearProblem(value: string): string {
  const match = /^(\d{4})-(\d{4})$/.exec(value);
  if (!match) return "Use the format YYYY-YYYY, for example 2026-2027.";
  if (Number(match[2]) !== Number(match[1]) + 1) return "The second year must follow the first, for example 2026-2027.";
  if (isBeforeSetupCutoff(value)) {
    return `Terms before AY ${SETUP_CUTOFF_ACADEMIC_YEAR} are already done and can't be set up.`;
  }
  return "";
}

// Section letters for a cohort of `count` sections: 1 -> A, 3 -> A, B, C.
export const MAX_SECTIONS = 26;
export function sectionLetters(count: number): string[] {
  if (!Number.isInteger(count) || count < 1) return [];
  return Array.from({ length: Math.min(count, MAX_SECTIONS) }, (_, index) => String.fromCharCode(65 + index));
}
