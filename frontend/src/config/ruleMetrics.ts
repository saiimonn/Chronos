import type { RuleSetRule, RuleSetThreshold } from "../api/client";

// Reader-facing definitions of the ARM metrics shown on the Rule Sets page.
// The "acceptable" values are the version's own recorded retention thresholds.
// The guidance score has no cut-off: the GA uses every retained rule and reads
// the score as a weight (services/ga/rule_scoring.py), so a low score still helps.

export type RuleMetric = "support" | "confidence" | "lift" | "term_recurrence" | "guidance_score";

export interface RuleMetricGuide {
  metric: RuleMetric;
  label: string;
  range: string;
  acceptable: string;
  meaning: string;
}

const NOT_RECORDED = "Not recorded for this version";

export function ruleMetricGuide(threshold: RuleSetThreshold | undefined): RuleMetricGuide[] {
  const recorded = (value: string | number | undefined) => (value === undefined ? NOT_RECORDED : `≥ ${value}`);
  return [
    {
      metric: "support",
      label: "Support",
      range: "0 – 1",
      acceptable: recorded(threshold?.min_support),
      meaning:
        "How often the teacher and the pattern occur together, out of all teaching records in this stream. A higher value means the pattern appears more frequently.",
    },
    {
      metric: "confidence",
      label: "Confidence",
      range: "0 – 1",
      acceptable: recorded(threshold?.min_confidence),
      meaning:
        "Of the teacher's classes, the share that follow the pattern. A higher value means the pattern is more reliable for that teacher.",
    },
    {
      metric: "lift",
      label: "Lift",
      range: "> 0",
      acceptable: recorded(threshold?.min_lift),
      meaning:
        "How much more often the teacher follows the pattern than teachers in general do. A value above 1 means the pattern is specific to that teacher, not just common for everyone.",
    },
    {
      metric: "term_recurrence",
      label: "Terms",
      range: "1 – training terms",
      acceptable: recorded(threshold?.min_term_recurrence),
      meaning:
        "In how many different academic terms the pattern was observed. A higher value means it has been seen repeatedly across different terms.",
    },
    {
      metric: "guidance_score",
      label: "Guidance score",
      range: "0 – 1",
      acceptable: "Higher guidance score means higher priority in GA",
      meaning:
        "A score that combines how reliable the rule is and how consistently it appears across academic terms. The genetic algorithm uses this score as additional guidance when creating schedules, but it never overrides mandatory scheduling requirements.",
    },
  ];
}

/** Whether a rule's value clears the acceptable value; undefined when none is recorded. */
export function meetsAcceptable(
  metric: RuleMetric,
  rule: RuleSetRule,
  threshold: RuleSetThreshold | undefined,
): boolean | undefined {
  if (metric === "guidance_score" || !threshold) return undefined;
  switch (metric) {
    case "support":
      return Number(rule.support) >= Number(threshold.min_support);
    case "confidence":
      return Number(rule.confidence) >= Number(threshold.min_confidence);
    case "lift":
      return Number(rule.lift) >= Number(threshold.min_lift);
    case "term_recurrence":
      return rule.term_recurrence >= threshold.min_term_recurrence;
  }
}
