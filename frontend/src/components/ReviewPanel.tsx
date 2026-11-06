import { ActionIcon, Card, Checkbox, Group, Kbd, Loader, Stack, Text, Tooltip } from '@mantine/core'
import { IconArrowDown, IconArrowUp, IconCheck, IconSparkles, IconTool, IconX } from '@tabler/icons-react'
import { createColumnHelper, tableFeatures, useTable } from '@tanstack/react-table'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import type { Job, ResultRow, RowFilter, RowsPage } from '../api/types'
import { CORRECTION_TYPES, fmtCoord } from '../lib/format'
import { useRowAction } from '../lib/rows'
import { BandBadge, StatusBadge } from './Badges'
import { BulkBar } from './BulkBar'
import { FilterBar } from './FilterBar'

type SortField = 'row_index' | 'score' | 'status' | 'm_city' | 'm_country' | 'in_city' | 'precision'
const PAGE = 200
const ROW_H = 44
const features = tableFeatures({})
const helper = createColumnHelper<typeof features, ResultRow>()
const CORRECTION_LABEL = Object.fromEntries(CORRECTION_TYPES.map((c) => [c.value, c.label]))
const WIDTHS: Record<string, number> = {
  select: 36, row_index: 64, input: 300, matched: 260, lat: 100, lon: 100, score: 120, precision: 90, flags: 70, status: 96,
  actions: 76,
}
const SORTABLE: Record<string, SortField> = { row_index: 'row_index', input: 'in_city', matched: 'm_city', score: 'score', precision: 'precision', status: 'status' }

function joinParts(...parts: (string | null | undefined)[]) {
  return parts.filter(Boolean).join(', ')
}

export function ReviewPanel({ job, filter, setFilter, onOpen, openRow }: {
  job: Job; filter: RowFilter; setFilter: (f: RowFilter) => void; onOpen: (id: number) => void; openRow: number | null
}) {
  const [sort, setSort] = useState<SortField>('row_index')
  const [desc, setDesc] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [cursorIdx, setCursorIdx] = useState(0)
  const rowAction = useRowAction(job.id)

  const query = useInfiniteQuery({
    queryKey: ['rows', job.id, filter, sort, desc],
    queryFn: ({ pageParam }) =>
      api<RowsPage>(`/jobs/${job.id}/rows/query`, { json: { filter, sort, desc, cursor: pageParam, limit: PAGE } }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  })
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.rows) ?? [], [query.data])
  const total = query.data?.pages[0]?.total ?? null

  useEffect(() => {
    setSelected(new Set())
    setCursorIdx(0)
  }, [filter, sort, desc])

  const act = useCallback(
    (row: ResultRow, action: 'confirm' | 'reject') => rowAction.mutate({ rowId: row.id, body: { action, version: row.version } }),
    [rowAction],
  )
  const toggle = useCallback((id: number) => {
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  }, [])

  const columns = useMemo(
    () =>
      helper.columns([
        helper.display({ id: 'select', header: () => null, cell: ({ row }) => (
          <Checkbox size="xs" checked={selected.has(row.original.id)} onClick={(e) => e.stopPropagation()}
            onChange={() => toggle(row.original.id)} aria-label="Select row" />
        ) }),
        helper.accessor('row_index', { header: '#', cell: (i) => <Text size="xs" c="dimmed">{i.getValue()}</Text> }),
        helper.display({ id: 'input', header: 'Input', cell: ({ row: { original: r } }) => (
          <Text size="sm" truncate title={joinParts(r.in_street, r.in_house_number, r.in_postcode, r.in_city, r.in_country)}>
            {joinParts([r.in_street, r.in_house_number].filter(Boolean).join(' '), `${r.in_postcode} ${r.in_city}`.trim(), r.in_country) || '—'}
          </Text>
        ) }),
        helper.display({ id: 'matched', header: 'Matched', cell: ({ row: { original: r } }) => (
          <Text size="sm" truncate fw={500}>
            {joinParts(r.m_street, `${r.m_postcode ?? ''} ${r.m_city ?? ''}`.trim(), r.m_country) || '—'}
          </Text>
        ) }),
        helper.accessor('lat', { header: 'Lat', cell: (i) => <span className="mono">{fmtCoord(i.getValue())}</span> }),
        helper.accessor('lon', { header: 'Lon', cell: (i) => <span className="mono">{fmtCoord(i.getValue())}</span> }),
        helper.display({ id: 'score', header: 'Confidence', cell: ({ row: { original: r } }) => <BandBadge band={r.band} score={r.score} /> }),
        helper.accessor('precision', { header: 'Precision', cell: (i) => <Text size="xs">{i.getValue() ?? '–'}</Text> }),
        helper.display({ id: 'flags', header: 'Fixes', cell: ({ row: { original: r } }) => {
          const fixes = r.flags.filter((f) => f in CORRECTION_LABEL)
          if (!fixes.length && !r.error) return null
          const ai = fixes.some((f) => f.startsWith('ai_'))
          return (
            <Tooltip label={[...fixes.map((f) => CORRECTION_LABEL[f]), r.error ? `error: ${r.error}` : ''].filter(Boolean).join(' · ')}>
              <Group gap={2} wrap="nowrap">
                {ai ? <IconSparkles size={14} color="var(--mantine-color-violet-6)" /> : <IconTool size={14} />}
                <Text size="xs" c={r.error ? 'red' : undefined}>{fixes.length || '!'}</Text>
              </Group>
            </Tooltip>
          )
        } }),
        helper.display({ id: 'status', header: 'Status', cell: ({ row: { original: r } }) => <StatusBadge status={r.status} /> }),
        helper.display({ id: 'actions', header: () => null, cell: ({ row: { original: r } }) => (
          <Group gap={2} wrap="nowrap" onClick={(e) => e.stopPropagation()}>
            <Tooltip label="Confirm (C)"><ActionIcon size="sm" variant="subtle" color="teal" onClick={() => act(r, 'confirm')}><IconCheck size={14} /></ActionIcon></Tooltip>
            <Tooltip label="Reject (R)"><ActionIcon size="sm" variant="subtle" color="red" onClick={() => act(r, 'reject')}><IconX size={14} /></ActionIcon></Tooltip>
          </Group>
        ) }),
      ]),
    [selected, toggle, act],
  )
  const table = useTable({ features, columns, data: rows })
  const tableRows = table.getRowModel().rows

  const scrollRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    getItemKey: (i) => tableRows[i].original.id,
    overscan: 10,
  })
  const items = virtualizer.getVirtualItems()
  const lastItem = items[items.length - 1]
  useEffect(() => {
    if (lastItem && lastItem.index >= tableRows.length - 40 && query.hasNextPage && !query.isFetchingNextPage) {
      void query.fetchNextPage()
    }
  }, [lastItem, tableRows.length, query])

  // Keyboard review: J/K move, C confirm, R reject, Enter opens detail.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (openRow !== null) return
      const t = e.target as HTMLElement
      if (t.closest('input, textarea, [contenteditable], [role="listbox"]')) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const row = rows[cursorIdx]
      const k = e.key.toLowerCase()
      if (k === 'j' || e.key === 'ArrowDown') setCursorIdx((i) => Math.min(rows.length - 1, i + 1))
      else if (k === 'k' || e.key === 'ArrowUp') setCursorIdx((i) => Math.max(0, i - 1))
      else if (k === 'c' && row) { act(row, 'confirm'); setCursorIdx((i) => Math.min(rows.length - 1, i + 1)) }
      else if (k === 'r' && row) { act(row, 'reject'); setCursorIdx((i) => Math.min(rows.length - 1, i + 1)) }
      else if (e.key === 'Enter' && row) onOpen(row.id)
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [rows, cursorIdx, act, onOpen, openRow])
  useEffect(() => {
    if (rows.length) virtualizer.scrollToIndex(cursorIdx, { align: 'auto' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursorIdx])

  const totalWidth = Object.values(WIDTHS).reduce((a, b) => a + b, 0)
  function clickHeader(id: string) {
    const f = SORTABLE[id]
    if (!f) return
    if (sort === f) setDesc(!desc)
    else { setSort(f); setDesc(false) }
  }

  return (
    <Stack gap="sm">
      <Card withBorder p="sm">
        <FilterBar filter={filter} setFilter={setFilter} total={total} />
      </Card>
      <Group justify="space-between">
        <BulkBar jobId={job.id} filter={filter} selected={[...selected]} />
        <Text size="xs" c="dimmed">
          <Kbd size="xs">J</Kbd>/<Kbd size="xs">K</Kbd> move · <Kbd size="xs">C</Kbd> confirm · <Kbd size="xs">R</Kbd> reject · <Kbd size="xs">Enter</Kbd> details
        </Text>
      </Group>
      <Card withBorder p={0}>
        <div ref={scrollRef} style={{ height: 'calc(100vh - 380px)', minHeight: 360, overflow: 'auto' }}>
          <div style={{ minWidth: totalWidth }}>
            {table.getHeaderGroups().map((group) => (
              <div key={group.id} className="grid-head">
                {group.headers.map((h) => (
                  <div key={h.id} className={`grid-cell${SORTABLE[h.id] ? ' sortable' : ''}`}
                    style={{ width: WIDTHS[h.id], flexGrow: h.id === 'input' || h.id === 'matched' ? 1 : 0 }}
                    onClick={() => clickHeader(h.id)}>
                    <Group gap={2} wrap="nowrap">
                      {h.isPlaceholder ? null : <table.FlexRender header={h} />}
                      {SORTABLE[h.id] === sort && (desc ? <IconArrowDown size={12} /> : <IconArrowUp size={12} />)}
                    </Group>
                  </div>
                ))}
              </div>
            ))}
            <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
              {items.map((item) => {
                const row = tableRows[item.index]
                return (
                  <div key={item.key} className="grid-row" data-selected={item.index === cursorIdx}
                    style={{ position: 'absolute', top: 0, left: 0, right: 0, height: ROW_H, transform: `translateY(${item.start}px)` }}
                    onClick={() => { setCursorIdx(item.index); onOpen(row.original.id) }}>
                    {row.getAllCells().map((cell) => (
                      <div key={cell.id} className="grid-cell"
                        style={{ width: WIDTHS[cell.column.id], flexGrow: cell.column.id === 'input' || cell.column.id === 'matched' ? 1 : 0 }}>
                        <table.FlexRender cell={cell} />
                      </div>
                    ))}
                  </div>
                )
              })}
            </div>
            {query.isLoading && <Group justify="center" p="lg"><Loader size="sm" /></Group>}
            {!query.isLoading && rows.length === 0 && <Text c="dimmed" ta="center" p="xl">No rows match the current filter.</Text>}
          </div>
        </div>
      </Card>
    </Stack>
  )
}
