import { Link } from "react-router-dom";

// Where each readiness blocker is fixed. Every target is a route both roles can
// open, so a Scheduler is never linked to an admin-only page. A code missing
// here still shows its message, just without a link.
export const BLOCKER_FIXES: Record<string, { label: string; to: string }> = {
  NO_TIME_SLOTS: { label: "Generate time slots", to: "/schedule#time-slots" },
  SUBJECT_WITHOUT_COMPATIBLE_TIME_SLOT: { label: "Review time slots", to: "/schedule#time-slots" },
  NO_BLOCK_CLASSES: { label: "Generate classes", to: "/schedule#generate-from-curriculum" },
  INCONSISTENT_OFFERING_BLOCK_MAPPING: { label: "Review Block subjects", to: "/schedule#block-subjects" },
  OFFERING_EXCEEDS_MAX_BLOCKS: { label: "Review Block subjects", to: "/schedule#block-subjects" },
  NO_ROOMS: { label: "Add a room", to: "/rooms" },
  SECTION_EXCEEDS_ALL_ROOM_CAPACITY: { label: "Review rooms", to: "/rooms" },
  LAB_SUBJECT_WITHOUT_LAB_ROOM: { label: "Review rooms", to: "/rooms" },
  LEC_SUBJECT_WITHOUT_LECTURE_ROOM: { label: "Review rooms", to: "/rooms" },
  FACULTY_MASK_LENGTH_MISMATCH: { label: "Review availability", to: "/roster" },
  FACULTY_WITHOUT_AVAILABILITY: { label: "Review availability", to: "/roster" },
  INSUFFICIENT_TEACHING_CAPACITY: { label: "Review faculty", to: "/roster" },
  // Fixed either by revising the term's classes or by activating the record again.
  INACTIVE_SUBJECT_OFFERED: { label: "Review subjects", to: "/subjects" },
  INACTIVE_PROGRAM_BLOCKS: { label: "Review Block subjects", to: "/schedule#block-subjects" },
};

export default function BlockerFixLink({ code }: { code: string }) {
  const fix = BLOCKER_FIXES[code];
  if (!fix) return null;
  return <Link to={fix.to} className="text-primary font-bold underline ml-2 whitespace-nowrap">{fix.label}</Link>;
}
