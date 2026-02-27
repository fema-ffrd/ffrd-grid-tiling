#!/usr/bin/env python3
import argparse
import math
import numpy as np

import rasterio
from rasterio.transform import from_origin
from rasterio.crs import CRS

# --- Your target CRS WKT (US survey foot) ---
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


def almost_int(x, tol=1e-9):
    return abs(x - round(x)) <= tol


def snap_check(xmin, ymin, xmax, ymax, cellsize):
    """Check that bounds are exact multiples of cellsize and that dimensions are integer."""
    dx = (xmax - xmin) / cellsize
    dy = (ymax - ymin) / cellsize
    ok = almost_int(dx) and almost_int(dy)
    return ok, dx, dy


def build_test_data(height, width, seed=0):
    """
    Create deterministic float32 pattern so you can spot flips/offsets.
    - base gradient + small sinusoid
    """
    rr = np.arange(height, dtype=np.float32)[:, None]
    cc = np.arange(width, dtype=np.float32)[None, :]
    data = (rr * 0.1) + (cc * 0.01) + (np.sin(rr / 25.0) * 0.5).astype(np.float32)
    return data.astype(np.float32)


def main():
    ap = argparse.ArgumentParser(description="Create a test float32 COG snapped to tile bounds.")
    ap.add_argument("--xmin", type=float, required=True, help="Tile xmin (projected feet)")
    ap.add_argument("--ymin", type=float, required=True, help="Tile ymin (projected feet)")
    ap.add_argument("--xmax", type=float, required=True, help="Tile xmax (projected feet)")
    ap.add_argument("--ymax", type=float, required=True, help="Tile ymax (projected feet)")
    ap.add_argument("--cellsize", type=float, default=4.0, help="Pixel size in feet (default 4.0)")
    ap.add_argument("--out", type=str, required=True, help="Output COG path (.tif)")
    ap.add_argument("--blocksize", type=int, default=512, help="Internal tile/block size (default 512)")
    ap.add_argument("--compress", type=str, default="DEFLATE", help="Compression: DEFLATE, LZW, ZSTD (if supported)")
    ap.add_argument("--predictor", type=int, default=3, help="Predictor (3 often good for float32 with DEFLATE)")
    ap.add_argument("--add-overviews", action="store_true", help="Build internal overviews after writing")
    args = ap.parse_args()

    xmin, ymin, xmax, ymax = args.xmin, args.ymin, args.xmax, args.ymax
    cell = args.cellsize

    ok, dx, dy = snap_check(xmin, ymin, xmax, ymax, cell)
    if not ok:
        raise ValueError(
            f"Tile bounds are not evenly divisible by cellsize.\n"
            f"(xmax-xmin)/cellsize={dx}, (ymax-ymin)/cellsize={dy}\n"
            f"Adjust bounds or cellsize so both are integers."
        )

    width = int(round(dx))
    height = int(round(dy))

    # IMPORTANT: rasterio uses a north-up transform from top-left corner
    # Pixel grid edges will match:
    # left = xmin, right = xmin + width*cell = xmax
    # top  = ymax, bottom = ymax - height*cell = ymin
    transform = from_origin(xmin, ymax, cell, cell)

    print("=== Expected tile/raster alignment ===")
    print(f"Tile bounds: xmin={xmin}, ymin={ymin}, xmax={xmax}, ymax={ymax}")
    print(f"Cell size: {cell}")
    print(f"Raster width={width} px, height={height} px")
    print(f"Transform: {transform}")
    print("Expected raster bounds (from transform/dims):")
    exp_left = xmin
    exp_top = ymax
    exp_right = xmin + width * cell
    exp_bottom = ymax - height * cell
    print(f"  left={exp_left}, bottom={exp_bottom}, right={exp_right}, top={exp_top}")

    # Create test data
    data = build_test_data(height, width)

    crs = CRS.from_wkt(WKT)

    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "count": 1,
        "width": width,
        "height": height,
        "crs": crs,
        "transform": transform,
        "tiled": True,
        "blockxsize": args.blocksize,
        "blockysize": args.blocksize,
        "compress": args.compress,
        "predictor": args.predictor,
        "nodata": None
    }

    # Write GeoTIFF
    with rasterio.open(args.out, "w", **profile) as dst:
        dst.write(data, 1)

    # Optionally add overviews (recommended for a "real" COG)
    # Note: some environments prefer building overviews via GDAL CLI; rasterio does fine for testing.
    if args.add_overviews:
        with rasterio.open(args.out, "r+") as dst:
            # Typical overview factors
            factors = [2, 4, 8, 16]
            dst.build_overviews(factors, rasterio.enums.Resampling.average)
            dst.update_tags(ns="rio_overview", resampling="average")

    # Read back and verify bounds
    with rasterio.open(args.out) as src:
        b = src.bounds
        t = src.transform
        print("\n=== Read-back verification ===")
        print(f"Read-back bounds: {b}")
        print(f"Read-back transform: {t}")
        print(f"Read-back pixel size: ({t.a}, {abs(t.e)})")
        # Check exact match
        def close(a, b, tol=1e-9): return abs(a - b) <= tol
        ok_bounds = (
            close(b.left, xmin) and close(b.right, xmax) and
            close(b.bottom, ymin) and close(b.top, ymax)
        )
        print(f"Bounds match tile exactly: {ok_bounds}")

        # Check origin snapped to cellsize relative to (0,0) if you want:
        # left/top should be integer multiples of cellsize if origin is 0.
        origin_ok = almost_int(xmin / cell) and almost_int(ymax / cell)
        print(f"Origin is integer multiple of cellsize (relative to 0,0): {origin_ok}")

    print(f"\nWrote test COG: {args.out}")


if __name__ == "__main__":
    main()
