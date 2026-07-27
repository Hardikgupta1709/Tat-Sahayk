import { Navigate, Route, Routes } from "react-router";
import { Loader2 } from "lucide-react";
import { Toaster } from "react-hot-toast";

import Layout from "./components/Layout.jsx";
import useAuthUser from "./hooks/useAuthUser.js";
import AdminDashboard from "./pages/AdminDashboard.jsx";
import AlertsPage from "./pages/AlertsPage.jsx";
import CreateReport from "./pages/CreateReport.jsx";
import HomePage from "./pages/HomePage.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import MapPage from "./pages/MapPage.jsx";
import ProfilePage from "./pages/ProfilePage.jsx";
import SignupPage from "./pages/SignupPage.jsx";


const ProtectedRoute = ({
  isAuthenticated,
  children,
}) => (
  isAuthenticated
    ? <Layout>{children}</Layout>
    : <Navigate to="/login" replace />
);


const AdminRoute = ({
  isAuthenticated,
  isAdmin,
  children,
}) => (
  isAuthenticated && isAdmin
    ? <Layout>{children}</Layout>
    : <Navigate to="/" replace />
);


const PublicLayout = ({ children }) => (
  <Layout>{children}</Layout>
);


const App = () => {
  const { isLoading, authUser } = useAuthUser();
  const isAuthenticated = Boolean(authUser);
  const isAdmin = authUser?.role === "admin";

  if (isLoading) {
    return (
      <div className="w-full min-h-screen bg-slate-50 dark:bg-black flex items-center justify-center">
        <Loader2
          className="animate-spin text-sky-500"
          size={40}
        />
      </div>
    );
  }

  return (
    <div className="h-screen">
      <Toaster
        position="top-center"
        toastOptions={{
          duration: 6000,
          className: "",
          style: {
            padding: "16px 20px",
            borderRadius: "10px",
            fontSize: "14px",
            fontWeight: "500",
            boxShadow: "0 8px 24px rgba(0, 0, 0, 0.2)",
            maxWidth: "500px",
            minWidth: "320px",
            background: "white",
            color: "#1f2937",
          },
          success: {
            duration: 4000,
            icon: "✓",
            style: {
              background: "white",
              color: "#1f2937",
              borderLeft: "4px solid #10b981",
              boxShadow: "0 8px 24px rgba(16, 185, 129, 0.2)",
            },
          },
          error: {
            duration: 8000,
            icon: "✕",
            style: {
              background: "white",
              color: "#1f2937",
              borderLeft: "4px solid #ef4444",
              boxShadow: "0 8px 24px rgba(239, 68, 68, 0.2)",
            },
          },
          loading: {
            duration: Infinity,
            icon: "⏳",
            style: {
              background: "white",
              color: "#1f2937",
              borderLeft: "4px solid #3b82f6",
              boxShadow: "0 8px 24px rgba(59, 130, 246, 0.2)",
            },
          },
        }}
        containerStyle={{
          top: 80,
          zIndex: 99999,
        }}
      />

      <Routes>
        <Route
          path="/"
          element={(
            <PublicLayout>
              <HomePage />
            </PublicLayout>
          )}
        />

        <Route
          path="/map"
          element={(
            <ProtectedRoute isAuthenticated={isAuthenticated}>
              <MapPage />
            </ProtectedRoute>
          )}
        />

        <Route
          path="/alerts"
          element={(
            <PublicLayout>
              <AlertsPage />
            </PublicLayout>
          )}
        />

        <Route
          path="/profile"
          element={(
            <ProtectedRoute isAuthenticated={isAuthenticated}>
              <ProfilePage />
            </ProtectedRoute>
          )}
        />

        <Route
          path="/new"
          element={(
            <ProtectedRoute isAuthenticated={isAuthenticated}>
              <CreateReport />
            </ProtectedRoute>
          )}
        />

        <Route
          path="/admin"
          element={(
            <AdminRoute
              isAuthenticated={isAuthenticated}
              isAdmin={isAdmin}
            >
              <AdminDashboard />
            </AdminRoute>
          )}
        />

        <Route
          path="/login"
          element={(
            isAuthenticated
              ? <Navigate to="/" replace />
              : <LoginPage />
          )}
        />

        <Route
          path="/signup"
          element={(
            isAuthenticated
              ? <Navigate to="/" replace />
              : <SignupPage />
          )}
        />

        <Route
          path="*"
          element={<Navigate to="/" replace />}
        />
      </Routes>
    </div>
  );
};


export default App;