import type { ArmThresholds, PatternType } from "../api/client";

// Thesis-selected ARM thresholds frozen by CHR-99 / CHR-COLAB-003. Source of
// truth: experiments/CHR-99-CHR-COLAB-003/CHR-COLAB-003-57eebe19a06d48b89804484aca170b82/
// selected_thresholds.json (locked in selection_lock.json, run id
// CHR-COLAB-003-57eebe19a06d48b89804484aca170b82). Keep in step with those files.
export const FROZEN_THRESHOLDS: ArmThresholds = {
  teacher_subject: { min_support: "0.0025", min_confidence: "0.2000", min_lift: "2.0000", min_term_recurrence: 3 },
  teacher_day: { min_support: "0.0050", min_confidence: "0.2000", min_lift: "1.2500", min_term_recurrence: 3 },
  teacher_time: { min_support: "0.0050", min_confidence: "0.1000", min_lift: "2.0000", min_term_recurrence: 3 },
  teacher_subject_room: { min_support: "0.0025", min_confidence: "0.5000", min_lift: "2.0000", min_term_recurrence: 2 },
};

export const THRESHOLD_FIELD_LABEL = {
  min_support: "min support",
  min_confidence: "min confidence",
  min_lift: "min lift",
  min_term_recurrence: "min term recurrence",
} as const;

export interface ThresholdDeviation {
  pattern: PatternType;
  field: keyof typeof THRESHOLD_FIELD_LABEL;
  entered: string;
  frozen: string;
}

/** Fields whose numeric value differs from the frozen one. A blank or non-numeric entry counts as a deviation. */
export function thresholdDeviations(current: ArmThresholds): ThresholdDeviation[] {
  const out: ThresholdDeviation[] = [];
  for (const pattern of Object.keys(FROZEN_THRESHOLDS) as PatternType[]) {
    for (const field of Object.keys(THRESHOLD_FIELD_LABEL) as ThresholdDeviation["field"][]) {
      const entered = current[pattern][field];
      const frozen = FROZEN_THRESHOLDS[pattern][field];
      if (String(entered).trim() === "" || Number(entered) !== Number(frozen)) {
        out.push({ pattern, field, entered: String(entered), frozen: String(frozen) });
      }
    }
  }
  return out;
}
