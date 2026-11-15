import { useState } from "react";
import { createPortal } from "react-dom";
import { KeyRound, LogOut, UserCircle2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useActiveTerm } from "../../hooks/useActiveTerm";
import { useAuth } from "../../hooks/useAuth";
import { displayRole } from "../../hooks/useRole";
import { api } from "../../api/client";
import PasswordModal from "../modals/PasswordModal";

export default function TopBar() {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { term, loading } = useActiveTerm();
  const [changingPassword, setChangingPassword] = useState(false);

  async function handleLogout() {
    try {
      await logout();
    } catch {
      // `logout` clears the local session before it rethrows, so a failed
      // revocation call is not actionable here and must not block the redirect.
    }
    navigate("/", { replace: true });
  }

  return (
    <header className="min-h-[64px] shrink-0 bg-surface-bright border-b border-outline-variant flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-2 px-5 md:px-8 z-40">
      <div className="min-w-0">
        <span className="font-headline-md text-[18px] text-primary tracking-tight">Chronos System</span>
        <p className="font-label-md text-[11px] text-on-surface-variant uppercase tracking-wider truncate">
          {loading ? "Loading active term" : term ? `Term ${term.term_number}, AY ${term.academic_year}` : "No active term selected"}
        </p>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <div className="hidden sm:flex items-center gap-2 text-right">
          <UserCircle2 className="w-5 h-5 text-primary" />
          <div>
            <p className="text-[14px] font-medium text-on-surface">{user?.username}</p>
            <p className="text-[12px] uppercase tracking-wider text-on-surface-variant">{user ? displayRole(user.role) : ""}</p>
          </div>
        </div>
        <button onClick={() => setChangingPassword(true)} aria-label="Change password" title="Change password" className="flex items-center gap-2 px-3 py-2 border border-outline-variant rounded text-[11px] font-label-md uppercase text-on-surface-variant hover:text-primary hover:border-primary transition-colors">
          <KeyRound className="w-4 h-4" />
          {/* Icon only on phones so Logout stays inside a 320px viewport. */}
          <span className="hidden sm:inline">Change password</span>
        </button>
        <button onClick={handleLogout} className="flex items-center gap-2 px-3 py-2 border border-outline-variant rounded text-[11px] font-label-md uppercase text-on-surface-variant hover:text-error hover:border-error transition-colors">
          <LogOut className="w-4 h-4" />
          Logout
        </button>
      </div>
      {/* The header is z-40 and so is a stacking context: a dialog inside it would sit under the z-50 sidebar rail, and inside anything the open menu marks inert. */}
      {changingPassword &&
        createPortal(
          <PasswordModal
            title="Change password"
            askCurrent
            submit={(current, next) => api.changePassword(current, next)}
            onClose={() => setChangingPassword(false)}
          />,
          document.body,
        )}
    </header>
  );
}
