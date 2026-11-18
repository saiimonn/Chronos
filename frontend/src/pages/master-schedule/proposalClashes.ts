/**
 * Clashes between proposals still in review.
 *
 * The server checks each proposal only against the approved master schedule,
 * so two proposals can each look clean while booking the same room or
 * instructor at the same time. The clash would surface only when the second
 * one is approved. This finds those clashes up front, so they can be seen and
 * fixed before anything is approved.
 */

export interface ProposedMeeting {
  blockId: string;
  classId: string;
  /** Classes of one offering meet together (a combined lecture), never a clash. */
  offeringId: string;
  subjectCode: string;
  facultyId: string;
  roomCode: string | null;
  days: string[];
  /** Minutes after midnight; `end` is exclusive. */
  start: number;
  end: number;
}

export interface ProposalClash {
  /** The Block this clash is listed under, and its class. */
  blockId: string;
  classId: string;
  subjectCode: string;
  /** The other proposal's Block and class. */
  otherBlockId: string;
  otherClassId: string;
  otherSubjectCode: string;
  /** What both classes hold at once. */
  resource: "room" | "instructor";
  day: string;
}

/** Every clash, listed once under each of the two Blocks involved. */
export function findProposalClashes(meetings: ProposedMeeting[]): Map<string, ProposalClash[]> {
  const byBlock = new Map<string, ProposalClash[]>();
  const add = (clash: ProposalClash) => {
    const list = byBlock.get(clash.blockId);
    if (list) list.push(clash);
    else byBlock.set(clash.blockId, [clash]);
  };
  for (let i = 0; i < meetings.length; i += 1) {
    const a = meetings[i];
    for (let j = i + 1; j < meetings.length; j += 1) {
      const b = meetings[j];
      if (a.blockId === b.blockId || a.offeringId === b.offeringId) continue;
      if (a.start >= b.end || b.start >= a.end) continue;
      const day = a.days.find((token) => b.days.includes(token));
      if (!day) continue;
      const resources: ProposalClash["resource"][] = [];
      if (a.roomCode && a.roomCode === b.roomCode) resources.push("room");
      if (a.facultyId && a.facultyId === b.facultyId) resources.push("instructor");
      for (const resource of resources) {
        add({ blockId: a.blockId, classId: a.classId, subjectCode: a.subjectCode, otherBlockId: b.blockId, otherClassId: b.classId, otherSubjectCode: b.subjectCode, resource, day });
        add({ blockId: b.blockId, classId: b.classId, subjectCode: b.subjectCode, otherBlockId: a.blockId, otherClassId: a.classId, otherSubjectCode: a.subjectCode, resource, day });
      }
    }
  }
  return byBlock;
}
