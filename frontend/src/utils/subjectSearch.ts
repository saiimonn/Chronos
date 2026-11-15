// Initials of a title, so "DSA" finds "Data Structures and Algorithms". Two
// forms are kept: every word ("DSAA") and without joining words ("DSA").
const ACRONYM_SKIP = new Set(["and", "of", "the", "to", "in", "for", "with", "on"]);
function titleAcronyms(title: string) {
  const words = title.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const initials = (list: string[]) => list.map((word) => word[0]).join("");
  return [initials(words), initials(words.filter((word) => !ACRONYM_SKIP.has(word)))];
}

// True when a lowercased, trimmed query matches a subject's code, title, or
// title initials.
export function matchesSubjectSearch(code: string, title: string, query: string) {
  if (code.toLowerCase().includes(query) || title.toLowerCase().includes(query)) return true;
  const compact = query.replace(/[^a-z0-9]/g, "");
  return compact.length >= 2 && titleAcronyms(title).some((acronym) => acronym.startsWith(compact));
}
