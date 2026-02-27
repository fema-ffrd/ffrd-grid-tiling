#! /usr/bin/env sh

# Generate hydraulics tiling scheme
python tiles.py --boundary ./data/huc4-conus-simplified.gpkg \
  --tile-size 98304 \
  --resolution 4 \
  --buffer-miles 10 \
  --out ./schemes/hydraulics-tiles_98304ft_4ft_res.parquet

# Generate hydrology tiling scheme
python tiles.py --boundary ./data/huc4-conus-simplified.gpkg \
  --tile-size 196608\
  --resolution 96 \
  --buffer-miles 10 \
  --out ./schemes/hydrology-tiles_196608ft_96ft_res.parquet