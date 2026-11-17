// Where each schedule-quality metric and its reference value come from.
//
// Every citation below was checked against Crossref (title, authors, venue,
// DOI). A reference value is labelled by the kind of evidence behind it:
//   "thesis"   - a validity rule of the Chronos manuscript itself
//   "observed" - a value the cited study measured in practice (context, not a goal)
//   "author"   - defined by this project; no literature target exists
// Do not turn an "observed" figure into a goal: the literature reports typical
// utilization, it does not prescribe one for a department like DCISM.

export type EvidenceKind = "thesis" | "observed" | "author";

export interface Citation {
  key: string;
  apa: string;
  doi?: string;
  url?: string;
}

export const CITATIONS: Record<string, Citation> = {
  beyrouthy2009: {
    key: "Beyrouthy et al. (2009)",
    apa: "Beyrouthy, C., Burke, E. K., Landa-Silva, D., McCollum, B., McMullan, P., & Parkes, A. J. (2009). Towards improving the utilization of university teaching space. Journal of the Operational Research Society, 60(1), 130–143.",
    doi: "10.1057/palgrave.jors.2602523",
  },
  hefce2006: {
    key: "HEFCE (2006)",
    apa: "Space Management Group. (2006). UK higher education space management project: Summary (HEFCE 2006/42). Higher Education Funding Council for England.",
  },
  lewis2008: {
    key: "Lewis (2008)",
    apa: "Lewis, R. (2008). A survey of metaheuristic-based techniques for university timetabling problems. OR Spectrum, 30(1), 167–190.",
    doi: "10.1007/s00291-007-0097-0",
  },
  schaerf1999: {
    key: "Schaerf (1999)",
    apa: "Schaerf, A. (1999). A survey of automated timetabling. Artificial Intelligence Review, 13(2), 87–127.",
    doi: "10.1023/A:1006576209967",
  },
  burke2002: {
    key: "Burke & Petrovic (2002)",
    apa: "Burke, E. K., & Petrovic, S. (2002). Recent research directions in automated timetabling. European Journal of Operational Research, 140(2), 266–280.",
    doi: "10.1016/S0377-2217(02)00069-3",
  },
  bonutti2012: {
    key: "Bonutti et al. (2012)",
    apa: "Bonutti, A., De Cesco, F., Di Gaspero, L., & Schaerf, A. (2012). Benchmarking curriculum-based course timetabling: Formulations, data formats, instances, validation, visualization, and results. Annals of Operations Research, 194(1), 59–70.",
    doi: "10.1007/s10479-010-0707-0",
  },
};

export interface MetricReference {
  formula: string;
  reference: string;
  evidence: EvidenceKind;
  sources: (keyof typeof CITATIONS)[];
}

export const METRIC_REFERENCES = {
  completeness: {
    formula: "Classes assigned ÷ classes required",
    reference: "100% for a valid schedule (manuscript validity rule)",
    evidence: "thesis",
    sources: ["schaerf1999", "lewis2008"],
  },
  conflicts: {
    formula: "Count of hard-constraint violations",
    reference: "0 for a valid schedule: hard constraints must hold for feasibility",
    evidence: "thesis",
    sources: ["schaerf1999", "burke2002", "lewis2008"],
  },
  roomUtilization: {
    formula: "U = F × O; F = room-hours used ÷ room-hours available, O = seats filled ÷ seats offered while in use. Seats filled are estimated from Block capacity (20, or 25 expanded), since enrolment is not recorded",
    reference: "Observed in universities: often about 20–30%, as low as 20–40% (Beyrouthy et al.); UK sector 27% in 2003–04 (HEFCE)",
    evidence: "observed",
    sources: ["beyrouthy2009", "hefce2006"],
  },
  facultyLoad: {
    formula: "Units assigned ÷ maximum units (full-time 24, part-time 17)",
    reference: "≤ 100% per member; 0 members overloaded (manuscript load rule BR-003)",
    evidence: "thesis",
    sources: [],
  },
  dayBalance: {
    formula: "Blocks whose heaviest and lightest Monday–Thursday days differ by ≤ 1.5 h ÷ Blocks",
    reference: "No literature target. Related to the curriculum compactness and minimum working days soft constraints of curriculum-based timetabling",
    evidence: "author",
    sources: ["bonutti2012"],
  },
  preference: {
    formula: "Sessions taught by a member who prefers the subject ÷ sessions of a preferred subject",
    reference: "No literature target; descriptive only",
    evidence: "author",
    sources: [],
  },
} satisfies Record<string, MetricReference>;

export type MetricKey = keyof typeof METRIC_REFERENCES;

export const EVIDENCE_LABEL: Record<EvidenceKind, string> = {
  thesis: "Thesis rule",
  observed: "Observed benchmark",
  author: "Author-defined",
};
