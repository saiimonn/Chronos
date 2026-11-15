import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Plus } from "lucide-react";
import {
  ApiError,
  ROOM_CAPACITY_MAX,
  ROOM_CODE_MAX_LENGTH,
  ROOM_TYPES,
  ROOM_TYPE_LABEL,
  api,
  deriveRoomCapacity,
  deriveRoomCode,
  normalizeRoomNumber,
  type Building,
  type Room,
  type RoomType,
  type Subject,
  type SubjectType,
} from "../api/client";
import { ShowInactiveToggle, hiddenInactiveMessage, isActive } from "../components/catalog/ActiveStatus";
import {
  CatalogRow,
  CatalogTable,
  EditButton,
  RowActions,
  RowActiveToggle,
  SortHeader,
  StatusPill,
  naturalCompare,
  sortRows,
  useSort,
} from "../components/catalog/CatalogTable";

type RoomSortKey = "room" | "building" | "type" | "capacity" | "status";
const byRoomCode = (a: Room, b: Room) => naturalCompare(a.room_code, b.room_code);
import ConfirmDeleteDialog from "../components/modals/ConfirmDeleteDialog";
import Modal from "../components/modals/Modal";
import LoadError from "../components/LoadError";
import SearchSelect from "../components/forms/SearchSelect";
import { matchesSubjectSearch } from "../utils/subjectSearch";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";

type BuildingsStatus = "loading" | "ready" | "failed";

// The room side of a subject's allowed rooms: the same records, so a subject
// picked here lists this room on the Subjects page and vice versa. A lab room
// is offered only LAB subjects and a lecture room only LEC ones.
function AllowedSubjects({ value, onChange, roomType }: { value: string[]; onChange: (next: string[]) => void; roomType: RoomType }) {
  const [subjects, setSubjects] = useState<Subject[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    api.listSubjects().then((response) => setSubjects(response.items)).catch(() => setFailed(true));
  }, []);
  const wantedType: SubjectType = roomType === "laboratory" ? "LAB" : "LEC";
  const offered = useMemo(
    () => (subjects ?? [])
      .filter((subject) => subject.subject_type === wantedType && !value.includes(subject.subject_code))
      .sort((a, b) => naturalCompare(a.subject_code, b.subject_code)),
    [subjects, value, wantedType],
  );
  const titleOf = (code: string) => subjects?.find((subject) => subject.subject_code === code)?.subject_title;
  return (
    <fieldset className="sm:col-span-2 space-y-2">
      <legend className="font-label-md text-[11px] uppercase text-on-surface-variant">Allowed subjects</legend>
      {value.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {/* A chosen subject that no longer fits (retired, or the room type
              changed) still shows here, so it can be removed. */}
          {value.map((code) => (
            <button
              key={code}
              type="button"
              onClick={() => onChange(value.filter((item) => item !== code))}
              aria-label={`Remove ${code}`}
              title={titleOf(code)}
              className="px-3 py-1.5 rounded-full border border-primary bg-primary/15 text-primary text-xs font-bold focus:outline-none focus:ring-2 focus:ring-primary"
            >
              {code} <span aria-hidden="true">×</span>
            </button>
          ))}
          <button type="button" onClick={() => onChange([])} className="px-2 py-1.5 text-xs text-on-surface-variant underline-offset-2 hover:underline">Clear</button>
        </div>
      )}
      {/* Searchable, but a click opens the full list too, so typing is optional.
          It always shows empty: picking a subject adds it as a chip above. */}
      <SearchSelect
        label="Add subject"
        value=""
        options={offered.map((subject) => ({ value: subject.subject_code, label: subject.subject_code, description: subject.subject_title }))}
        onChange={(code) => code && onChange([...value, code].sort(naturalCompare))}
        placeholder={`Add a ${wantedType} subject...`}
        loading={subjects === null && !failed}
        error={failed ? "Could not load subjects." : ""}
        loadingMessage="Loading subjects..."
        emptyMessage={`No more ${wantedType} subjects to add.`}
        // Same search as the Subjects page: code, title, or initials ("DSA").
        filter={(option, query) => matchesSubjectSearch(option.label, option.description ?? "", query)}
      />
      <p className="text-xs text-on-surface-variant">
        {value.length
          ? `This room is added to the allowed rooms of ${value.join(", ")}. A subject with no allowed rooms before is then limited to this room.`
          : "None chosen: no subject is tied to this room."}
      </p>
    </fieldset>
  );
}

function RoomForm({ buildings, buildingsStatus, buildingsStale, buildingsRetrying, onRetryBuildings, room, onClose, onSaved }: { buildings: Building[]; buildingsStatus: BuildingsStatus; buildingsStale: boolean; buildingsRetrying: boolean; onRetryBuildings: () => void; room?: Room; onClose: () => void; onSaved: () => void }) {
  // The room code is spelled from the building's code and the room number, so
  // the number is the only part the form collects. An existing room keeps the
  // code it was created with: it is the primary key the API addresses.
  const [roomNumber, setRoomNumber] = useState("");
  // A room may only name a catalog building, so the field offers those and
  // nothing else. Names are unique, so the name identifies the row.
  const [buildingName, setBuildingName] = useState(room?.building_name ?? "");
  // Rooms predating the catalog carry whatever spelling was typed into the old
  // free-text field, and the API resolves a name by trimming and folding case.
  // Matching the same way keeps a casing variant from being reported as
  // uncatalogued, and lets the field show the catalog's spelling of it.
  // Catalog names are already trimmed by the API, so only this side needs it.
  const selected = buildings.find(
    (building) => building.building_name.toLowerCase() === buildingName.trim().toLowerCase(),
  );
  const [roomType, setRoomType] = useState<RoomType>(room?.room_type ?? "lecture");
  const [expanded, setExpanded] = useState(room?.expanded ?? false);
  const [allowedSubjects, setAllowedSubjects] = useState<string[]>(room?.allowed_subject_codes ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Existing rooms show the code they hold; new ones show what the entered
  // number would produce, so the value is never typed and never a surprise.
  const derivedCode = room
    ? room.room_code
    : deriveRoomCode(selected?.building_code ?? "", roomNumber);
  const codeTooLong = derivedCode.length > ROOM_CODE_MAX_LENGTH;
  const derivedCapacity = deriveRoomCapacity(roomType, expanded);
  // Capacity follows the derived value until it is typed over. A room saved
  // with a different seat count opens as overridden, so editing it does not
  // silently snap the count back to the default.
  const [capacityInput, setCapacityInput] = useState<string | null>(
    room && room.capacity !== deriveRoomCapacity(room.room_type, room.expanded) ? String(room.capacity) : null,
  );
  const capacityOverridden = capacityInput !== null && capacityInput !== String(derivedCapacity);
  const capacityValue = Number(capacityInput);
  const capacityInvalid = capacityOverridden
    && !(capacityInput.trim() !== "" && Number.isInteger(capacityValue) && capacityValue >= 1 && capacityValue <= ROOM_CAPACITY_MAX);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const values = {
        building_name: selected?.building_name ?? buildingName.trim(),
        room_type: roomType,
        expanded,
        // Null takes the derived value (and resets an earlier override).
        capacity: capacityOverridden ? capacityValue : null,
        allowed_subject_codes: allowedSubjects,
      };
      if (room) {
        await api.updateRoom(room.room_code, values);
      } else {
        await api.createRoom({ room_code: derivedCode, ...values });
      }
      onSaved();
    } catch (caught: unknown) {
      // The code is derived, so a duplicate names the collision and the field
      // that resolves it rather than reporting a generic conflict.
      if (caught instanceof ApiError && caught.code === "DUPLICATE_RESOURCE") {
        setError(`Room ${derivedCode} already exists. Enter a different room number.`);
      } else {
        setError(caught instanceof ApiError ? caught.message : "Could not save room.");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={room ? `Edit ${room.room_code}` : "Add room"} onClose={onClose}>
      <form onSubmit={submit} className="flex flex-col min-h-0">
        <div className="p-6 grid sm:grid-cols-2 gap-5 overflow-y-auto">
          {/* The code reads building code, then room number, so the two inputs
              sit in that order with the code they spell directly beneath. */}
          <div className={`space-y-1.5 ${room ? "sm:col-span-2" : ""}`}>
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Building</span>
            <select value={selected?.building_name ?? buildingName} onChange={(event) => setBuildingName(event.target.value)} required disabled={buildings.length === 0} className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest disabled:bg-surface-container-highest outline-none focus:border-primary">
              <option value="" disabled>Select a building</option>
              {/* An unresolvable name is listed rather than dropped, so the field
                  cannot display a different building than the one it holds. It is
                  only called uncatalogued once the catalog is actually known: a
                  failed re-read leaves the last list standing, but stale. */}
              {buildingName && !selected && (
                <option value={buildingName} disabled>{buildingName}{buildingsStatus === "ready" && !buildingsStale && !buildingsRetrying ? " (not in catalog)" : ""}</option>
              )}
              {/* The code leads because it is the part that enters the room code. */}
              {buildings.map((building) => (
                <option key={building.building_id} value={building.building_name}>
                  {building.building_code} — {building.building_name}
                </option>
              ))}
            </select>
          </label>
            {/* Outside the label so the select's accessible name stays "Building". */}
            {buildingsStatus === "ready" && buildings.length === 0 && !buildingsRetrying && (
              <span className="block text-sm text-on-surface-variant">
                No buildings recorded yet. <Link to="/buildings" className="text-primary underline">Add a building</Link> first.
              </span>
            )}
            {(buildingsStatus === "loading" || (buildingsStatus === "ready" && !buildingsStale && buildingsRetrying)) && <span className="block text-sm text-on-surface-variant">Loading buildings...</span>}
            {(buildingsStatus === "failed" || buildingsStale) && (
              <span className="flex flex-wrap items-center gap-3 text-sm text-error">
                Buildings could not be loaded.
                <button
                  type="button"
                  aria-disabled={buildingsRetrying}
                  onClick={() => { if (!buildingsRetrying) onRetryBuildings(); }}
                  className="px-3 py-1 rounded border border-error/40 aria-disabled:opacity-60"
                >
                  {buildingsRetrying ? "Retrying…" : "Retry"}
                </button>
              </span>
            )}
          </div>
          {!room && (
            <label className="block space-y-1.5">
              <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Room number</span>
              <input value={roomNumber} onChange={(event) => setRoomNumber(event.target.value)} required maxLength={ROOM_CODE_MAX_LENGTH} inputMode="text" placeholder="e.g. 483" className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest uppercase placeholder:normal-case outline-none focus:border-primary" />
            </label>
          )}
          <div className="sm:col-span-2 space-y-1.5">
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Room code (derived)</span>
            <input value={derivedCode} disabled placeholder="Filled in from the fields above" className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-highest font-medium outline-none" />
          </label>
            <span className="block text-sm text-on-surface-variant">
              {room
                ? "The room code is fixed once a room is created; changing the building does not rename it."
                : "Building code + room number."}
            </span>
            {codeTooLong && (
              <span className="block text-sm text-error">
                {`A room code may be at most ${ROOM_CODE_MAX_LENGTH} characters. Shorten the room number.`}
              </span>
            )}
          </div>
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Room type</span>
            <select value={roomType} onChange={(event) => setRoomType(event.target.value as RoomType)} required className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary">
              {ROOM_TYPES.map((type) => (
                <option key={type} value={type}>{ROOM_TYPE_LABEL[type]}</option>
              ))}
            </select>
          </label>
          <div className="space-y-1.5">
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Capacity</span>
            <input
              type="number"
              min={1}
              max={ROOM_CAPACITY_MAX}
              step={1}
              value={capacityInput ?? String(derivedCapacity)}
              onChange={(event) => setCapacityInput(event.target.value)}
              aria-invalid={capacityInvalid}
              className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary aria-invalid:border-error"
            />
          </label>
            {/* Outside the label so the input's accessible name stays "Capacity". */}
            {capacityInvalid ? (
              <span className="block text-sm text-error">{`Enter a whole number from 1 to ${ROOM_CAPACITY_MAX}.`}</span>
            ) : capacityOverridden ? (
              <span className="flex flex-wrap items-center gap-2 text-sm text-on-surface-variant">
                {`Overrides the derived ${derivedCapacity}.`}
                <button type="button" onClick={() => setCapacityInput(null)} className="text-primary underline">Use derived</button>
              </span>
            ) : (
              <span className="block text-sm text-on-surface-variant">Derived from room type and expansion. Type to override.</span>
            )}
          </div>
          <label className="flex items-center gap-3 sm:col-span-2">
            <input type="checkbox" checked={expanded} onChange={(event) => setExpanded(event.target.checked)} className="w-4 h-4 rounded border-outline-variant" />
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Expanded (+5 capacity)</span>
          </label>
          <AllowedSubjects value={allowedSubjects} onChange={setAllowedSubjects} roomType={roomType} />
          {error && <p role="alert" className="sm:col-span-2 text-sm text-error">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
          <button type="button" onClick={onClose} className="px-4 py-2">Cancel</button>
          {/* A disabled select is skipped by constraint validation, so `required`
              cannot stop an empty catalog from submitting a blank building. */}
          <button disabled={saving || !selected || capacityInvalid || (!room && (!normalizeRoomNumber(roomNumber) || codeTooLong))} className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60">{saving ? "Saving..." : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}

export default function RoomsPage() {
  const [rooms, setRooms] = useState<Room[]>([]);
  const [buildings, setBuildings] = useState<Building[]>([]);
  const [buildingsStatus, setBuildingsStatus] = useState<BuildingsStatus>("loading");
  const [buildingsError, setBuildingsError] = useState("");
  const [buildingsRetrying, setBuildingsRetrying] = useState(false);
  const buildingsRequest = useRef(0);
  // Rows from the last good read: an empty catalog is not data to keep on a failed re-read.
  const buildingCount = useRef(0);
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);
  const [editing, setEditing] = useState<Room | "new" | null>(null);
  const requestId = useRef(0);

  function loadRooms() {
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError("");
    // The catalog page reads inactive rooms too; the checkbox hides them.
    api.listRooms(true)
      .then((response) => {
        if (currentRequest !== requestId.current) return;
        setRooms(response.items);
        // Only a successful list read clears a failed load.
        setLoadFailed(false);
      })
      .catch((caught: unknown) => {
        if (currentRequest === requestId.current) {
          setError(caught instanceof ApiError ? caught.message : "Could not load rooms.");
          setLoadFailed(true);
        }
      })
      .finally(() => {
        if (currentRequest === requestId.current) setLoading(false);
      });
  }

  useEffect(loadRooms, []);

  // The room form can only offer buildings the catalog already holds, so a
  // failure here is kept apart from the rooms list and never read as "no buildings".
  function loadBuildings() {
    const currentRequest = ++buildingsRequest.current;
    setBuildingsRetrying(true);
    api.listBuildings()
      .then((response) => {
        if (currentRequest !== buildingsRequest.current) return;
        setBuildings(response.items);
        buildingCount.current = response.items.length;
        setBuildingsError("");
        setBuildingsStatus("ready");
      })
      .catch((caught: unknown) => {
        if (currentRequest !== buildingsRequest.current) return;
        setBuildingsError(caught instanceof ApiError ? caught.message : "Could not load buildings.");
        setBuildingsStatus((status) => (status === "ready" && buildingCount.current > 0 ? status : "failed"));
      })
      .finally(() => {
        if (currentRequest === buildingsRequest.current) setBuildingsRetrying(false);
      });
  }

  useEffect(loadBuildings, []);

  function retryAll() {
    loadRooms();
    loadBuildings();
  }

  // Rooms are never deleted: past schedules and mined rules name them.
  // Deactivating keeps a room out of scheduling; Activate undoes it.
  const [deactivating, setDeactivating] = useState<Room | null>(null);
  const [activating, setActivating] = useState<string | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  const [sort, toggleSort] = useSort<RoomSortKey>("room");
  async function deactivate(room: Room) {
    await api.deactivateRoom(room.room_code);
    loadRooms();
  }

  async function activate(room: Room) {
    setActivating(room.room_code);
    setError("");
    try {
      await api.updateRoom(room.room_code, { is_active: true });
      loadRooms();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not activate room.");
    } finally {
      setActivating(null);
    }
  }

  // Filters offer every canonical type, not only the ones already recorded, so
  // the set of filters does not change as rooms are added and removed.
  const types = ROOM_TYPES;
  // A failed first load has no records to show; a failed refresh keeps them.
  const loadBlocked = loadFailed && rooms.length === 0;
  const inactiveCount = rooms.filter((room) => !isActive(room)).length;
  const matching = filter === "all" ? rooms : rooms.filter((room) => room.room_type === filter);
  const filtered = matching.filter((room) => showInactive || isActive(room));
  const sorted = sortRows(filtered, sort, {
    room: byRoomCode,
    building: (a, b) => naturalCompare(a.building_name, b.building_name),
    type: (a, b) => a.room_type.localeCompare(b.room_type),
    capacity: (a, b) => a.capacity - b.capacity,
    status: (a, b) => Number(isActive(b)) - Number(isActive(a)),
  }, byRoomCode);
  const activeRooms = rooms.filter(isActive);
  const activeSeats = activeRooms.reduce((total, room) => total + room.capacity, 0);

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            <div className="flex flex-wrap justify-between items-end gap-4">
              <div>
                <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">Current catalog</p>
                <h1 className="font-headline-lg text-[32px] mt-1">Rooms Terminal</h1>
                <p className="text-on-surface-variant">Room capacity and type records from the Chronos server.</p>
                {rooms.length > 0 && (
                  <p className="mt-1 text-sm text-on-surface-variant">
                    {activeRooms.length} active ({activeSeats} seats) · {inactiveCount} inactive
                  </p>
                )}
              </div>
              <button onClick={() => setEditing("new")} className="flex items-center gap-2 bg-surface-tint text-white px-4 py-2.5 rounded-lg font-bold">
                <Plus className="w-4 h-4" /> Add room
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              <button onClick={() => setFilter("all")} className={`px-4 py-2 rounded-lg border ${filter === "all" ? "border-primary bg-primary/10 text-primary" : "border-outline-variant"}`}>All rooms</button>
              {types.map((type) => (
                <button key={type} onClick={() => setFilter(type)} className={`px-4 py-2 rounded-lg border ${filter === type ? "border-primary bg-primary/10 text-primary" : "border-outline-variant"}`}>{ROOM_TYPE_LABEL[type]}</button>
              ))}
              <div className="ml-auto self-center">
                <ShowInactiveToggle checked={showInactive} onChange={setShowInactive} count={inactiveCount} />
              </div>
            </div>
            {buildingsError && <LoadError message={buildingsError} retrying={buildingsRetrying} onRetry={loadBuildings} />}
            {error && !loadBlocked && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>}
            {loadBlocked ? (
              <LoadError message={error} retrying={loading} onRetry={retryAll} />
            ) : loading ? (
              <p className="text-on-surface-variant">Loading rooms...</p>
            ) : filtered.length === 0 ? (
              <p className="border border-outline-variant rounded-xl bg-surface-container-lowest p-10 text-center text-on-surface-variant">{rooms.length === 0 ? "No rooms found." : matching.length > 0 ? hiddenInactiveMessage("rooms") : "No rooms match this filter."}</p>
            ) : (
              <CatalogTable
                caption={`Rooms, ${sorted.length} shown. Column headings sort the list.`}
                headings={(
                  <>
                    <SortHeader label="Room" sortKey="room" sort={sort} onSort={toggleSort} />
                    <SortHeader label="Building" sortKey="building" sort={sort} onSort={toggleSort} className="hidden md:table-cell" />
                    <SortHeader label="Type" sortKey="type" sort={sort} onSort={toggleSort} />
                    <SortHeader label="Capacity" sortKey="capacity" sort={sort} onSort={toggleSort} className="text-right" />
                    <SortHeader label="Status" sortKey="status" sort={sort} onSort={toggleSort} />
                  </>
                )}
              >
                {sorted.map((room) => (
                  <CatalogRow key={room.room_code} active={isActive(room)}>
                    <td className="px-4 py-2 whitespace-nowrap">
                      <span className="font-medium text-on-surface">{room.room_code}</span>
                      {/* The building column is hidden on a phone; it folds under the code. */}
                      <span className="md:hidden block text-xs text-on-surface-variant">{room.building_name}</span>
                    </td>
                    <td className="hidden md:table-cell px-4 py-2 text-on-surface-variant">{room.building_name}</td>
                    <td className="px-4 py-2 whitespace-nowrap">
                      <span className="px-2 py-0.5 rounded bg-surface-container-highest text-xs font-label-md uppercase">{ROOM_TYPE_LABEL[room.room_type]}</span>
                      {room.expanded && (
                        <span className="ml-1.5 px-2 py-0.5 rounded bg-tertiary/10 text-tertiary text-xs font-label-md uppercase">Expanded</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">{room.capacity}</td>
                    <td className="px-4 py-2"><StatusPill active={isActive(room)} inactiveTitle="Not used for new schedules" /></td>
                    <RowActions>
                      <EditButton label={room.room_code} onClick={() => setEditing(room)} />
                      <RowActiveToggle
                        isActive={isActive(room)}
                        label={room.room_code}
                        activating={activating === room.room_code}
                        onDeactivate={() => setDeactivating(room)}
                        onActivate={() => void activate(room)}
                      />
                    </RowActions>
                  </CatalogRow>
                ))}
              </CatalogTable>
            )}
          </div>
        </main>
      </div>
      {editing && (
        <RoomForm
          buildings={buildings}
          buildingsStatus={buildingsStatus}
          buildingsStale={buildingsError !== ""}
          buildingsRetrying={buildingsRetrying}
          onRetryBuildings={loadBuildings}
          room={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            loadRooms();
          }}
        />
      )}
      {deactivating && (
        <ConfirmDeleteDialog
          title={`Deactivate room ${deactivating.room_code}?`}
          subtitle={deactivating.building_name}
          actionLabel="Deactivate"
          pendingLabel="Deactivating..."
          fallbackError="Could not deactivate room."
          onConfirm={() => deactivate(deactivating)}
          onClose={() => setDeactivating(null)}
        >
          <p>It will not be used for new schedules while inactive. Nothing is deleted: past schedules, history, and rules that name it are kept.</p>
          <p>Tick "Show inactive" to find it and activate it again.</p>
        </ConfirmDeleteDialog>
      )}
    </div>
  );
}
