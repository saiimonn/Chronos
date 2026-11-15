import { api, type BlockSection, type Faculty, type Room, type Subject, type TimeSlot } from "../../api/client";

export interface Catalog {
  faculty: Faculty[];
  facultyUnavailable: boolean;
  rooms: Room[];
  subjects: Subject[];
  blocks: BlockSection[];
  timeSlots: TimeSlot[];
}

export const EMPTY_CATALOG: Catalog = {
  faculty: [],
  facultyUnavailable: false,
  rooms: [],
  subjects: [],
  blocks: [],
  timeSlots: [],
};

// Department program order: BSCS, then BSIT, then BSIS. Any other program
// follows, alphabetically.
const PROGRAM_ORDER = ["BSCS", "BSIT", "BSIS"];

export function programRank(code: string): number {
  const index = PROGRAM_ORDER.indexOf(code.toUpperCase());
  return index === -1 ? PROGRAM_ORDER.length : index;
}

/**
 * Master Schedule order: year level, then program (BSCS, BSIT, BSIS), then Block
 * letter (A, B, C...). "Generate all Blocks" runs in this order, so 1st-year
 * Blocks are proposed before 2nd-year ones.
 */
export function compareBlocks(a: BlockSection, b: BlockSection): number {
  return (
    a.year_level - b.year_level ||
    compareProgramCodes(a.program_code, b.program_code) ||
    a.section_letter.length - b.section_letter.length ||
    a.section_letter.localeCompare(b.section_letter) ||
    a.block_id.localeCompare(b.block_id)
  );
}

/** Program codes in department order: BSCS, BSIT, BSIS, then any others A-Z. */
export function compareProgramCodes(a: string, b: string): number {
  return programRank(a) - programRank(b) || a.localeCompare(b);
}

// Read on every synchronization rather than cached, so a Room or faculty
// member added in another session is selectable without a reload.
export async function loadCatalog(termId: string): Promise<Catalog> {
  // Inactive members too, so a record that still names one shows their name.
  const faculty = api.listFaculty(true)
    .then((result) => ({ items: result.items, unavailable: false }))
    .catch(() => ({ items: [] as Faculty[], unavailable: true }));
  const [facultyResult, rooms, subjects, blocks, timeSlots] = await Promise.all([
    faculty,
    // Retired rooms and subjects too, so records naming them keep their
    // details; edit pickers filter on is_active themselves.
    api.listRooms(true),
    api.listSubjects(true),
    api.listBlocks(termId),
    api.listTimeSlots(termId),
  ]);
  return {
    faculty: facultyResult.items,
    facultyUnavailable: facultyResult.unavailable,
    rooms: rooms.items,
    subjects: subjects.items,
    blocks: [...blocks.items].sort(compareBlocks),
    timeSlots: timeSlots.items,
  };
}
