"""Interactive distance calculator (origin/destination as address or coordinates).

This is the ONLY place Google Maps Platform is used. Google results are never written to PostgreSQL,
never enter the job geocode cache and never appear in exports; they are cached in Redis for at most
30 days (see `Settings.google_cache_ttl_hours`).
"""
