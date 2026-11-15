"""CHR-FIX-016: the seed CLI has no default password and enforces the API's limits."""

import pytest

from app.cli import seed_users as cli
from app.cli.seed_users import PASSWORD_ENV_VAR, SeedPasswordError, seed_password
from app.schemas.auth import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


def set_password(monkeypatch, password: str | None) -> None:
    if password is None:
        monkeypatch.delenv(PASSWORD_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(PASSWORD_ENV_VAR, password)


@pytest.mark.parametrize(
    "password",
    [
        None,
        "",
        " " * MIN_PASSWORD_LENGTH,
        "a" * (MIN_PASSWORD_LENGTH - 1),
        "a" * (MAX_PASSWORD_LENGTH + 1),
    ],
)
def test_missing_blank_or_out_of_range_password_is_refused(monkeypatch, password) -> None:
    set_password(monkeypatch, password)
    with pytest.raises(SeedPasswordError):
        seed_password()


def test_invalid_utf8_password_is_refused(monkeypatch) -> None:
    set_password(monkeypatch, "valid-prefix-\udcff")
    with pytest.raises(SeedPasswordError):
        seed_password()


@pytest.mark.parametrize(
    "password", ["a" * MIN_PASSWORD_LENGTH, "a" * MAX_PASSWORD_LENGTH, " pad word "]
)
def test_valid_password_is_returned_verbatim(monkeypatch, password) -> None:
    set_password(monkeypatch, password)
    assert seed_password() == password


def test_main_refuses_before_touching_the_database(monkeypatch, capsys) -> None:
    set_password(monkeypatch, None)

    def no_session():
        raise AssertionError("database opened")

    monkeypatch.setattr(cli, "SessionLocal", no_session)

    assert cli.main() == 1
    assert PASSWORD_ENV_VAR in capsys.readouterr().err


def test_main_reports_the_source_never_the_value(monkeypatch, capsys) -> None:
    set_password(monkeypatch, "s3cret-pass")

    class Stop(Exception):
        pass

    def stop():
        raise Stop

    monkeypatch.setattr(cli, "SessionLocal", stop)
    with pytest.raises(Stop):
        cli.main()

    out = capsys.readouterr().out
    assert PASSWORD_ENV_VAR in out
    assert "s3cret-pass" not in out
