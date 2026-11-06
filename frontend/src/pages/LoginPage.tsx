import { Alert, Button, Center, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core'
import { IconMapPin } from '@tabler/icons-react'
import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Me } from '../api/types'
import { useAuth } from '../auth/AuthContext'

export function LoginPage() {
  const { me } = useAuth()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (me) return <Navigate to={me.must_change_password ? '/change-password' : '/'} replace />

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const user = await api<Me>('/auth/login', { json: { username, password } })
      qc.setQueryData(['me'], user)
      const from = (location.state as { from?: string } | null)?.from
      navigate(user.must_change_password ? '/change-password' : from || '/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Center mih="100vh" bg="var(--mantine-color-default-hover)">
      <Paper withBorder shadow="sm" p="xl" w={380}>
        <form onSubmit={submit}>
          <Stack>
            <Stack gap={4} align="center">
              <IconMapPin size={36} color="var(--mantine-primary-color-filled)" />
              <Title order={3}>Geocoding Workbench</Title>
              <Text c="dimmed" size="sm">
                Sign in to continue
              </Text>
            </Stack>
            {error && <Alert color="red">{error}</Alert>}
            <TextInput label="Username" value={username} onChange={(e) => setUsername(e.currentTarget.value)}
              autoComplete="username" required autoFocus />
            <PasswordInput label="Password" value={password} onChange={(e) => setPassword(e.currentTarget.value)}
              autoComplete="current-password" required />
            <Button type="submit" loading={busy} fullWidth>
              Sign in
            </Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  )
}
