import {
  Alert, Badge, Button, Card, Checkbox, Group, Loader, ScrollArea, Select, SimpleGrid, Stack, Stepper, Switch,
  Table, Text, TextInput, Title,
} from '@mantine/core'
import { Dropzone } from '@mantine/dropzone'
import { IconFileSpreadsheet, IconSparkles, IconUpload, IconX } from '@tabler/icons-react'
import { useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Job, Mapping, Preview, TargetField } from '../api/types'
import { COUNTRY_OPTIONS } from '../lib/countries'
import { FIELD_LABELS, fmtNumber } from '../lib/format'
import { notifyError, useCapabilities } from '../lib/hooks'

const FIELDS: TargetField[] = ['street', 'house_number', 'city', 'postcode', 'country', 'id', 'full_address']
const ACCEPT = {
  'text/csv': ['.csv'], 'text/plain': ['.txt'], 'text/tab-separated-values': ['.tsv'],
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
  'application/vnd.ms-excel': ['.xls'],
}
const ENCODINGS = ['utf-8', 'utf-8-sig', 'utf-16', 'cp1252', 'iso-8859-1', 'cp1250', 'iso-8859-15', 'mac_roman']
const DELIMITERS = [
  { value: ',', label: 'Comma ,' }, { value: ';', label: 'Semicolon ;' }, { value: '\t', label: 'Tab' },
  { value: '|', label: 'Pipe |' },
]

function mappingProblems(m: Mapping, defaultCountry: string | null): string[] {
  const out: string[] = []
  if (!m.city && !m.postcode && !m.full_address) out.push('Map at least a city or a postcode column (or a full-address column).')
  if (!m.country && !defaultCountry && !m.full_address) out.push('Map a country column or choose a default country.')
  const used = Object.values(m).filter(Boolean)
  if (new Set(used).size !== used.length) out.push('A column is mapped to more than one field.')
  return out
}

export function NewJobPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const caps = useCapabilities()
  const [step, setStep] = useState(0)
  const [job, setJob] = useState<Job | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [mapping, setMapping] = useState<Mapping | null>(null)
  const [uploading, setUploading] = useState(false)
  const [reparsing, setReparsing] = useState(false)
  const [name, setName] = useState('')
  const [aiRepair, setAiRepair] = useState(true)
  const [defaultCountry, setDefaultCountry] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [serverProblems, setServerProblems] = useState<string[]>([])

  async function onDrop(files: File[]) {
    const fd = new FormData()
    fd.append('file', files[0])
    setUploading(true)
    try {
      const res = await api<{ job: Job; preview: Preview }>('/jobs/upload', { method: 'POST', body: fd })
      setJob(res.job)
      setPreview(res.preview)
      setMapping(res.preview.mapping)
      setName(res.job.name)
      setStep(1)
    } catch (e) {
      notifyError(e, 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  async function reparse(over: Partial<{ encoding: string; delimiter: string; sheet: string; has_header: boolean }>) {
    if (!job || !preview) return
    setReparsing(true)
    try {
      const f = preview.file
      const res = await api<{ preview: Preview }>(`/jobs/${job.id}/preview`, {
        json: { encoding: f.encoding, delimiter: f.delimiter, sheet: f.sheet, has_header: f.has_header, ...over },
      })
      setPreview(res.preview)
      setMapping(res.preview.mapping)
    } catch (e) {
      notifyError(e, 'Could not re-read the file')
    } finally {
      setReparsing(false)
    }
  }

  async function startJob() {
    if (!job || !mapping) return
    setStarting(true)
    setServerProblems([])
    try {
      await api(`/jobs/${job.id}/start`, {
        json: { name, mapping, ai_repair: aiRepair && !!caps.data?.ai_available, default_country: defaultCountry },
      })
      await qc.invalidateQueries({ queryKey: ['jobs'] })
      navigate(`/jobs/${job.id}`)
    } catch (e) {
      const detail = (e as { detail?: { problems?: string[] } }).detail
      if (detail?.problems) setServerProblems(detail.problems)
      else notifyError(e, 'Could not start the job')
    } finally {
      setStarting(false)
    }
  }

  const problems = mapping ? mappingProblems(mapping, defaultCountry) : []
  const mappedCols = useMemo(() => new Map(Object.entries(mapping ?? {}).filter(([, c]) => c).map(([f, c]) => [c!, f])), [mapping])
  const columnOptions = preview ? [{ value: '', label: '— not in file —' }, ...preview.columns.map((c) => ({ value: c, label: c }))] : []

  return (
    <Stack maw={1200} mx="auto">
      <Title order={2}>New geocoding job</Title>
      <Stepper active={step} onStepClick={(s) => s < step && setStep(s)} allowNextStepsSelect={false}>
        <Stepper.Step label="Upload" description="CSV, Excel or text">
          <Dropzone onDrop={onDrop} onReject={() => notifyError('Unsupported file type or file too large')}
            maxSize={(caps.data?.max_upload_mb ?? 100) * 1024 * 1024} accept={ACCEPT} multiple={false}
            loading={uploading} mt="md">
            <Group justify="center" gap="xl" mih={200} style={{ pointerEvents: 'none' }}>
              <Dropzone.Accept><IconUpload size={48} /></Dropzone.Accept>
              <Dropzone.Reject><IconX size={48} /></Dropzone.Reject>
              <Dropzone.Idle><IconFileSpreadsheet size={48} stroke={1.5} /></Dropzone.Idle>
              <div>
                <Text size="lg" fw={600}>Drop an address file here or click to select</Text>
                <Text size="sm" c="dimmed" mt={6}>
                  .csv, .txt, .tsv, .xlsx, .xls · up to {caps.data?.max_upload_mb ?? 100} MB and{' '}
                  {fmtNumber(caps.data?.max_rows ?? 250000)} rows. Columns: city, postcode, country, optionally street.
                </Text>
              </div>
            </Group>
          </Dropzone>
        </Stepper.Step>

        <Stepper.Step label="Check file" description="How it was read">
          {preview && (
            <Stack mt="md">
              <Card withBorder>
                <SimpleGrid cols={{ base: 2, md: 5 }}>
                  <div>
                    <Text size="xs" c="dimmed">Rows</Text>
                    <Text fw={600}>{fmtNumber(preview.file.row_count)}</Text>
                    {preview.file.empty_rows_skipped > 0 && (
                      <Text size="xs" c="dimmed">{preview.file.empty_rows_skipped} empty rows skipped</Text>
                    )}
                  </div>
                  {preview.file.format === 'csv' ? (
                    <>
                      <Select label="Encoding" size="xs" data={Array.from(new Set([preview.file.encoding ?? 'utf-8', ...ENCODINGS]))}
                        value={preview.file.encoding} onChange={(v) => v && reparse({ encoding: v })} allowDeselect={false} />
                      <Select label="Delimiter" size="xs" data={DELIMITERS} value={preview.file.delimiter}
                        onChange={(v) => v && reparse({ delimiter: v })} allowDeselect={false} />
                    </>
                  ) : (
                    <Select label="Sheet" size="xs" data={preview.file.sheets} value={preview.file.sheet}
                      onChange={(v) => v && reparse({ sheet: v })} allowDeselect={false} />
                  )}
                  <Checkbox label="First row is a header" checked={preview.file.has_header} mt="lg"
                    onChange={(e) => reparse({ has_header: e.currentTarget.checked })} />
                  {reparsing && <Loader size="sm" mt="lg" />}
                </SimpleGrid>
              </Card>
              <PreviewTable preview={preview} mapped={mappedCols} />
              <Group justify="flex-end">
                <Button onClick={() => setStep(2)}>Looks right — map columns</Button>
              </Group>
            </Stack>
          )}
        </Stepper.Step>

        <Stepper.Step label="Map columns" description="Which column is what">
          {preview && mapping && (
            <Stack mt="md">
              <Card withBorder>
                <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }}>
                  {FIELDS.map((f) => (
                    <Select key={f} label={
                      <Group gap={4}>
                        {FIELD_LABELS[f]}
                        {preview.mapping_source[f] === 'ai' && mapping[f] === preview.mapping[f] && (
                          <Badge size="xs" color="violet" leftSection={<IconSparkles size={10} />}>AI-suggested</Badge>
                        )}
                      </Group>
                    } data={columnOptions} value={mapping[f] ?? ''} allowDeselect={false}
                      onChange={(v) => setMapping({ ...mapping, [f]: v || null })} />
                  ))}
                  <Select label="Default country" description="Used when no country column exists or a cell is empty"
                    data={COUNTRY_OPTIONS} value={defaultCountry} onChange={setDefaultCountry} searchable clearable />
                </SimpleGrid>
              </Card>
              {problems.length > 0 && (
                <Alert color="orange">{problems.map((p) => <div key={p}>{p}</div>)}</Alert>
              )}
              <PreviewTable preview={preview} mapped={mappedCols} />
              <Group justify="flex-end">
                <Button disabled={problems.length > 0} onClick={() => setStep(3)}>Continue</Button>
              </Group>
            </Stack>
          )}
        </Stepper.Step>

        <Stepper.Step label="Start" description="Options">
          <Card withBorder mt="md" maw={640}>
            <Stack>
              <TextInput label="Job name" value={name} onChange={(e) => setName(e.currentTarget.value)} required />
              <Switch checked={aiRepair && !!caps.data?.ai_available} disabled={!caps.data?.ai_available}
                onChange={(e) => setAiRepair(e.currentTarget.checked)} label="AI repair for addresses the rules cannot fix"
                description={caps.data?.ai_available
                  ? 'Only the street, house number, city, postcode and country of unresolved rows are sent to OpenAI, with an anonymous id. Other columns never leave the server. AI never produces coordinates; every suggestion is checked against the gazetteer.'
                  : 'No OpenAI API key is configured on the server.'} />
              {!caps.data?.street_geocoding_available && mapping?.street && (
                <Alert color="yellow">
                  No geocoder API key is configured: rows with a street are located at postcode or city level only.
                </Alert>
              )}
              {serverProblems.length > 0 && <Alert color="red">{serverProblems.join(' ')}</Alert>}
              <Group justify="flex-end">
                <Button onClick={startJob} loading={starting} disabled={!name.trim()}>
                  Start processing {preview ? `${fmtNumber(preview.file.row_count)} rows` : ''}
                </Button>
              </Group>
            </Stack>
          </Card>
        </Stepper.Step>
      </Stepper>
    </Stack>
  )
}

function PreviewTable({ preview, mapped }: { preview: Preview; mapped: Map<string, string> }) {
  return (
    <Card withBorder p={0}>
      <ScrollArea>
        <Table striped withColumnBorders fz="xs">
          <Table.Thead>
            <Table.Tr>
              {preview.columns.map((c) => (
                <Table.Th key={c} bg={mapped.has(c) ? 'var(--mantine-primary-color-light)' : undefined}>
                  <Stack gap={2}>
                    <span>{c}</span>
                    {mapped.has(c) && <Badge size="xs">{FIELD_LABELS[mapped.get(c)!]}</Badge>}
                  </Stack>
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {preview.rows.map((r, i) => (
              <Table.Tr key={i}>
                {preview.columns.map((c, j) => (
                  <Table.Td key={c} style={{ whiteSpace: 'nowrap' }}>{r[j]}</Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea>
      <Text size="xs" c="dimmed" p="xs">First {preview.rows.length} of {fmtNumber(preview.file.row_count)} rows</Text>
    </Card>
  )
}
