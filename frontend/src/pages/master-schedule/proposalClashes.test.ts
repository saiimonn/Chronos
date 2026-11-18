import { expect, it } from "vitest";
import { findProposalClashes, type ProposedMeeting } from "./proposalClashes";

const base: ProposedMeeting = {
  blockId: "a", classId: "a-1", offeringId: "o-a", subjectCode: "CS101",
  facultyId: "f1", roomCode: "LB 483", days: ["M", "W"], start: 480, end: 570,
};

it("lists a shared room at the same time under both Blocks", () => {
  const clashes = findProposalClashes([
    base,
    { ...base, blockId: "b", classId: "b-1", offeringId: "o-b", subjectCode: "CS102", facultyId: "f2", days: ["W"] },
  ]);
  expect(clashes.get("a")).toEqual([{ blockId: "a", classId: "a-1", subjectCode: "CS101", otherBlockId: "b", otherClassId: "b-1", otherSubjectCode: "CS102", resource: "room", day: "W" }]);
  expect(clashes.get("b")?.[0]).toMatchObject({ otherBlockId: "a", resource: "room" });
});

it("flags the same instructor in two rooms at once", () => {
  const clashes = findProposalClashes([base, { ...base, blockId: "b", classId: "b-1", offeringId: "o-b", roomCode: "LB 484" }]);
  expect(clashes.get("a")?.map((clash) => clash.resource)).toEqual(["instructor"]);
});

it("ignores a combined lecture, back-to-back times, other days and the same Block", () => {
  expect(findProposalClashes([
    base,
    { ...base, blockId: "b", classId: "b-1" }, // same offering: one combined session
    { ...base, blockId: "c", classId: "c-1", offeringId: "o-c", start: 570, end: 660 }, // starts as a ends
    { ...base, blockId: "d", classId: "d-1", offeringId: "o-d", days: ["T", "Th"] },
  ]).size).toBe(0);
  // Within one Block the server's own check already covers it.
  expect(findProposalClashes([base, { ...base, classId: "a-2", offeringId: "o-a2" }]).size).toBe(0);
});
