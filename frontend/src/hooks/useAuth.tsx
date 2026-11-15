import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type User } from "../api/client";
import { subscribeUnauthorized } from "../state/appEvents";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /**
   * Set while a sign-out has not been confirmed by the server: "retry" when the
   * marker was saved and the next load retries it, "unsaved" when storage is
   * blocked and a reload would restore the session instead.
   */
  logoutPending: LogoutPending;
}

type LogoutPending = "retry" | "unsaved" | null;

// Survives reloads so a revocation that failed (API down) is retried before
// the HttpOnly cookie can silently restore the session on a shared workstation.
const LOGOUT_PENDING_KEY = "chronos.logoutPending";
const LOGOUT_TIMEOUT_MS = 10_000;

function readLogoutPending(): boolean {
  try {
    return localStorage.getItem(LOGOUT_PENDING_KEY) !== null;
  } catch {
    return false;
  }
}

/** Returns false when storage is blocked and the marker could not be written. */
function writeLogoutPending(pending: boolean): boolean {
  try {
    if (pending) localStorage.setItem(LOGOUT_PENDING_KEY, "1");
    else localStorage.removeItem(LOGOUT_PENDING_KEY);
    return true;
  } catch {
    return false;
  }
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [logoutPending, setLogoutPending] = useState<LogoutPending>(() => (readLogoutPending() ? "retry" : null));
  // Bumped on every login/logout so a revocation that settles late cannot
  // clear the marker of a newer, still unconfirmed sign-out.
  const authEpoch = useRef(0);

  useEffect(() => {
    let active = true;
    const unsubscribe = subscribeUnauthorized(() => setUser(null));
    if (readLogoutPending()) {
      // A pending revocation must be confirmed before the cookie session may be
      // restored. The user stays signed out and sees the login page right away;
      // the bounded retry only clears the marker once the server confirms it.
      setLoading(false);
      const epoch = authEpoch.current;
      api.logout(AbortSignal.timeout(LOGOUT_TIMEOUT_MS))
        .then(() => {
          if (epoch !== authEpoch.current) return;
          writeLogoutPending(false);
          if (active) setLogoutPending(null);
        })
        .catch(() => undefined);
    } else {
      api.me()
        .then((currentUser) => {
          if (active) setUser(currentUser);
        })
        .catch(() => {
          if (active) setUser(null);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  async function login(username: string, password: string) {
    const signedIn = await api.login(username, password);
    authEpoch.current += 1;
    // A fresh login replaces the cookie, so the old pending revocation no
    // longer applies to this browser.
    writeLogoutPending(false);
    setLogoutPending(null);
    setUser(signedIn);
  }

  async function logout() {
    // The local session is dropped whether or not the server acknowledges the
    // revocation, so a backend that is down cannot strand the user in a
    // signed-in interface. An unconfirmed revocation is remembered and retried
    // on the next load before any session restore.
    const epoch = ++authEpoch.current;
    setLogoutPending(writeLogoutPending(true) ? "retry" : "unsaved");
    setUser(null);
    await api.logout(AbortSignal.timeout(LOGOUT_TIMEOUT_MS));
    if (epoch !== authEpoch.current) return;
    writeLogoutPending(false);
    setLogoutPending(null);
  }

  return <AuthContext.Provider value={{ user, loading, login, logout, logoutPending }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider.");
  return value;
}
