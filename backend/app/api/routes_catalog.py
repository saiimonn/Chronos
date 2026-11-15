from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_committee_member
from app.models import Building, CourseProgram, Room, Subject
from app.schemas.catalog import (
    BuildingCreate,
    BuildingResponse,
    BuildingUpdate,
    ProgramCreate,
    ProgramResponse,
    ProgramUpdate,
    RoomCreate,
    RoomResponse,
    RoomUpdate,
    SubjectCreate,
    SubjectResponse,
    SubjectUpdate,
)
from app.schemas.common import ListResponse
from app.services.catalog import buildings, programs, rooms, subjects

router = APIRouter(tags=["catalog"], dependencies=[Depends(require_committee_member)])

DbSession = Annotated[Session, Depends(get_db)]

# Catalog records are never removed: past schedules, snapshots and mined rules
# name them. DELETE is kept for API compatibility but is soft, as for faculty:
# it marks the record inactive, and PATCH {"is_active": true} restores it.
# Lists hide inactive records unless asked, so every scheduling picker offers
# only what is in use; the catalog pages ask for all.


# The `{code:path}` item routes below would otherwise treat "/rooms/" as an item
# with an empty code, so the framework's own trailing-slash redirect never runs.
def _redirect_to_collection(request: Request) -> RedirectResponse:
    url = request.url.replace(path=request.url.path.rstrip("/"))
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


for _collection in ("subjects", "rooms", "course-programs"):
    router.add_api_route(
        f"/{_collection}/",
        _redirect_to_collection,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )


@router.get("/subjects", response_model=ListResponse[SubjectResponse])
def list_subjects(db: DbSession, include_inactive: bool = False) -> dict:
    return {"items": subjects.list_subjects(db, include_inactive)}


@router.post(
    "/subjects", response_model=SubjectResponse, status_code=status.HTTP_201_CREATED
)
def create_subject(payload: SubjectCreate, db: DbSession) -> Subject:
    return subjects.create_subject(db, payload)


@router.get("/subjects/{subject_code:path}", response_model=SubjectResponse)
def read_subject(subject_code: str, db: DbSession) -> Subject:
    return subjects.get_subject(db, subject_code)


@router.patch("/subjects/{subject_code:path}", response_model=SubjectResponse)
def update_subject(subject_code: str, payload: SubjectUpdate, db: DbSession) -> Subject:
    return subjects.update_subject(db, subject_code, payload)


@router.delete("/subjects/{subject_code:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subject(subject_code: str, db: DbSession) -> None:
    subjects.deactivate_subject(db, subject_code)



@router.get("/buildings", response_model=ListResponse[BuildingResponse])
def list_buildings(db: DbSession, include_inactive: bool = False) -> dict:
    return {"items": buildings.list_buildings(db, include_inactive)}


@router.post(
    "/buildings", response_model=BuildingResponse, status_code=status.HTTP_201_CREATED
)
def create_building(payload: BuildingCreate, db: DbSession) -> Building:
    return buildings.create_building(db, payload)


@router.get("/buildings/{building_id}", response_model=BuildingResponse)
def read_building(building_id: UUID, db: DbSession) -> Building:
    return buildings.get_building(db, building_id)


@router.patch("/buildings/{building_id}", response_model=BuildingResponse)
def update_building(
    building_id: UUID, payload: BuildingUpdate, db: DbSession
) -> Building:
    return buildings.update_building(db, building_id, payload)


@router.delete("/buildings/{building_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_building(building_id: UUID, db: DbSession) -> None:
    buildings.deactivate_building(db, building_id)



@router.get("/rooms", response_model=ListResponse[RoomResponse])
def list_rooms(db: DbSession, include_inactive: bool = False) -> dict:
    return {"items": rooms.list_rooms(db, include_inactive)}


@router.post("/rooms", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
def create_room(payload: RoomCreate, db: DbSession) -> Room:
    return rooms.create_room(db, payload)


@router.get("/rooms/{room_code:path}", response_model=RoomResponse)
def read_room(room_code: str, db: DbSession) -> Room:
    return rooms.get_room(db, room_code)


@router.patch("/rooms/{room_code:path}", response_model=RoomResponse)
def update_room(room_code: str, payload: RoomUpdate, db: DbSession) -> Room:
    return rooms.update_room(db, room_code, payload)


@router.delete("/rooms/{room_code:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_room(room_code: str, db: DbSession) -> None:
    rooms.deactivate_room(db, room_code)



@router.get("/course-programs", response_model=ListResponse[ProgramResponse])
def list_programs(db: DbSession, include_inactive: bool = False) -> dict:
    return {"items": programs.list_programs(db, include_inactive)}


@router.post(
    "/course-programs",
    response_model=ProgramResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_program(payload: ProgramCreate, db: DbSession) -> CourseProgram:
    return programs.create_program(db, payload)


@router.get("/course-programs/{program_code:path}", response_model=ProgramResponse)
def read_program(program_code: str, db: DbSession) -> CourseProgram:
    return programs.get_program(db, program_code)


@router.patch("/course-programs/{program_code:path}", response_model=ProgramResponse)
def update_program(
    program_code: str, payload: ProgramUpdate, db: DbSession
) -> CourseProgram:
    return programs.update_program(db, program_code, payload)


@router.delete("/course-programs/{program_code:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_program(program_code: str, db: DbSession) -> None:
    programs.deactivate_program(db, program_code)

