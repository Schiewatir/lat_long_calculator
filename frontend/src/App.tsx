import { Center, Loader } from '@mantine/core'
import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'
import { RequireAuth } from './auth/guards'
import { AppLayout } from './components/AppLayout'
import { ChangePasswordPage } from './pages/ChangePasswordPage'
import { JobsPage } from './pages/JobsPage'
import { LoginPage } from './pages/LoginPage'

const JobPage = lazy(() => import('./pages/JobPage').then((m) => ({ default: m.JobPage })))
const NewJobPage = lazy(() => import('./pages/NewJobPage').then((m) => ({ default: m.NewJobPage })))
const DistancePage = lazy(() => import('./pages/DistancePage').then((m) => ({ default: m.DistancePage })))
const AdminUsersPage = lazy(() => import('./pages/AdminUsersPage').then((m) => ({ default: m.AdminUsersPage })))
const AdminScoringPage = lazy(() => import('./pages/AdminScoringPage').then((m) => ({ default: m.AdminScoringPage })))

export default function App() {
  return (
    <Suspense fallback={<Center h="50vh"><Loader /></Center>}>
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/change-password"
        element={
          <RequireAuth>
            <ChangePasswordPage />
          </RequireAuth>
        }
      />
      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<JobsPage />} />
        <Route path="/jobs/new" element={<NewJobPage />} />
        <Route path="/jobs/:jobId" element={<JobPage />} />
        <Route path="/distance" element={<DistancePage />} />
        <Route
          path="/admin/users"
          element={
            <RequireAuth admin>
              <AdminUsersPage />
            </RequireAuth>
          }
        />
        <Route
          path="/admin/scoring"
          element={
            <RequireAuth admin>
              <AdminScoringPage />
            </RequireAuth>
          }
        />
      </Route>
    </Routes>
    </Suspense>
  )
}
