#!/usr/bin/env python3
"""
Generate a tiling scheme (fishnet grid) from an arbitrary vector boundary and export as GeoParquet.

Key features:
- Uses an arbitrary vector layer (e.g., geopackage, shapefile) to define the boundary.
- Buffers the boundary (default 10 miles) to ensure full coverage.
- Reprojects boundary into the target CRS (provided WKT) and generates a snapped fishnet grid.
- Optionally clips tiles to the buffered footprint (default: clip ON).
- Writes GeoParquet with embedded CRS.

Tile ID scheme (origin-anchored, extendable):
- T{tile-size}_R{resolution}_C±#######_R±#######
  Example: T98304_R32_C+0000000_R+0000000

Indexing convention:
- col increases eastward (+x)
- row increases northward (+y)
- col,row computed from tile lower-left corner relative to a fixed origin (default 0,0)
  so IDs remain stable when coverage expands.

Example:
  python make_conus_tiling_geoparquet.py \
    --boundary watersheds.gpkg \
    --tile-size 98304 \
    --resolution 32 \
    --buffer-miles 10 \
    --out tiles_98304ft_32ft_res.parquet
"""

import argparse
import math
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box
from shapely.ops import unary_union
from pyproj import CRS


# FFRD SOP projection WKT (International foot)
WKT = r'''PROJCS["USA_Contiguous_Albers_Equal_Area_Conic_USGS_version",
GEOGCS["GCS_North_American_1983",
DATUM["D_North_American_1983",
SPHEROID["GRS_1980",6378137.0,298.257222101]],
PRIMEM["Greenwich",0.0],
UNIT["Degree",0.0174532925199433]],
PROJECTION["Albers"],
PARAMETER["False_Easting",0.0],
PARAMETER["False_Northing",0.0],
PARAMETER["Central_Meridian",-96.0],
PARAMETER["Standard_Parallel_1",29.5],
PARAMETER["Standard_Parallel_2",45.5],
PARAMETER["Latitude_Of_Origin",23.0],
UNIT["Foot",0.3048]]'''


def load_boundary(vector_path: str, layer: str = None) -> gpd.GeoSeries:
    """
    Load a boundary from an arbitrary vector file (shapefile, geopackage, GeoJSON, etc.).
    Dissolves all geometries into a single boundary geometry.
    Returns a GeoSeries with one geometry (in source CRS).
    """
    print("Loading boundary vector data...")
    gdf = gpd.read_file(vector_path, layer=layer)

    if len(gdf) == 0:
        raise ValueError(f"No features found in {vector_path}")

    print("Dissolving geometries into a single boundary...")
    boundary_geom = unary_union(gdf.geometry)

    # Fix potential minor topology issues
    print("Buffering with zero to fix potential geometry issues...")
    boundary_geom = boundary_geom.buffer(0)

    return gpd.GeoSeries([boundary_geom], crs=gdf.crs)


def snapped_start(value: float, origin: float, size: float) -> float:
    """Snap down to the nearest tile boundary relative to origin."""
    return origin + math.floor((value - origin) / size) * size


def snapped_end(value: float, origin: float, size: float) -> float:
    """Snap up to the nearest tile boundary relative to origin."""
    return origin + math.ceil((value - origin) / size) * size


def idx_from_ll(xmin: float, ymin: float, origin_x: float, origin_y: float, tile_size: float) -> tuple[int, int]:
    """
    Compute stable (col,row) indices from a tile's LOWER-LEFT corner relative to origin.
    Uses floor to keep behavior correct for negative coordinates.
    """
    col = math.floor((xmin - origin_x) / tile_size)
    row = math.floor((ymin - origin_y) / tile_size)
    return int(col), int(row)


def validate_tile_resolution(tile_size: float, resolution: float) -> None:
    """
    Validate that tile_size and resolution are compatible with COG requirements:
    - Tile size / resolution must result in whole number of pixels
    - Pixel count must be divisible by 512 (GDAL COG default block size)
    - Pixel count must be divisible by 16 (GDAL requirement for block alignment)
    """
    pixels = tile_size / resolution
    
    # Check for whole number of pixels
    if abs(pixels - round(pixels)) > 1e-9:
        raise ValueError(
            f"tile_size ({tile_size} ft) / resolution ({resolution} ft) = {pixels} pixels. "
            f"Must result in a whole number of pixels."
        )
    
    pixels = int(round(pixels))
    
    # Check divisibility by 512 (GDAL COG default block size)
    if pixels % 512 != 0:
        raise ValueError(
            f"Pixel count ({pixels}) must be divisible by 512 (GDAL COG block size). "
            f"tile_size={tile_size}, resolution={resolution}."
        )
    
    # Check divisibility by 16 (GDAL block alignment requirement)
    if pixels % 16 != 0:
        raise ValueError(
            f"Pixel count ({pixels}) must be divisible by 16 (GDAL block alignment). "
            f"tile_size={tile_size}, resolution={resolution}."
        )


def format_tile_id(tile_size: float, col: int, row: int, resolution: float) -> str:
    """
    Build tile_id using the scheme: T{tile-size}_R{resolution}_C±#######_R±#######
    
    Args:
        tile_size: Tile size in feet
        col: Column index
        row: Row index
        resolution: Cell resolution in feet
    """
    tile_size_int = int(round(tile_size))
    resolution_int = int(round(resolution))
    # + sign is included; fixed width supports lexicographic sorting and extension
    return f"T{tile_size_int}_R{resolution_int}_C{col:+07d}_R{row:+07d}"

def generate_tiles(bounds, tile_size, origin_x, origin_y, resolution):
    """
    Generate tile polygons covering given bounds (xmin, ymin, xmax, ymax),
    snapped to origin and tile size. Uses origin-anchored global col/row indices.
    """
    xmin, ymin, xmax, ymax = bounds
    sxmin = snapped_start(xmin, origin_x, tile_size)
    symin = snapped_start(ymin, origin_y, tile_size)
    sxmax = snapped_end(xmax, origin_x, tile_size)
    symax = snapped_end(ymax, origin_y, tile_size)

    ncols = int(round((sxmax - sxmin) / tile_size))
    nrows = int(round((symax - symin) / tile_size))

    records = []
    for r in range(nrows):
        y0 = symin + r * tile_size
        y1 = y0 + tile_size
        for c in range(ncols):
            x0 = sxmin + c * tile_size
            x1 = x0 + tile_size

            # Global indices from lower-left corner (x0,y0)
            col, row = idx_from_ll(x0, y0, origin_x, origin_y, tile_size)
            tile_id = format_tile_id(tile_size, col, row, resolution)

            rec = {
                "tile_id": tile_id,
                "tile_size_ft": float(tile_size),
                "resolution_ft": float(resolution),
                "origin_x": float(origin_x),
                "origin_y": float(origin_y),
                "col": col,
                "row": row,
                "xmin": float(x0),
                "ymin": float(y0),
                "xmax": float(x1),
                "ymax": float(y1),
                "width": float(tile_size),
                "height": float(tile_size),
                "geometry": box(x0, y0, x1, y1)
            }

            records.append(rec)

    return records


def main():
    parser = argparse.ArgumentParser(
        description="Generate a tiling scheme (fishnet grid) from an arbitrary vector boundary layer."
    )
    parser.add_argument("--boundary", type=str, required=True,
                        help="Path to boundary vector file (shapefile, geopackage, GeoJSON, etc.)")
    parser.add_argument("--layer", type=str, default=None,
                        help="Optional layer name for formats that support multiple layers (e.g., geopackage)")

    parser.add_argument("--tile-size", type=float, required=True,
                        help="Tile size in target CRS units (feet).")

    parser.add_argument("--resolution", type=float, required=True,
                        help="Cell resolution in feet. Must be compatible with tile-size and GDAL COG requirements.")


    parser.add_argument("--origin-x", type=float, default=0.0,
                        help="Grid origin X for snapping (default 0.0)")
    parser.add_argument("--origin-y", type=float, default=0.0,
                        help="Grid origin Y for snapping (default 0.0)")

    parser.add_argument("--buffer-miles", type=float, default=10.0,
                        help="Buffer applied to boundary (miles). Default 10.")
    parser.add_argument("--clip", action="store_true", default=True,
                        help="Clip tiles to buffered boundary (default True)")
    parser.add_argument("--no-clip", dest="clip", action="store_false",
                        help="Do not clip; output full bounding rectangle tiles")

    parser.add_argument("--out", type=str, required=True,
                        help="Output GeoParquet path (.parquet)")
    parser.add_argument("--compression", type=str, default="zstd",
                        help="Parquet compression (zstd/snappy/gzip/none)")
    args = parser.parse_args()

    validate_tile_resolution(args.tile_size, args.resolution)

    target_crs = CRS.from_wkt(WKT)

    print(f"Loading boundary from: {args.boundary}")
    if args.layer:
        print(f"  Layer: {args.layer}")
    boundary = load_boundary(args.boundary, layer=args.layer)

    print("Reprojecting boundary to target CRS...")
    boundary_proj = boundary.to_crs(target_crs)

    # Buffer (miles -> feet); 5280 feet per mile
    buffer_feet = args.buffer_miles * 5280.0
    print(f"Buffering boundary by {args.buffer_miles} miles (~{buffer_feet:,.0f} ft)...")
    boundary_buf = boundary_proj.buffer(buffer_feet).iloc[0]

    bounds = boundary_buf.bounds  # (xmin, ymin, xmax, ymax)
    print(f"Buffered boundary bounds (target CRS): {bounds}")

    print("Generating tiles...")
    records = generate_tiles(
        bounds=bounds,
        tile_size=args.tile_size,
        origin_x=args.origin_x,
        origin_y=args.origin_y,
        resolution=args.resolution
    )
    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=target_crs)

    if args.clip:
        print("Clipping tiles to buffered boundary footprint (intersect test)...")
        mask = gdf.intersects(boundary_buf)
        gdf = gdf.loc[mask].copy()
        gdf["buffer_miles"] = args.buffer_miles

    print(f"Final tile count: {len(gdf):,}")

    compression = None if args.compression.lower() == "none" else args.compression.lower()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Writing GeoParquet to: {out_path}")
    gdf.to_parquet(out_path, compression=compression, index=False)

    print("Done.")
    print(f"Output bounds: {gdf.total_bounds}")


if __name__ == "__main__":
    main()
