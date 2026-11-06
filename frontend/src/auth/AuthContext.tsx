import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError, setUnauthorizedHandler } from '../api/client'
import type { Me } from '../api/types'

interface AuthValue {
  me: Me | null
  loading: boolean
  refresh: () => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { data, isLoading } = useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await api<Me>('/auth/me')
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null
        throw e
      }
    },
    staleTime: 60_000,
    retry: false,
  })

  useEffect(() => {
    setUnauthorizedHandler(() => {
      qc.setQueryData(['me'], null)
      if (!window.location.pathname.startsWith('/login')) navigate('/login', { replace: true })
    })
  }, [qc, navigate])

  const refresh = useCallback(async () => {
    await qc.invalidateQueries({ queryKey: ['me'] })
  }, [qc])

  const logout = useCallback(async () => {
    try {
      await api('/auth/logout', { method: 'POST' })
    } finally {
      qc.clear()
      qc.setQueryData(['me'], null)
      navigate('/login', { replace: true })
    }
  }, [qc, navigate])

  return (
    <AuthContext.Provider value={{ me: data ?? null, loading: isLoading, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext)
  if (!v) throw new Error('useAuth outside AuthProvider')
  return v
}
