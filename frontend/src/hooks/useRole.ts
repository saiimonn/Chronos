import { useAuth } from "./useAuth";
import type { User } from "../api/client";

export type DisplayRole = "Scheduler" | "Administrator";

const ROLE_DISPLAY: Record<User["role"], DisplayRole> = {
  scheduling_committee: "Scheduler",
  department_administrator: "Administrator",
};

export function displayRole(role: User["role"]): DisplayRole {
  return ROLE_DISPLAY[role];
}

export function isAdmin(user: User | null): boolean {
  return user?.role === "department_administrator";
}

export function useIsAdmin(): boolean {
  const { user } = useAuth();
  return isAdmin(user);
}
