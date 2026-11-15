import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Building
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres


BUILDING = {"building_name": "Lawton", "building_code": "LT"}
ROOM = {
    "room_code": "LT204",
    "building_name": "Lawton",
    "room_type": "laboratory",
}


def test_building_crud_round_trip(db_session: Session):
    client = make_api_client(db_session)

    created = client.post("/api/v1/buildings", json={**BUILDING, "building_code": "lt"})
    assert created.status_code == 201
    building = created.json()
    assert building["building_name"] == "Lawton"
    assert building["building_code"] == "LT"

    assert client.get("/api/v1/buildings").json()["items"] == [building]
    assert client.patch(
        f"/api/v1/buildings/{building['building_id']}",
        json={"building_name": "Lawton Hall"},
    ).json()["building_name"] == "Lawton Hall"
    assert client.delete(f"/api/v1/buildings/{building['building_id']}").status_code == 204
    # Retired, not removed: hidden from the default list, kept for history.
    assert client.get("/api/v1/buildings").json()["items"] == []
    retired = client.get(f"/api/v1/buildings/{building['building_id']}").json()
    assert retired["is_active"] is False
    listed = client.get("/api/v1/buildings", params={"include_inactive": True})
    assert [item["building_id"] for item in listed.json()["items"]] == [
        building["building_id"]
    ]


def test_building_names_and_codes_are_unique(db_session: Session):
    client = make_api_client(db_session)
    assert client.post("/api/v1/buildings", json=BUILDING).status_code == 201

    duplicate_name = client.post(
        "/api/v1/buildings", json={**BUILDING, "building_code": "LH"}
    )
    duplicate_code = client.post(
        "/api/v1/buildings", json={"building_name": "Library", "building_code": "LT"}
    )

    assert duplicate_name.status_code == 409
    assert duplicate_code.status_code == 409


@pytest.mark.parametrize(
    ("building_name", "building_code"),
    [(" ", "LT"), ("Library", "lt"), ("Library", " ")],
)
def test_database_rejects_blank_or_noncanonical_building_values(
    db_session: Session, building_name: str, building_code: str
):
    db_session.add(
        Building(building_name=building_name, building_code=building_code)
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_building_names_are_unique_regardless_of_case(db_session: Session):
    client = make_api_client(db_session)
    assert client.post("/api/v1/buildings", json=BUILDING).status_code == 201

    variant = client.post(
        "/api/v1/buildings", json={"building_name": "LAWTON", "building_code": "LH"}
    )

    assert variant.status_code == 409
    assert variant.json()["code"] == "DUPLICATE_RESOURCE"


def test_room_writes_accept_an_untrimmed_or_recased_building_name(
    db_session: Session,
):
    client = make_api_client(db_session)
    client.post("/api/v1/buildings", json=BUILDING)

    created = client.post("/api/v1/rooms", json={**ROOM, "building_name": "  lawton "})

    assert created.status_code == 201
    # The catalog's spelling wins, so the Room never disagrees with its Building.
    assert created.json()["building_name"] == "Lawton"


def test_renaming_a_building_renames_its_rooms(db_session: Session):
    client = make_api_client(db_session)
    building = client.post("/api/v1/buildings", json=BUILDING).json()
    client.post("/api/v1/rooms", json=ROOM)

    renamed = client.patch(
        f"/api/v1/buildings/{building['building_id']}",
        json={"building_name": "Lawton Hall"},
    )

    assert renamed.status_code == 200
    assert client.get(f"/api/v1/rooms/{ROOM['room_code']}").json()["building_name"] == (
        "Lawton Hall"
    )


def test_a_building_can_be_recased_without_colliding_with_itself(
    db_session: Session,
):
    client = make_api_client(db_session)
    building = client.post("/api/v1/buildings", json=BUILDING).json()

    recased = client.patch(
        f"/api/v1/buildings/{building['building_id']}",
        json={"building_name": "LAWTON"},
    )

    assert recased.status_code == 200
    assert recased.json()["building_name"] == "LAWTON"


def test_room_requires_an_existing_building(db_session: Session):
    client = make_api_client(db_session)

    unknown = client.post("/api/v1/rooms", json=ROOM)
    assert unknown.status_code == 409
    assert unknown.json()["code"] == "UNKNOWN_REFERENCE"

    building = client.post("/api/v1/buildings", json=BUILDING).json()
    assert client.post("/api/v1/rooms", json=ROOM).status_code == 201

    refused = client.delete(f"/api/v1/buildings/{building['building_id']}")
    assert refused.status_code == 409
    assert refused.json()["code"] == "DEPENDENT_RECORDS_EXIST"
    assert refused.json()["details"]["dependents"] == {"rooms": 1}
    assert refused.json()["details"]["dependent_records"]["rooms"]["items"] == [
        "Room LT204"
    ]
    # PATCH takes the same guard as DELETE.
    patched = client.patch(
        f"/api/v1/buildings/{building['building_id']}", json={"is_active": False}
    )
    assert patched.status_code == 409


def test_retired_building_takes_no_active_rooms(db_session: Session):
    client = make_api_client(db_session)
    building = client.post("/api/v1/buildings", json=BUILDING).json()
    other = client.post(
        "/api/v1/buildings", json={"building_name": "Annex", "building_code": "AX"}
    ).json()
    assert client.post("/api/v1/rooms", json=ROOM).status_code == 201

    # A building with only retired rooms can be retired.
    assert client.delete("/api/v1/rooms/LT204").status_code == 204
    url = f"/api/v1/buildings/{building['building_id']}"
    assert client.delete(url).status_code == 204

    # Its retired room keeps its building for history...
    room = client.get("/api/v1/rooms/LT204").json()
    assert room["building_name"] == "Lawton"
    assert room["is_active"] is False

    # ...but cannot be restored there, nor can a new room be put in it.
    restore = client.patch("/api/v1/rooms/LT204", json={"is_active": True})
    assert restore.status_code == 409
    assert restore.json()["code"] == "INACTIVE_REFERENCE"
    created = client.post("/api/v1/rooms", json={**ROOM, "room_code": "LT205"})
    assert created.status_code == 409
    assert created.json()["code"] == "INACTIVE_REFERENCE"

    # Moving it to an active building while restoring it is allowed.
    moved = client.patch(
        "/api/v1/rooms/LT204",
        json={"is_active": True, "building_name": other["building_name"]},
    )
    assert moved.status_code == 200
    assert moved.json()["building_name"] == "Annex"
    assert moved.json()["is_active"] is True

    # Restoring the building lets rooms in again.
    assert client.patch(url, json={"is_active": True}).json()["is_active"] is True
    assert client.post(
        "/api/v1/rooms", json={**ROOM, "room_code": "LT205"}
    ).status_code == 201
