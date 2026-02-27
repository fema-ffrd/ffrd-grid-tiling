# FFRD Grid Tiling

A tool for generating fishnet grid tiling schemes from vector boundaries, optimized for Cloud-Optimized GeoTIFF (COG) storage and FFRD (Federal Flood Resilience Database) standard operating procedures.

## Overview

This project provides utilities to:
- **Generate tiling schemes** from arbitrary vector boundaries (shapefiles, geopackages, GeoJSON)
- **Create optimized grids** with configurable tile sizes and resolutions
- **Export as GeoParquet** with embedded coordinate reference system (CRS)
- **Test COG alignment** and generate sample raster tiles with proper georeferencing

The tiling system uses an origin-anchored, globally-stable indexing scheme that remains consistent as coverage expands.

## Project Structure

```
.
├── tiles.py                     # Main tiling scheme generator
├── test_cog.py                  # COG test/validation utility
├── generate-tiles.sh            # Example shell script with preset configurations
├── data/                        # Input vector boundaries
│   └── huc4-conus-simplified.gpkg
|       # CONUS HUC-4 Watersheds, obtained from:
|       # https://resilience.climate.gov/datasets/esri::watershed-boundary-dataset-huc-4s
├── schemes/                     # Output tiling scheme files (GeoParquet)
│   ├── hydraulics-tiles_98304ft_4ft_res.parquet
│   └── hydrology-tiles_196608ft_96ft_res.parquet
└── pyproject.toml              # Project metadata and dependencies
```

## Installation

### Prerequisites
- Python 3.13+
- Dependencies (geopandas, rasterio, pyarrow)

### Using uv (Recommended)
```bash
uv sync
```

### Using pip
```bash
pip install -e .
```

## Usage

### Generate a Tiling Scheme

The `tiles.py` script creates a fishnet grid from a vector boundary:

```bash
python tiles.py \
  --boundary <boundary_file> \
  --tile-size <feet> \
  --resolution <feet> \
  --buffer-miles <miles> \
  --out <output.parquet>
```

#### Arguments

| Argument | Type | Description |
|----------|------|-------------|
| `--boundary` | string | Path to boundary vector file (required). Supports shapefile, geopackage, GeoJSON, etc. |
| `--tile-size` | float | Tile size in feet (required). Must be compatible with resolution for COG requirements. |
| `--resolution` | float | Cell resolution in feet (required). Pixel count must be divisible by 512. |
| `--layer` | string | Optional layer name for multi-layer formats (e.g., geopackage). |
| `--origin-x` | float | Grid origin X coordinate for snapping. Default: 0.0 |
| `--origin-y` | float | Grid origin Y coordinate for snapping. Default: 0.0 |
| `--buffer-miles` | float | Buffer applied to boundary in miles. Default: 10.0 |
| `--clip` | flag | Clip tiles to buffered boundary (default: True). |
| `--no-clip` | flag | Output full bounding rectangle tiles without clipping. |
| `--out` | string | Output GeoParquet path (required). |
| `--compression` | string | Parquet compression method: zstd, snappy, gzip, none. Default: zstd |

#### Example

```bash
python tiles.py \
  --boundary ./data/huc4-conus-simplified.gpkg \
  --tile-size 98304 \
  --resolution 4 \
  --buffer-miles 10 \
  --out ./schemes/hydraulics-tiles_98304ft_4ft_res.parquet
```

#### Output Schema

The resulting GeoParquet contains:

| Column | Type | Description |
|--------|------|-------------|
| `tile_id` | string | Unique tile identifier: `T{tile-size}_R{resolution}_C{col:+07d}_R{row:+07d}` |
| `tile_size_ft` | float | Tile size in feet |
| `resolution_ft` | float | Cell resolution in feet |
| `origin_x` | float | Grid origin X coordinate |
| `origin_y` | float | Grid origin Y coordinate |
| `col` | int | Column index (increases eastward) |
| `row` | int | Row index (increases northward) |
| `xmin`, `ymin`, `xmax`, `ymax` | float | Tile bounds |
| `width`, `height` | float | Tile dimensions in feet |
| `geometry` | geometry | Tile polygon (left-lower corner is column/row origin) |
| `buffer_miles` | float | Buffer applied to boundary (if clipped) |

### Tile ID Scheme

Tile IDs follow the format: `T{tile-size}_R{resolution}_C±#######_R±#######`

**Example:** `T98304_R32_C+0000000_R+0000000`

**Design:**
- Origin-anchored indices remain stable as coverage expands
- Column increases eastward (+x), row increases northward (+y)
- Indices computed from tile's lower-left corner relative to fixed origin (default 0,0)
- 7-digit sign-prefixed format enables lexicographic sorting and future extension

### Test COG Creation

The `test_cog.py` script creates a test Cloud-Optimized GeoTIFF for validation:

```bash
python test_cog.py \
  --xmin <feet> \
  --ymin <feet> \
  --xmax <feet> \
  --ymax <feet> \
  --cellsize <feet> \
  --out <output.tif>
```

#### Arguments

| Argument | Type | Description |
|----------|------|-------------|
| `--xmin`, `--ymin`, `--xmax`, `--ymax` | float | Tile bounds in projected coordinates (feet) (required) |
| `--cellsize` | float | Pixel size in feet. Default: 4.0 |
| `--out` | string | Output COG path (required) |
| `--blocksize` | int | Internal block/tile size (GDAL). Default: 512 |
| `--compress` | string | Compression: DEFLATE, LZW, ZSTD. Default: DEFLATE |
| `--predictor` | int | Compression predictor. Default: 3 (good for float32 + DEFLATE) |
| `--add-overviews` | flag | Build internal overviews after writing |

#### Example

```bash
python test_cog.py \
  --xmin 0 --ymin 0 --xmax 98304 --ymax 98304 \
  --cellsize 4 \
  --out test_tile.tif \
  --add-overviews
```

The script creates a test float32 raster with a deterministic gradient pattern and verifies bounds alignment.

### Batch Generation

Use `generate-tiles.sh` to generate multiple tiling schemes:

```bash
chmod +x generate-tiles.sh
./generate-tiles.sh
```

Currently generates:
1. **Hydraulics scheme:** 98,304 ft tiles at 4 ft resolution
2. **Hydrology scheme:** 196,608 ft tiles at 96 ft resolution

Both use the HUC4 CONUS boundary with a 10-mile buffer.

## Coordinate Reference System

All operations use the **USA Contiguous Albers Equal Area Conic (USGS version)** in **international feet**:

```
PROJCS["USA_Contiguous_Albers_Equal_Area_Conic_USGS_version",
GEOGCS["GCS_North_American_1983", ...],
PROJECTION["Albers"],
PARAMETER["Central_Meridian", -96.0],
PARAMETER["Standard_Parallel_1", 29.5],
PARAMETER["Standard_Parallel_2", 45.5],
PARAMETER["Latitude_Of_Origin", 23.0],
UNIT["Foot", 0.3048]]
```

## Validation

### Tile Resolution Validation

The `tiles.py` script validates compatibility between tile size and resolution:
- Pixel count must be a whole number
- Pixel count must be divisible by 512 (GDAL COG default block size)
- Pixel count must be divisible by 16 (GDAL block alignment)

**Invalid example:**
```bash
# 98304 / 3 = 32768 pixels → divisible by 512? Yes → divisible by 16? Yes ✓
# 98304 / 2.5 = 39321.6 pixels → NOT a whole number ✗
```