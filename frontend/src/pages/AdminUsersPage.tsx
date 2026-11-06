import { Badge, Button, Card, Group, Modal, PasswordInput, Select, Stack, Switch, Table, Text, TextInput, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import type { AdminUser, Role } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { fmtDate } from '../lib/format'
import { notifyError, notifyOk } from '../lib/hooks'

export function AdminUsersPage() {
  const { me } = useAuth()
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ['admin-users'], queryFn: () => api<AdminUser[]>('/admin/users') })
  const [createOpen, setCreateOpen] = useState(false)
  const [resetFor, setResetFor] = useState<AdminUser | null>(null)
  const [form, setForm] = useState({ username: '', password: '', role: 'user' as Role })
  const [resetPw, setResetPw] = useState('')

  const invalidate = () => qc.invalidateQueries({ queryKey: ['admin-users'] })
  const create = useMutation({
    mutationFn: () => api('/admin/users', { json: form }),
    onSuccess: () => {
      notifyOk(`User ${form.username} created`)
      setCreateOpen(false)
      setForm({ username: '', password: '', role: 'user' })
      void invalidate()
    },
    onError: (e) => notifyError(e, 'Could not create user'),
  })
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Partial<Pick<AdminUser, 'role' | 'active'>> }) =>
      api(`/admin/users/${id}`, { method: 'PATCH', json: body }),
    onSuccess: () => void invalidate(),
    onError: (e) => notifyError(e, 'Could not update user'),
  })
  const reset = useMutation({
    mutationFn: () => api(`/admin/users/${resetFor!.id}/reset-password`, { json: { password: resetPw } }),
    onSuccess: () => {
      notifyOk(`Password reset; ${resetFor!.username} must choose a new one at next sign-in.`)
      setResetFor(null)
      setResetPw('')
      void invalidate()
    },
    onError: (e) => notifyError(e, 'Could not reset password'),
  })

  return (
    <Stack maw={1000} mx="auto">
      <Group justify="space-between">
        <Title order={2}>Users</Title>
        <Button onClick={() => setCreateOpen(true)}>Add user</Button>
      </Group>
      <Card withBorder p={0}>
        <Table verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Username</Table.Th>
              <Table.Th>Role</Table.Th>
              <Table.Th>Active</Table.Th>
              <Table.Th>Created</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {users.data?.map((u) => (
              <Table.Tr key={u.id}>
                <Table.Td>
                  <Text fw={500}>{u.username}</Text>
                  {u.must_change_password && (
                    <Badge size="xs" color="yellow" variant="light">
                      must change password
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <Select size="xs" w={110} data={['user', 'admin']} value={u.role} allowDeselect={false}
                    onChange={(v) => v && patch.mutate({ id: u.id, body: { role: v as Role } })} />
                </Table.Td>
                <Table.Td>
                  <Switch checked={u.active} disabled={u.id === me?.id}
                    onChange={(e) => patch.mutate({ id: u.id, body: { active: e.currentTarget.checked } })} />
                </Table.Td>
                <Table.Td>{fmtDate(u.created_at)}</Table.Td>
                <Table.Td ta="right">
                  <Button size="xs" variant="default" onClick={() => setResetFor(u)}>
                    Reset password
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>

      <Modal opened={createOpen} onClose={() => setCreateOpen(false)} title="Add user">
        <Stack>
          <TextInput label="Username" value={form.username} onChange={(e) => setForm({ ...form, username: e.currentTarget.value })} />
          <PasswordInput label="Initial password" description="At least 12 characters" value={form.password}
            onChange={(e) => setForm({ ...form, password: e.currentTarget.value })} />
          <Select label="Role" data={['user', 'admin']} value={form.role} allowDeselect={false}
            onChange={(v) => setForm({ ...form, role: (v as Role) ?? 'user' })} />
          <Button onClick={() => create.mutate()} loading={create.isPending}>
            Create user
          </Button>
        </Stack>
      </Modal>
      <Modal opened={!!resetFor} onClose={() => setResetFor(null)} title={`Reset password for ${resetFor?.username}`}>
        <Stack>
          <Text size="sm" c="dimmed">
            The user is signed out everywhere and must choose a new password at next sign-in.
          </Text>
          <PasswordInput label="Temporary password" value={resetPw} onChange={(e) => setResetPw(e.currentTarget.value)} />
          <Button color="orange" onClick={() => reset.mutate()} loading={reset.isPending}>
            Reset password
          </Button>
        </Stack>
      </Modal>
    </Stack>
  )
}
