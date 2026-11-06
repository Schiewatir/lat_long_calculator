import { Alert, Card, Group, Loader, Stack, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { MapContainer, TileLayer, useMap } from 'react-leaflet'
import { api } from '../api/client'
import type { RowFilter } from '../api/types'
import { BAND_HEX, BANDS } from '../lib/format'
import L, { loadMarkerCluster, TILE_ATTRIBUTION, TILE_URL } from '../lib/leaflet'
import { FilterBar } from './FilterBar'

type Point = [number, number, number, string]
const WORST_ORDER = 'HMLF'

/** Cluster bubbles take the color of the worst band inside, so problems stay visible when zoomed out. */
function clusterIcon(cluster: L.MarkerCluster): L.DivIcon {
  let worst = 0
  for (const m of cluster.getAllChildMarkers()) {
    worst = Math.max(worst, WORST_ORDER.indexOf((m.options as { band?: string }).band ?? 'H'))
  }
  const n = cluster.getChildCount()
  const size = n < 10 ? 30 : n < 100 ? 36 : n < 1000 ? 42 : 48
  const color = BAND_HEX[WORST_ORDER[worst]]
  return L.divIcon({
    className: '',
    iconSize: [size, size],
    html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${color};opacity:.9;border:3px solid white;box-shadow:0 1px 4px rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;color:white;font:600 12px system-ui">${n.toLocaleString()}</div>`,
  })
}

function ClusterLayer({ points, onOpen }: { points: Point[]; onOpen: (id: number) => void }) {
  const map = useMap()
  useEffect(() => {
    let layer: L.Layer | null = null
    let cancelled = false
    void loadMarkerCluster().then(() => {
      if (cancelled) return
      const renderer = L.canvas({ padding: 0.5 })
      const group = L.markerClusterGroup({ chunkedLoading: true, maxClusterRadius: 50, showCoverageOnHover: false, iconCreateFunction: clusterIcon })
      const markers = points.map(([id, lat, lon, band]) =>
        L.circleMarker([lat, lon], { renderer, radius: 6, color: '#fff', weight: 1.5, fillColor: BAND_HEX[band] ?? BAND_HEX['?'], fillOpacity: 0.9, band } as L.CircleMarkerOptions)
          .on('click', () => onOpen(id)),
      )
      group.addLayers(markers as unknown as L.Marker[])
      group.addTo(map)
      layer = group
      if (points.length) map.fitBounds(L.latLngBounds(points.map((p) => [p[1], p[2]] as [number, number])), { padding: [30, 30], maxZoom: 12 })
    })
    return () => {
      cancelled = true
      if (layer) map.removeLayer(layer)
    }
  }, [points, map, onOpen])
  return null
}

export function MapPanel({ jobId, filter, setFilter, onOpen }: {
  jobId: string; filter: RowFilter; setFilter: (f: RowFilter) => void; onOpen: (id: number) => void
}) {
  const q = useQuery({
    queryKey: ['map', jobId, filter],
    queryFn: () => api<{ points: Point[]; truncated: boolean }>(`/jobs/${jobId}/map`, { json: filter }),
  })
  return (
    <Stack gap="sm">
      <Card withBorder p="sm"><FilterBar filter={filter} setFilter={setFilter} /></Card>
      <Group gap="md">
        {BANDS.map((b) => (
          <Group key={b} gap={4}><div style={{ width: 10, height: 10, borderRadius: 5, background: BAND_HEX[b[0]] }} /><Text size="xs">{b}</Text></Group>
        ))}
        {q.data && <Text size="xs" c="dimmed">{q.data.points.length.toLocaleString()} located rows · clusters show their worst band · click a point for details</Text>}
        {q.isFetching && <Loader size="xs" />}
      </Group>
      {q.data?.truncated && <Alert color="yellow">Only the first 200,000 points are shown; narrow the filter to see the rest.</Alert>}
      <Card withBorder p={0} style={{ height: 'calc(100vh - 340px)', minHeight: 400 }}>
        <MapContainer center={[50, 10]} zoom={4} style={{ height: '100%' }} preferCanvas>
          <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
          {q.data && <ClusterLayer points={q.data.points} onOpen={onOpen} />}
        </MapContainer>
      </Card>
    </Stack>
  )
}
