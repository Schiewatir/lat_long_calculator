import { useEffect, useMemo } from 'react'
import { MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import type { DistanceResponse } from '../../api/types'
import { decodePolyline } from '../../lib/distance'
import { pinIcon, TILE_ATTRIBUTION, TILE_URL } from '../../lib/leaflet'

function FitTo({ points }: { points: [number, number][] }) {
  const map = useMap()
  const key = JSON.stringify(points)
  useEffect(() => {
    if (points.length > 1) map.fitBounds(points, { padding: [40, 40], maxZoom: 13 })
    else if (points.length === 1) map.setView(points[0], 12)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return null
}

/**
 * Both points, a dashed straight line and (when allowed) the road route.
 * `showGoogle=false` means the deployment has not cleared drawing Google content on the OSM map,
 * so the route polyline is omitted; callers hide the whole map when a point came from Google.
 */
export function DistanceMap({ result, showGoogle }: { result: DistanceResponse; showGoogle: boolean }) {
  const o: [number, number] = [result.origin.lat, result.origin.lon]
  const d: [number, number] = [result.destination.lat, result.destination.lon]
  const polyline = showGoogle ? result.road?.polyline : null
  const route = useMemo(() => (polyline ? decodePolyline(polyline) : []), [polyline])
  const bounds = route.length ? [o, d, ...route] : [o, d]
  return (
    <div style={{ height: 380 }}>
      <MapContainer center={o} zoom={5} style={{ height: '100%', borderRadius: 8 }} scrollWheelZoom>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
        <FitTo points={bounds} />
        <Polyline positions={[o, d]} pathOptions={{ color: '#868e96', weight: 2, dashArray: '6 6' }} />
        {route.length > 1 && <Polyline positions={route} pathOptions={{ color: '#228be6', weight: 4, opacity: 0.8 }} />}
        <Marker position={o} icon={pinIcon('#12b886', 24)}><Tooltip>Origin: {result.origin.label}</Tooltip></Marker>
        <Marker position={d} icon={pinIcon('#fa5252', 24)}><Tooltip>Destination: {result.destination.label}</Tooltip></Marker>
      </MapContainer>
    </div>
  )
}
