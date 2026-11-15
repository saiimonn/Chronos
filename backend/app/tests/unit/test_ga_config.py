import pytest
from pydantic import ValidationError

from app.schemas.jobs import GAConfigRequest
from app.services.ga import GAConfig
from app.services.ga.config import validate_meeting_day_patterns
from app.services.jobs.codecs import decode_ga_config, encode_ga_config


def test_defaults_are_internally_consistent():
    config = GAConfig()

    assert config.population_size > 0
    assert config.max_generations > 0
    assert 0.0 <= config.crossover_rate <= 1.0
    assert 0.0 <= config.mutation_rate <= 1.0
    assert 0.0 <= config.gene_mutation_rate <= 1.0
    assert 2 <= config.tournament_size <= config.population_size
    assert 0 <= config.elite_count < config.population_size
    assert config.meeting_day_patterns == ("MW", "TTh", "F")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"population_size": 0}, "population_size must be greater than zero"),
        ({"max_generations": 0}, "max_generations must be greater than zero"),
        ({"crossover_rate": 1.5}, "crossover_rate must be between 0.0 and 1.0"),
        ({"mutation_rate": -0.1}, "mutation_rate must be between 0.0 and 1.0"),
        (
            {"gene_mutation_rate": 1.2},
            "gene_mutation_rate must be between 0.0 and 1.0",
        ),
        ({"tournament_size": 1}, "tournament_size must be at least 2"),
        ({"tournament_size": 999}, "tournament_size must not exceed population_size"),
        ({"elite_count": -1}, "elite_count must not be negative"),
        ({"elite_count": 50}, "elite_count must be smaller than population_size"),
        ({"stagnation_limit": 0}, "stagnation_limit must be greater than zero"),
        ({"meeting_day_patterns": ()}, "meeting_day_patterns must not be empty"),
        ({"meeting_day_patterns": ("",)}, "meeting_day_patterns must not be empty"),
    ],
)
def test_out_of_range_parameters_are_rejected(overrides, message):
    parameters = {"population_size": 50} | overrides

    with pytest.raises(ValueError, match=message):
        GAConfig(**parameters)


def test_stagnation_limit_may_be_disabled():
    assert GAConfig(stagnation_limit=None).stagnation_limit is None


@pytest.mark.parametrize("pattern", ["MX", "Su", "MSu", " "])
def test_invalid_or_calendar_incompatible_days_are_rejected(pattern):
    """CHR-125 / CHR-CFG-002 validates new saves and API overrides alike."""
    with pytest.raises(ValueError):
        validate_meeting_day_patterns((pattern,))
    with pytest.raises(ValidationError) as error:
        GAConfigRequest(meeting_day_patterns=(pattern,))
    assert error.value.errors()[0]["loc"] == ("meeting_day_patterns",)


def test_semester_patterns_are_accepted_without_changing_patterns():
    patterns = ("MW", "TTh", "F")
    assert validate_meeting_day_patterns(patterns) == patterns
    assert GAConfigRequest(meeting_day_patterns=patterns).meeting_day_patterns == patterns
    assert GAConfigRequest(meeting_day_patterns=("TTh",)).meeting_day_patterns == ("TTh",)


@pytest.mark.parametrize("pattern", ["MWTTh", "MTWTh", "MWF", "M", "Sa", "WM", "MTWThFSa"])
def test_patterns_other_than_mw_tth_or_f_are_rejected(pattern):
    """A typo such as "MWTTh" would otherwise schedule every class Mon-Thu;
    the summer term's MTWTh is fixed by the term, not configured."""
    with pytest.raises(ValueError, match="MW, TTh or F"):
        validate_meeting_day_patterns(("MW", pattern))
    with pytest.raises(ValidationError) as error:
        GAConfigRequest(meeting_day_patterns=(pattern,))
    assert error.value.errors()[0]["loc"] == ("meeting_day_patterns",)


def test_existing_job_configuration_keeps_previously_accepted_sunday():
    config = GAConfig(meeting_day_patterns=("MWF", "Su"))
    assert decode_ga_config(encode_ga_config(config)) == config
