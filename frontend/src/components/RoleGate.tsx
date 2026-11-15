import { Link, Navigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { isAdmin } from "../hooks/useRole";
import Sidebar from "./layout/Sidebar";
import TopBar from "./layout/TopBar";

interface RoleGateProps {
  children: React.ReactNode;
  requireAdmin?: boolean;
}

export default function RoleGate({ children, requireAdmin = false }: RoleGateProps) {
  const { user, loading } = useAuth();

  if (loading) return null;

  if (!user) return <Navigate to="/" replace />;

  if (requireAdmin && !isAdmin(user)) {
    // Keep the app shell so the user can navigate away. Only the page is
    // withheld, so its admin content is never rendered or fetched.
    return (
      <div className="bg-background text-on-surface font-body-md min-h-screen flex">
        <Sidebar compactOnMobile />
        <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
          <TopBar />
          <main id="main-content" className="flex-1 grid place-items-center p-4 sm:p-6 md:p-8 text-on-surface-variant">
            <div className="text-center space-y-3">
              <p className="font-headline-md text-headline-md text-on-surface">Access restricted</p>
              <p className="font-body-md text-body-md">
                This page requires Administrator privileges.
              </p>
              <p className="font-body-sm text-body-sm text-on-surface-variant">
                Contact a Department Administrator if you need access.
              </p>
              <Link to="/dashboard" className="inline-block text-primary font-medium hover:underline">Back to dashboard</Link>
            </div>
          </main>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
