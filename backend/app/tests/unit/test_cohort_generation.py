from app.services.catalog.cohort_generation import (
    PlannedClass,
    plan_cohort_classes,
)


def test_new_blocks_get_every_required_subject():
    plan = plan_cohort_classes(["A"], {}, ["CIS 2101", "CIS 2102"])

    assert plan.blocks_to_create == ("A",)
    assert plan.existing_blocks == ()
    assert plan.classes_to_create == (
        PlannedClass("A", "CIS 2101"),
        PlannedClass("A", "CIS 2102"),
    )
    assert plan.existing_classes == ()


def test_only_subjects_not_yet_taken_are_planned():
    plan = plan_cohort_classes(
        ["A"], {"A": frozenset({"CIS 2101"})}, ["CIS 2101", "CIS 2102"]
    )

    assert plan.blocks_to_create == ()
    assert plan.existing_blocks == ("A",)
    assert plan.classes_to_create == (PlannedClass("A", "CIS 2102"),)
    assert plan.existing_classes == (PlannedClass("A", "CIS 2101"),)


def test_a_subject_taken_through_another_group_counts_as_taken():
    # The planner sees subject codes, not groups: a Block already in G3 of a
    # subject takes that subject, and nothing new is planned for it.
    plan = plan_cohort_classes(["B"], {"B": frozenset({"CIS 2101"})}, ["CIS 2101"])

    assert plan.classes_to_create == ()
    assert plan.existing_classes == (PlannedClass("B", "CIS 2101"),)


def test_empty_requirements_still_create_the_blocks():
    # Year 3 has no summer term: the Blocks exist, and take nothing.
    plan = plan_cohort_classes(["A", "B"], {}, [])

    assert plan.blocks_to_create == ("A", "B")
    assert plan.classes_to_create == ()
    assert plan.existing_classes == ()


def test_every_block_gets_its_own_class_per_subject():
    # One planned class per Block per subject, and each becomes its own
    # offering: generation never merges two Blocks into one group.
    plan = plan_cohort_classes(["A", "B", "C"], {}, ["CIS 2101"])

    assert plan.classes_to_create == (
        PlannedClass("A", "CIS 2101"),
        PlannedClass("B", "CIS 2101"),
        PlannedClass("C", "CIS 2101"),
    )


def test_a_fully_generated_cohort_plans_nothing():
    taken = frozenset({"CIS 2101", "CIS 2102"})
    plan = plan_cohort_classes(
        ["A", "B"], {"A": taken, "B": taken}, ["CIS 2101", "CIS 2102"]
    )

    assert plan.blocks_to_create == ()
    assert plan.classes_to_create == ()
    assert len(plan.existing_classes) == 4


def test_classes_outside_the_curriculum_are_ignored():
    # An elective added by hand is neither planned nor reported; generation
    # only adds, so it never removes what the curriculum does not list.
    plan = plan_cohort_classes(
        ["A"], {"A": frozenset({"CS ELEC 1"})}, ["CIS 2101"]
    )

    assert plan.classes_to_create == (PlannedClass("A", "CIS 2101"),)
    assert plan.existing_classes == ()
