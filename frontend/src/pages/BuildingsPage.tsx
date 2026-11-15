import { useEffect, useRef, useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import { ApiError, api, type Building } from "../api/client";
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

type BuildingSortKey = "name" | "code" | "status";
const byName = (a: Building, b: Building) => naturalCompare(a.building_name, b.building_name);
import ConfirmDeleteDialog from "../components/modals/ConfirmDeleteDialog";
import Modal from "../components/modals/Modal";
import LoadError from "../components/LoadError";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";

function BuildingForm({ building, onClose, onSaved }: { building?: Building; onClose: () => void; onSaved: () => void }) {
  const [buildingName, setBuildingName] = useState(building?.building_name ?? "");
  const [buildingCode, setBuildingCode] = useState(building?.building_code ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const values = {
        building_name: buildingName.trim(),
        building_code: buildingCode.trim().toUpperCase(),
      };
      if (building) {
        await api.updateBuilding(building.building_id, values);
      } else {
        await api.createBuilding(values);
      }
      onSaved();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not save building.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={building ? `Edit ${building.building_code}` : "Add building"} onClose={onClose}>
      <form onSubmit={submit} className="flex flex-col min-h-0">
        <div className="p-6 grid sm:grid-cols-2 gap-5 overflow-y-auto">
          <label className="block sm:col-span-2 space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Building name</span>
            <input value={buildingName} onChange={(event) => setBuildingName(event.target.value)} required maxLength={100} className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary" />
          </label>
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Building code</span>
            <input value={buildingCode} onChange={(event) => setBuildingCode(event.target.value)} required maxLength={20} className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest uppercase outline-none focus:border-primary" />
          </label>
          {error && <p role="alert" className="sm:col-span-2 text-sm text-error">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
          <button type="button" onClick={onClose} className="px-4 py-2">Cancel</button>
          <button disabled={saving} className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60">{saving ? "Saving..." : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}

export default function BuildingsPage() {
  const [buildings, setBuildings] = useState<Building[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);
  const [editing, setEditing] = useState<Building | "new" | null>(null);
  const requestId = useRef(0);

  function loadBuildings() {
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError("");
    // The catalog page reads inactive buildings too; the checkbox hides them.
    api.listBuildings(true)
      .then((response) => {
        if (currentRequest !== requestId.current) return;
        setBuildings(response.items);
        // Only a successful list read clears a failed load.
        setLoadFailed(false);
      })
      .catch((caught: unknown) => {
        if (currentRequest === requestId.current) {
          setError(caught instanceof ApiError ? caught.message : "Could not load buildings.");
          setLoadFailed(true);
        }
      })
      .finally(() => {
        if (currentRequest === requestId.current) setLoading(false);
      });
  }

  useEffect(loadBuildings, []);

  // A failed first load has no records to show; a failed refresh keeps them.
  const loadBlocked = loadFailed && buildings.length === 0;

  // Buildings are never deleted: their rooms' history names them.
  // Deactivating one stops new rooms going in; Activate undoes it.
  const [deactivating, setDeactivating] = useState<Building | null>(null);
  const [activating, setActivating] = useState<string | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  const [sort, toggleSort] = useSort<BuildingSortKey>("name");
  async function deactivate(building: Building) {
    await api.deactivateBuilding(building.building_id);
    loadBuildings();
  }

  async function activate(building: Building) {
    setActivating(building.building_id);
    setError("");
    try {
      await api.updateBuilding(building.building_id, { is_active: true });
      loadBuildings();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not activate building.");
    } finally {
      setActivating(null);
    }
  }

  const inactiveCount = buildings.filter((building) => !isActive(building)).length;
  const visible = buildings.filter((building) => showInactive || isActive(building));
  const sorted = sortRows(visible, sort, {
    name: byName,
    code: (a, b) => naturalCompare(a.building_code, b.building_code),
    status: (a, b) => Number(isActive(b)) - Number(isActive(a)),
  }, byName);

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
                <h1 className="font-headline-lg text-[32px] mt-1">Buildings</h1>
                <p className="text-on-surface-variant">Every room belongs to a building recorded here.</p>
              </div>
              <button onClick={() => setEditing("new")} className="flex items-center gap-2 bg-surface-tint text-white px-4 py-2.5 rounded-lg font-bold">
                <Plus className="w-4 h-4" /> Add building
              </button>
            </div>
            <ShowInactiveToggle checked={showInactive} onChange={setShowInactive} count={inactiveCount} />
            {error && !loadBlocked && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>}
            {loadBlocked ? (
              <LoadError message={error} retrying={loading} onRetry={loadBuildings} />
            ) : loading ? (
              <p className="text-on-surface-variant">Loading buildings...</p>
            ) : visible.length === 0 ? (
              <p className="border border-outline-variant rounded-xl bg-surface-container-lowest p-10 text-center text-on-surface-variant">
                {buildings.length === 0 ? "No buildings yet. Add one before recording rooms." : hiddenInactiveMessage("buildings")}
              </p>
            ) : (
              <CatalogTable
                caption={`Buildings, ${sorted.length} shown. Column headings sort the list.`}
                headings={(
                  <>
                    <SortHeader label="Name" sortKey="name" sort={sort} onSort={toggleSort} />
                    <SortHeader label="Code" sortKey="code" sort={sort} onSort={toggleSort} />
                    <SortHeader label="Status" sortKey="status" sort={sort} onSort={toggleSort} />
                  </>
                )}
              >
                {sorted.map((building) => (
                  <CatalogRow key={building.building_id} active={isActive(building)}>
                    <td className="px-4 py-2 font-medium text-on-surface min-w-[10rem]">{building.building_name}</td>
                    <td className="px-4 py-2 whitespace-nowrap">{building.building_code}</td>
                    <td className="px-4 py-2"><StatusPill active={isActive(building)} inactiveTitle="No new rooms can be added" /></td>
                    <RowActions>
                      <EditButton label={building.building_code} onClick={() => setEditing(building)} />
                      <RowActiveToggle
                        isActive={isActive(building)}
                        label={building.building_code}
                        activating={activating === building.building_id}
                        onDeactivate={() => setDeactivating(building)}
                        onActivate={() => void activate(building)}
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
        <BuildingForm
          building={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            loadBuildings();
          }}
        />
      )}
      {deactivating && (
        <ConfirmDeleteDialog
          title={`Deactivate building ${deactivating.building_code}?`}
          subtitle={deactivating.building_name}
          actionLabel="Deactivate"
          pendingLabel="Deactivating..."
          fallbackError="Could not deactivate building."
          onConfirm={() => deactivate(deactivating)}
          onClose={() => setDeactivating(null)}
        >
          <p>No new rooms can be added to it while inactive. Nothing is deleted: its rooms and their history are kept.</p>
          <p>A building that still has active rooms cannot be deactivated; deactivate or move those rooms first.</p>
        </ConfirmDeleteDialog>
      )}
    </div>
  );
}
