import { Alert, Anchor, Button, Card, Group, Loader, Progress, Stack, Table, Text, Title } from '@mantine/core'
import { IconPlus } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Job } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { BandBar, JobStatusBadge } from '../components/Badges'
import { ACTIVE_STATUSES, fmtDate, fmtNumber } from '../lib/format'
import { useCapabilities } from '../lib/hooks'

export function JobsPage() {
  const { me } = useAuth()
  const caps = useCapabilities()
  const jobs = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api<Job[]>('/jobs'),
    refetchInterval: (q) => (q.state.data?.some((j) => ACTIVE_STATUSES.includes(j.status)) ? 3000 : false),
  })

  return (
    <Stack maw={1300} mx="auto">
      <Group justify="space-between">
        <div>
          <Title order={2}>Jobs</Title>
          <Text c="dimmed" size="sm">
            {me?.role === 'admin' ? 'All users’ geocoding jobs' : 'Your geocoding jobs'}
          </Text>
        </div>
        <Button component={Link} to="/jobs/new" leftSection={<IconPlus size={16} />}>
          New job
        </Button>
      </Group>
      {caps.data && !caps.data.gazetteer_imported_at && (
        <Alert color="orange" title="No gazetteer imported">
          Postcode and city lookups need the GeoNames gazetteer. Ask an operator to run
          <code> python -m app.cli import-geonames</code>.
        </Alert>
      )}
      {jobs.isLoading && <Loader />}
      {jobs.data && jobs.data.length === 0 && (
        <Card withBorder p="xl">
          <Stack align="center" gap="xs">
            <Text fw={600}>No jobs yet</Text>
            <Text c="dimmed" size="sm">
              Upload a CSV, Excel or text file with addresses to get coordinates and confidence scores.
            </Text>
            <Button component={Link} to="/jobs/new" mt="sm">
              Upload a file
            </Button>
          </Stack>
        </Card>
      )}
      {jobs.data && jobs.data.length > 0 && (
        <Card withBorder p={0}>
          <Table highlightOnHover verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                {me?.role === 'admin' && <Table.Th>Owner</Table.Th>}
                <Table.Th>Created</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th ta="right">Rows</Table.Th>
                <Table.Th w={220}>Confidence</Table.Th>
                <Table.Th w={200}>Review progress</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {jobs.data.map((j) => {
                const total = j.counts.total_rows ?? 0
                const review = j.summary?.review ?? {}
                const reviewed = (review.confirmed ?? 0) + (review.edited ?? 0)
                return (
                  <Table.Tr key={j.id}>
                    <Table.Td>
                      <Anchor component={Link} to={`/jobs/${j.id}`} fw={600}>
                        {j.name}
                      </Anchor>
                      <Text size="xs" c="dimmed">
                        {j.file_name}
                      </Text>
                    </Table.Td>
                    {me?.role === 'admin' && <Table.Td>{j.owner}</Table.Td>}
                    <Table.Td>
                      <Text size="sm">{fmtDate(j.created_at)}</Text>
                    </Table.Td>
                    <Table.Td>
                      <JobStatusBadge status={j.status} />
                    </Table.Td>
                    <Table.Td ta="right">{fmtNumber(total)}</Table.Td>
                    <Table.Td>{j.summary && <BandBar bands={j.summary.bands} />}</Table.Td>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Progress value={total ? (reviewed / total) * 100 : 0} w={110} color="teal" />
                        <Text size="xs" c="dimmed">
                          {fmtNumber(reviewed)}/{fmtNumber(total)}
                        </Text>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                )
              })}
            </Table.Tbody>
          </Table>
        </Card>
      )}
    </Stack>
  )
}
