import { useEffect, useRef, useState, type FormEvent } from "react";
import { KeyRound, Pencil, Plus } from "lucide-react";
import { ApiError, api, type ManagedUser, type User } from "../api/client";
import Modal from "../components/modals/Modal";
import PasswordModal from "../components/modals/PasswordModal";
import LoadError from "../components/LoadError";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";
import { useAuth } from "../hooks/useAuth";
import { displayRole } from "../hooks/useRole";

type UserChange = Partial<Pick<ManagedUser, "role" | "is_active">>;

const ROLES: User["role"][] = ["scheduling_committee", "department_administrator"];

const SOLE_ADMIN_HINT = "The only active Administrator can't be demoted or deactivated. Make another account an Administrator first.";

// The API's 422 names only the field, so the username rules are spelled out here.
const USERNAME_RULES =
  "Usernames can't have leading/trailing or special spaces, invisible characters, or decomposed accents. Retype it rather than pasting.";

// The browser's minLength counts UTF-16 units, the server counts characters,
// so a short emoji password can pass here and still be refused.
const PASSWORD_RULES = "Passwords need at least 8 characters; an emoji counts as one.";

function isFieldValidationError(caught: unknown, field: string): boolean {
  if (!(caught instanceof ApiError) || caught.code !== "VALIDATION_ERROR") return false;
  const errors = caught.details.errors;
  return Array.isArray(errors) && errors.some((entry: { field?: unknown }) => entry?.field === field);
}

function UserForm({ onClose, onSaved }: { onClose: () => void; onSaved: (created: ManagedUser) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<User["role"]>("scheduling_committee");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      onSaved(await api.createUser({ username: username.trim(), password, role }));
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError && caught.code === "DUPLICATE_RESOURCE"
          ? `The username ${username.trim()} is already taken.`
          : isFieldValidationError(caught, "body.username")
            ? USERNAME_RULES
            : isFieldValidationError(caught, "body.password")
              ? PASSWORD_RULES
              : caught instanceof ApiError ? caught.message : "Could not create the account.",
      );
    } finally {
      setSaving(false);
    }
  }

  // The server may still commit a request in flight, so the form can't be
  // dismissed (and a different draft opened) until it settles.
  const close = () => {
    if (!saving) onClose();
  };
  const field = "w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary";
  return (
    <Modal title="Add local account" onClose={close}>
      <form onSubmit={submit} className="flex flex-col min-h-0">
        <div className="p-6 grid gap-5 overflow-y-auto">
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Username</span>
            <input value={username} onChange={(event) => setUsername(event.target.value)} required maxLength={100} autoComplete="off" className={field} />
          </label>
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Initial password</span>
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={8} maxLength={1024} autoComplete="new-password" className={field} />
          </label>
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Role</span>
            <select value={role} onChange={(event) => setRole(event.target.value as User["role"])} className={field}>
              {ROLES.map((value) => <option key={value} value={value}>{displayRole(value)}</option>)}
            </select>
          </label>
          {error && <p role="alert" className="text-sm text-error">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
          <button type="button" onClick={close} aria-disabled={saving} className="px-4 py-2 aria-disabled:opacity-60">Cancel</button>
          <button disabled={saving} className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60">{saving ? "Saving..." : "Create account"}</button>
        </div>
      </form>
    </Modal>
  );
}

function RenameForm({
  account,
  onClose,
  onSaved,
}: {
  account: ManagedUser;
  onClose: () => void;
  onSaved: (updated: ManagedUser) => void;
}) {
  const [username, setUsername] = useState(account.username);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = username.trim();
    if (next === account.username) {
      onClose();
      return;
    }
    setSaving(true);
    setError("");
    try {
      onSaved(await api.updateUser(account.user_id, { username: next }));
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError && caught.code === "DUPLICATE_RESOURCE"
          ? `The username ${next} is already taken.`
          : isFieldValidationError(caught, "body.username")
            ? USERNAME_RULES
            : caught instanceof ApiError ? caught.message : "Could not rename the account.",
      );
    } finally {
      setSaving(false);
    }
  }

  const close = () => {
    if (!saving) onClose();
  };
  return (
    <Modal title={`Rename ${account.username}`} onClose={close}>
      <form onSubmit={submit} className="flex flex-col min-h-0">
        <div className="p-6 grid gap-5 overflow-y-auto">
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">New username</span>
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
              maxLength={100}
              autoComplete="off"
              className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary"
            />
          </label>
          <p className="text-sm text-on-surface-variant">The account signs in with the new name from now on; its password and role stay the same.</p>
          {error && <p role="alert" className="text-sm text-error">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
          <button type="button" onClick={close} aria-disabled={saving} className="px-4 py-2 aria-disabled:opacity-60">Cancel</button>
          <button disabled={saving || !username.trim()} className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60">{saving ? "Saving..." : "Rename"}</button>
        </div>
      </form>
    </Modal>
  );
}

export default function UsersPage() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);
  const [creating, setCreating] = useState(false);
  const [settingPasswordFor, setSettingPasswordFor] = useState<ManagedUser | null>(null);
  const [renaming, setRenaming] = useState<ManagedUser | null>(null);
  // Rows with a PATCH in flight. Their controls are aria-disabled and guarded
  // rather than disabled, so the control keeping focus does not drop it.
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  // A change to the signed-in account that would end its admin access waits
  // here for an explicit confirmation.
  const [confirming, setConfirming] = useState<{ userId: string; data: UserChange } | null>(null);
  const requestId = useRef(0);
  // Whether any list read has ever succeeded, so one added row never stands in for the list.
  const everLoaded = useRef(false);
  // The control that opened the confirm row gets focus back when it closes.
  const trigger = useRef<HTMLElement | null>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);
  const confirmGroup = useRef<HTMLDivElement>(null);

  // Scroll the whole group (warning and actions) into view, then focus
  // without letting the focus scroll stop at Confirm and hide the warning.
  useEffect(() => {
    if (!confirming) return;
    confirmGroup.current?.scrollIntoView?.({ block: "nearest" });
    confirmButton.current?.focus({ preventScroll: true });
  }, [confirming]);

  // `quiet` keeps a resync after a refused change from replacing the
  // server's reason with a load error.
  function loadUsers({ quiet = false } = {}) {
    const currentRequest = ++requestId.current;
    setLoading(true);
    return api.listUsers()
      .then((response) => {
        if (currentRequest !== requestId.current) return;
        setUsers(response.items);
        everLoaded.current = true;
        // Only a successful list read clears a failed load.
        setLoadFailed(false);
        // A quiet resync must not clear the refusal it follows.
        if (!quiet) setError("");
      })
      .catch((caught: unknown) => {
        if (currentRequest === requestId.current && !quiet) {
          setError(caught instanceof ApiError ? caught.message : "Could not load accounts.");
          setLoadFailed(true);
        }
      })
      .finally(() => {
        if (currentRequest === requestId.current) setLoading(false);
      });
  }

  useEffect(() => {
    void loadUsers();
  }, []);

  function setRowPending(userId: string, inFlight: boolean) {
    setPending((current) => {
      const next = new Set(current);
      if (inFlight) next.add(userId);
      else next.delete(userId);
      return next;
    });
  }

  function request(target: ManagedUser, data: UserChange, control: HTMLElement) {
    if (pending.has(target.user_id)) return;
    trigger.current = control;
    const removesOwnAccess =
      target.user_id === currentUser?.user_id &&
      (data.is_active === false || data.role === "scheduling_committee");
    if (removesOwnAccess) {
      setConfirming({ userId: target.user_id, data });
      return;
    }
    void change(target, data);
  }

  function cancelConfirm() {
    setConfirming(null);
    trigger.current?.focus();
  }

  async function change(target: ManagedUser, data: UserChange) {
    // Captured per request, so a later click elsewhere can't redirect it.
    const control = trigger.current;
    setConfirming(null);
    setError("");
    setRowPending(target.user_id, true);
    try {
      const updated = await api.updateUser(target.user_id, data);
      // The session's role is read once at sign-in; reload so a change to the
      // signed-in account takes effect in the UI immediately.
      if (target.user_id === currentUser?.user_id) {
        window.location.reload();
        return;
      }
      // The response is the row's new state, so the row is right even if a
      // list reload fails. The reload bumps requestId, which drops any list
      // served before this PATCH, while keeping accounts created meanwhile.
      setUsers((current) => current.map((row) => (row.user_id === updated.user_id ? updated : row)));
      void loadUsers({ quiet: true });
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not update the account.");
      await loadUsers({ quiet: true });
    } finally {
      setRowPending(target.user_id, false);
      // Only reclaim focus nobody else holds: a modal opened meanwhile, or
      // another row, keeps it.
      const active = document.activeElement;
      if (control && (active === control || active === document.body || active === null)) control.focus();
    }
  }

  // The UI mirrors the server's LAST_ACTIVE_ADMINISTRATOR rule; the server
  // still refuses if another tab removed an administrator meanwhile.
  const activeAdminCount = users.filter((row) => row.is_active && row.role === "department_administrator").length;

  const confirmingAccount = users.find((row) => row.user_id === confirming?.userId);

  // A failed first load has no records to show; a failed refresh keeps them.
  const loadBlocked = loadFailed && users.length === 0;

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            <div className="flex flex-wrap justify-between items-end gap-4">
              <div>
                <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">Local accounts</p>
                <h1 className="font-headline-lg text-[32px] mt-1">Users</h1>
                <p className="text-on-surface-variant">At least one active Administrator must always remain.</p>
              </div>
              <button onClick={() => setCreating(true)} className="flex items-center gap-2 bg-surface-tint text-white px-4 py-2.5 rounded-lg font-bold">
                <Plus className="w-4 h-4" /> Add account
              </button>
            </div>
            {error && !loadBlocked && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>}
            {loadBlocked ? (
              <LoadError message={error} retrying={loading} onRetry={() => void loadUsers()} />
            ) : loading && users.length === 0 ? (
              <p className="text-on-surface-variant">Loading accounts...</p>
            ) : (
              <div className="overflow-x-auto border border-outline-variant rounded-xl bg-surface-container-lowest">
                <table className="w-full text-left text-sm">
                  <thead className="text-[11px] uppercase text-on-surface-variant border-b border-outline-variant">
                    <tr>
                      <th className="px-4 py-3">Username</th>
                      <th className="px-4 py-3">Role</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="relative px-4 py-3"><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((account) => {
                      const busy = pending.has(account.user_id);
                      const isSelf = account.user_id === currentUser?.user_id;
                      const soleAdmin = account.is_active && account.role === "department_administrator" && activeAdminCount === 1;
                      return (
                      <tr key={account.user_id} className="border-b border-outline-variant/60 last:border-0">
                        <td className="px-4 py-3 font-medium">
                          {account.username}
                          {isSelf && <span className="ml-1.5 text-xs font-normal text-on-surface-variant">(you)</span>}
                        </td>
                        <td className="px-4 py-3">
                          <select
                            aria-label={`Role for ${account.username}`}
                            value={account.role}
                            aria-disabled={busy}
                            disabled={soleAdmin}
                            title={soleAdmin ? SOLE_ADMIN_HINT : undefined}
                            onChange={(event) => request(account, { role: event.target.value as User["role"] }, event.currentTarget)}
                            className="border border-outline-variant rounded px-2 py-1.5 text-base md:text-sm bg-surface-container-lowest aria-disabled:opacity-60 disabled:opacity-60 disabled:cursor-not-allowed"
                          >
                            {ROLES.map((value) => <option key={value} value={value}>{displayRole(value)}</option>)}
                          </select>
                        </td>
                        <td className="px-4 py-3">
                          {account.is_active ? "Active" : "Deactivated"}
                          {soleAdmin && <span className="block text-xs text-on-surface-variant">Only active Administrator</span>}
                        </td>
                        <td className="px-4 py-3 text-right space-x-2">
                          <button
                            aria-label={`Rename ${account.username}`}
                            aria-disabled={busy}
                            onClick={() => { if (!busy) setRenaming(account); }}
                            className="px-3 py-1.5 rounded border border-outline-variant text-xs hover:border-primary aria-disabled:opacity-60"
                          >
                            <Pencil className="inline w-3 h-3 mr-1" aria-hidden="true" />
                            Rename
                          </button>
                          {/* Your own password is changed from the top bar, with the current one. */}
                          {!isSelf && (
                            <button
                              aria-label={`Set password for ${account.username}`}
                              onClick={() => setSettingPasswordFor(account)}
                              className="px-3 py-1.5 rounded border border-outline-variant text-xs hover:border-primary"
                            >
                              <KeyRound className="inline w-3 h-3 mr-1" aria-hidden="true" />
                              Set password
                            </button>
                          )}
                          <button
                            aria-disabled={busy}
                            disabled={soleAdmin}
                            title={soleAdmin ? SOLE_ADMIN_HINT : undefined}
                            onClick={(event) => request(account, { is_active: !account.is_active }, event.currentTarget)}
                            className="px-3 py-1.5 rounded border border-outline-variant text-xs hover:border-primary aria-disabled:opacity-60 disabled:opacity-60 disabled:cursor-not-allowed disabled:hover:border-outline-variant"
                          >
                            {account.is_active ? `Deactivate ${account.username}` : `Reactivate ${account.username}`}
                          </button>
                        </td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {confirmingAccount && confirming && (
              <div ref={confirmGroup} role="group" aria-label="Confirm change to your own account" className="flex flex-wrap items-center justify-end gap-3 border border-outline-variant rounded-xl bg-error-container/20 px-4 py-3">
                <p role="alert" className="basis-full sm:flex-1 min-w-0">
                  {confirming.data.is_active === false
                    ? "Deactivating your own account signs you out immediately."
                    : "Changing your own role to Scheduler removes your access to administration."}
                </p>
                <button type="button" onClick={cancelConfirm} className="px-3 py-1.5 text-xs">Cancel</button>
                <button ref={confirmButton} type="button" onClick={() => void change(confirmingAccount, confirming.data)} className="px-3 py-1.5 rounded bg-error text-on-error text-xs font-bold">
                  Confirm
                </button>
              </div>
            )}
          </div>
        </main>
      </div>
      {settingPasswordFor && (
        <PasswordModal
          title={`Set password for ${settingPasswordFor.username}`}
          submit={(_current, next) => api.resetUserPassword(settingPasswordFor.user_id, next)}
          onClose={() => setSettingPasswordFor(null)}
        />
      )}
      {renaming && (
        <RenameForm
          account={renaming}
          onClose={() => setRenaming(null)}
          onSaved={(updated) => {
            setRenaming(null);
            // The signed-in name is read once at sign-in, as with a role change.
            if (updated.user_id === currentUser?.user_id) {
              window.location.reload();
              return;
            }
            setUsers((current) => current.map((row) => (row.user_id === updated.user_id ? updated : row)));
            void loadUsers({ quiet: true });
          }}
        />
      )}
      {creating && (
        <UserForm
          onClose={() => setCreating(false)}
          onSaved={(created) => {
            setCreating(false);
            // The response is the accepted account, so it shows even if the
            // revalidating reload fails.
            if (everLoaded.current) setUsers((current) => [...current.filter((row) => row.user_id !== created.user_id), created]);
            void loadUsers();
          }}
        />
      )}
    </div>
  );
}
