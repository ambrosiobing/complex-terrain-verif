# Static inputs

- `stations_tier0.csv` is written by `afv stations --config configs/default.yaml` (network, needs FROST_CLIENT_ID). Edit `station_class` by hand where you know a site is coastal or inland; Frost has no coastline distance.
- `orog_0p25_norway.nc` is the model orography proxy for the 0.25 degree fields, produced from MEPS `surface_geopotential` with `afv.fetch_meps.orography_to_grid`. Absent file switches the lapse-rate correction off and the report says so.

Both files are small and meant to be committed once produced, so a run is reproducible from the repository alone.
