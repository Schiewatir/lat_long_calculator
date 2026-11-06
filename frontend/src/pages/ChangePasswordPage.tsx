import { Alert, Button, Center, Paper, PasswordInput, Stack, Text, Title } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Me } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { notifyOk } from '../lib/hooks'

export function ChangePasswordPage() {
  const { me } = useAuth()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (next !== confirm) return setError('The new passwords do not match.')
    if (next.length < 12) return setError('Password must be at least 12 characters long.')
    setBusy(true)
    setError(null)
    try {
      const user = await api<Me>('/auth/change-password', { json: { current_password: current, new_password: next } })
      qc.setQueryData(['me'], user)
      notifyOk('Your password was changed.')
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change password')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Center mih="80vh">
      <Paper withBorder shadow="sm" p="xl" w={420}>
        <form onSubmit={submit}>
          <Stack>
            <Title order={3}>Change password</Title>
            {me?.must_change_password && (
              <Alert color="yellow">An administrator reset your password. Please choose a new one to continue.</Alert>
            )}
            {error && <Alert color="red">{error}</Alert>}
            <PasswordInput label="Current password" value={current} onChange={(e) => setCurrent(e.currentTarget.value)}
              autoComplete="current-password" required />
            <PasswordInput label="New password" description="At least 12 characters" value={next}
              onChange={(e) => setNext(e.currentTarget.value)} autoComplete="new-password" required />
            <PasswordInput label="Repeat new password" value={confirm} onChange={(e) => setConfirm(e.currentTarget.value)}
              autoComplete="new-password" required />
            <Button type="submit" loading={busy}>
              Save new password
            </Button>
            <Text size="xs" c="dimmed">
              Other sessions of your account will be signed out.
            </Text>
          </Stack>
        </form>
      </Paper>
    </Center>
  )
}
