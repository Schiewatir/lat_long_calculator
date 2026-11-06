import { Alert, Badge, Button, Card, Group, JsonInput, Stack, Table, Text, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { fmtDate } from '../lib/format'
import { notifyError, notifyOk } from '../lib/hooks'

interface ScoringConfigResponse {
  active_version: number
  config: Record<string, unknown>
  versions: { version: number; active: boolean; created_at: string }[]
}

export function AdminScoringPage() {
  const qc = useQueryClient()
  const cfg = useQuery({ queryKey: ['scoring-config'], queryFn: () => api<ScoringConfigResponse>('/admin/scoring-config') })
  if (!cfg.data) return null
  return <ScoringEditor key={cfg.data.active_version} data={cfg.data} onSaved={() => void qc.invalidateQueries({ queryKey: ['scoring-config'] })} />
}

function ScoringEditor({ data, onSaved }: { data: ScoringConfigResponse; onSaved: () => void }) {
  const [text, setText] = useState(() => JSON.stringify(data.config, null, 2))
  const save = useMutation({
    mutationFn: () => api('/admin/scoring-config', { json: { config: JSON.parse(text) } }),
    onSuccess: () => {
      notifyOk('New scoring configuration is active. Existing jobs keep their scores until re-scored.')
      onSaved()
    },
    onError: (e) => notifyError(e, 'Could not save configuration'),
  })

  return (
    <Stack maw={1000} mx="auto">
      <Title order={2}>Confidence scoring</Title>
      <Alert color="blue">
        Scores start at 100 and lose the listed penalties for each signal; precision caps then apply. Saving creates a
        new version. Jobs keep the version they were scored with until an admin re-scores them from the job page.
        Calibrate weights with <code>python -m app.cli calibrate labeled.csv</code>.
      </Alert>
      <Group align="flex-start" grow>
        <Card withBorder>
          <Stack>
            <Group justify="space-between">
              <Text fw={600}>Active configuration</Text>
              <Badge>v{data.active_version}</Badge>
            </Group>
            <JsonInput value={text} onChange={setText} autosize minRows={20} maxRows={40} validationError="Invalid JSON"
              formatOnBlur />
            <Button onClick={() => save.mutate()} loading={save.isPending}>
              Save as new version
            </Button>
          </Stack>
        </Card>
        <Card withBorder maw={280}>
          <Text fw={600} mb="xs">
            Versions
          </Text>
          <Table>
            <Table.Tbody>
              {data.versions.map((v) => (
                <Table.Tr key={v.version}>
                  <Table.Td>v{v.version}</Table.Td>
                  <Table.Td>
                    <Text size="xs">{fmtDate(v.created_at)}</Text>
                  </Table.Td>
                  <Table.Td>{v.active && <Badge size="xs">active</Badge>}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      </Group>
    </Stack>
  )
}
