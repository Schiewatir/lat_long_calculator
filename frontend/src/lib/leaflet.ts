import L from 'leaflet'

// leaflet.markercluster is a UMD plugin that extends the global `L`.
;(window as unknown as { L: typeof L }).L = L

let clusterLoaded: Promise<unknown> | null = null
export function loadMarkerCluster(): Promise<unknown> {
  clusterLoaded ??= import('leaflet.markercluster')
  return clusterLoaded
}

export function pinIcon(color: string, size = 22): L.DivIcon {
  return L.divIcon({
    className: '',
    html: `<div class="pin-icon" style="background:${color};width:${size}px;height:${size}px"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size],
  })
}

export const TILE_URL: string = import.meta.env.VITE_TILE_URL ?? 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
export const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

export default L
