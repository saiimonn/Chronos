"""How a workbook teacher name relates to a Faculty roster row.

The Offered Courses workbook writes names as "SURNAME, GIVEN NAMES M." in
upper case; the roster stores display names ("Given M. Surname"). The roster
import CLI and the historical upload both use these helpers, so a name added
by one is recognized by the other.
"""

from __future__ import annotations

import re
import uuid

# Fixed namespace so the same historical name always yields the same id.
HISTORICAL_FACULTY_NAMESPACE = uuid.UUID("6d3f2b4e-9a1c-5e7f-8b2d-4c6a8e0f1b3d")

_SUFFIXES = {"JR", "JR.", "SR", "SR.", "II", "III", "IV"}
_SUFFIX_DISPLAY = {"JR": "Jr.", "JR.": "Jr.", "SR": "Sr.", "SR.": "Sr."}
_TITLES = {"FR.": "Fr.", "FR": "Fr."}
_VOWELS = set("AEIOUÁÉÍÓÚÑ")


def _title_token(token: str) -> str:
    if token in _TITLES:
        return _TITLES[token]
    if token in _SUFFIX_DISPLAY:
        return _SUFFIX_DISPLAY[token]
    if token in {"II", "III", "IV"}:
        return token
    # A middle initial ("S." or "S") stays upper case.
    if re.fullmatch(r"[A-ZÑ]\.?", token):
        return token if token.endswith(".") else f"{token}."
    # Letter-only initialisms such as "RB" have no vowel; keep them as written.
    if token.isalpha() and not (_VOWELS & set(token)):
        return token
    # "D'COSTA" -> "D'Costa", "DEL" -> "Del".
    return "'".join(part[:1].upper() + part[1:].lower() for part in token.split("'"))


def display_name(historical_name: str) -> str:
    """Turn "ALCANTARA, BENEDICT S." into "Benedict S. Alcantara".

    The workbook writes names as "SURNAME, GIVEN NAMES MIDDLE-INITIAL" in upper
    case, sometimes with a generational suffix among the given names ("FELIX
    JR. L.") and ".." where no middle initial was recorded. Suffixes move to
    the end, as on the reviewed roster entries.
    """

    surname, _, given = historical_name.partition(",")
    if not given:
        return " ".join(_title_token(t) for t in historical_name.split())
    given_tokens = [t for t in given.split() if t.strip(".")]
    suffixes = [t for t in given_tokens if t in _SUFFIXES]
    rest = [t for t in given_tokens if t not in _SUFFIXES]
    parts = [_title_token(t) for t in rest]
    parts += [_title_token(t) for t in surname.split()]
    parts += [_title_token(t) for t in suffixes]
    return " ".join(parts)


def _name_key(name: str) -> str:
    return re.sub(r"[^a-zñ]", "", name.lower())


def historical_faculty_id(historical_name: str) -> uuid.UUID:
    return uuid.uuid5(HISTORICAL_FACULTY_NAMESPACE, historical_name)
