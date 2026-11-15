import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import { BlockGenerationProvider } from "./state/BlockGeneration";
import BuildingsPage from "./pages/BuildingsPage";
import HistoryPage from "./pages/HistoryPage";
import JobHistoryPage from "./pages/JobHistoryPage";
import HistoricalUploadPage from "./pages/HistoricalUploadPage";
import CommitteeTimetablePage from "./pages/CommitteeTimetablePage";
import DashboardPage from "./pages/DashboardPage";
import LoginPage from "./pages/LoginPage";
import MasterSchedulePage from "./pages/MasterSchedulePage";
import RoomsPage from "./pages/RoomsPage";
import RosterPage from "./pages/RosterPage";
import RuleSetsPage from "./pages/RuleSetsPage";
import GAConfigurationPage from "./pages/GAConfigurationPage";
import MatchedEvaluationPage from "./pages/MatchedEvaluationPage";
import SchedulePage from "./pages/SchedulePage";
import SubjectsPage from "./pages/SubjectsPage";
import UsersPage from "./pages/UsersPage";
import RoleGate from "./components/RoleGate";

function SessionGate({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return <div className="h-screen grid place-items-center bg-background text-on-surface-variant">Restoring local session...</div>;
  }
  return user ? children : <Navigate to="/" replace />;
}

function LoginRoute() {
  const { user, loading } = useAuth();
  if (loading) return null;
  return user ? <Navigate to="/dashboard" replace /> : <LoginPage />;
}

export default function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <AuthProvider>
        <BlockGenerationProvider>
        <Routes>
          <Route path="/" element={<LoginRoute />} />
          <Route path="/dashboard" element={<SessionGate><DashboardPage /></SessionGate>} />
          <Route path="/schedule" element={<SessionGate><SchedulePage /></SessionGate>} />
          <Route path="/master-schedule" element={<SessionGate><MasterSchedulePage /></SessionGate>} />
          <Route path="/timetable" element={<SessionGate><CommitteeTimetablePage /></SessionGate>} />
          <Route path="/committee-timetable" element={<Navigate to="/timetable" replace />} />
          <Route path="/roster" element={<SessionGate><RosterPage /></SessionGate>} />
          <Route path="/subjects" element={<SessionGate><SubjectsPage /></SessionGate>} />
          <Route path="/rooms" element={<SessionGate><RoomsPage /></SessionGate>} />
          <Route path="/buildings" element={<SessionGate><BuildingsPage /></SessionGate>} />
          <Route path="/job-history" element={<SessionGate><JobHistoryPage /></SessionGate>} />
          <Route path="/rule-sets" element={<SessionGate><RoleGate requireAdmin><RuleSetsPage /></RoleGate></SessionGate>} />
          <Route path="/history" element={<SessionGate><RoleGate requireAdmin><HistoryPage /></RoleGate></SessionGate>} />
          <Route path="/historical-upload" element={<SessionGate><RoleGate requireAdmin><HistoricalUploadPage /></RoleGate></SessionGate>} />
          <Route path="/ga-configuration" element={<SessionGate><RoleGate requireAdmin><GAConfigurationPage /></RoleGate></SessionGate>} />
          <Route path="/users" element={<SessionGate><RoleGate requireAdmin><UsersPage /></RoleGate></SessionGate>} />
          <Route path="/evaluations" element={<SessionGate><RoleGate requireAdmin><MatchedEvaluationPage /></RoleGate></SessionGate>} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
        </BlockGenerationProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
