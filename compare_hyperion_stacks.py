#!/usr/bin/env python3
"""
Hyperion Stack Comparison & Spectral Verification Tool
======================================================
CLI tool to compare two ERDAS Imagine (.img) Hyperion hyperspectral stacks:
  - Validates band counts, spatial dimensions, CRS, and geotransforms.
  - Compares statistical distributions across diagnostic bands:
    * Band 13 (~477.7 nm, Visible Blue)
    * Band 20 (~549.0 nm, Visible Green)
    * Band 29 (~640.5 nm, Visible Red)
    * Band 50 (~854.2 nm, Near-Infrared / NIR)
    * Band 150 (~1648.9 nm, Shortwave Infrared / SWIR-1)
    * Band 204 (~2193.3 nm, Shortwave Infrared / SWIR-2)
  - Auto-detects sample pixels (Center, Healthy Vegetation, Soil/Mineral)
    and plots full spectral radiance curves (356 to 2577 nm) with shaded
    atmospheric water vapor absorption bands (~1400 nm and ~1900 nm).
  - Exports publication-grade comparison plot and CSV profile data.

Author: Senior Geospatial Software Engineer & Remote Sensing Scientist
"""

import os
import sys

# Ensure PROJ and Matplotlib environments are correctly configured on Windows
try:
    import pyproj
    proj_dir = pyproj.datadir.get_data_dir()
    if os.path.exists(proj_dir):
        os.environ["PROJ_LIB"] = proj_dir
        os.environ["PROJ_DATA"] = proj_dir
except Exception:
    pass

if "MPLCONFIGDIR" not in os.environ:
    os.environ["MPLCONFIGDIR"] = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", ".")), "matplotlib_cache")

import argparse
import csv
import numpy as np

# Dual support: Try rasterio first, fallback to osgeo.gdal
USE_RASTERIO = False
USE_GDAL = False

try:
    import rasterio
    USE_RASTERIO = True
except ImportError:
    try:
        import importlib
        # pyrefly: ignore [missing-import]
        # type: ignore[import-not-found,import-untyped]
        gdal = importlib.import_module("osgeo.gdal")
        gdal.UseExceptions()
        USE_GDAL = True
    except (ImportError, ModuleNotFoundError):
        print("ERROR: Neither 'rasterio' nor 'osgeo.gdal' is installed.")
        sys.exit(1)

try:
    import matplotlib
    import matplotlib.pyplot as plt
    HAVE_MATPLOTLIB = True
except ImportError:
    HAVE_MATPLOTLIB = False

try:
    from PIL import Image
except ImportError:
    Image = None


# Diagnostic bands for statistical consistency verification
DIAGNOSTIC_BANDS = [
    (13, "Band 13 (~477.7 nm, VNIR Blue)"),
    (20, "Band 20 (~549.0 nm, VNIR Green)"),
    (29, "Band 29 (~640.5 nm, VNIR Red)"),
    (50, "Band 50 (~854.2 nm, VNIR NIR)"),
    (150, "Band 150 (~1648.9 nm, SWIR-1)"),
    (204, "Band 204 (~2193.3 nm, SWIR-2)")
]

# Wavelength lookup generator for Hyperion
def get_wavelength(b):
    if b <= 70:
        return 355.59 + (b - 1) * 10.1764
    else:
        return 851.92 + (b - 71) * 10.0856


def get_scale_factor(b):
    return 40.0 if b <= 70 else 80.0


def compare_rasters(path1, path2, output_plot="hyperion_spectral_comparison.png", output_csv="hyperion_spectral_profiles.csv", custom_points=None):
    print("=================================================================")
    print("       HYPERION HYPERSPECTRAL STACK COMPARISON & VERIFICATION     ")
    print("=================================================================\n")

    if not os.path.exists(path1):
        print(f"ERROR: Stack 1 does not exist: {path1}")
        sys.exit(1)
    if not os.path.exists(path2):
        print(f"ERROR: Stack 2 does not exist: {path2}")
        sys.exit(1)

    print(f"Stack 1 (Candidate): {path1}")
    print(f"Stack 2 (Reference): {path2}\n")

    if USE_RASTERIO:
        ds1 = rasterio.open(path1)
        ds2 = rasterio.open(path2)
        count1, count2 = ds1.count, ds2.count
        w1, w2 = ds1.width, ds2.width
        h1, h2 = ds1.height, ds2.height
        crs1, crs2 = ds1.crs, ds2.crs
        gt1, gt2 = ds1.transform, ds2.transform
        dtype1, dtype2 = ds1.dtypes[0], ds2.dtypes[0]
        nodata1, nodata2 = ds1.nodata or 0, ds2.nodata or 0
    else:
        ds1 = gdal.Open(path1, gdal.GA_ReadOnly)
        ds2 = gdal.Open(path2, gdal.GA_ReadOnly)
        count1, count2 = ds1.RasterCount, ds2.RasterCount
        w1, w2 = ds1.RasterXSize, ds2.RasterXSize
        h1, h2 = ds1.RasterYSize, ds2.RasterYSize
        crs1, crs2 = ds1.GetProjection(), ds2.GetProjection()
        gt1, gt2 = ds1.GetGeoTransform(), ds2.GetGeoTransform()
        dtype1, dtype2 = ds1.GetRasterBand(1).DataType, ds2.GetRasterBand(1).DataType
        nodata1 = ds1.GetRasterBand(1).GetNoDataValue() or 0
        nodata2 = ds2.GetRasterBand(1).GetNoDataValue() or 0

    print("-----------------------------------------------------------------")
    print("1. RASTER GEOMETRY & METADATA COMPARISON")
    print("-----------------------------------------------------------------")
    print(f"Band Counts  : Stack 1 = {count1} bands | Stack 2 = {count2} bands [{'MATCH' if count1 == count2 else 'MISMATCH'}]")
    print(f"Dimensions   : Stack 1 = ({w1} x {h1}) | Stack 2 = ({w2} x {h2}) [{'MATCH' if (w1, h1) == (w2, h2) else 'MISMATCH'}]")
    print(f"Data Types   : Stack 1 = {dtype1} | Stack 2 = {dtype2} [{'MATCH' if dtype1 == dtype2 else 'NOTE'}]")
    print(f"CRS / Spatial: {'MATCH' if str(crs1) == str(crs2) else 'CHECK'}")
    print(f"Geotransform : {'MATCH' if str(gt1) == str(gt2) else 'CHECK'}\n")

    # Determine comparable bands
    common_count = min(count1, count2)

    print("-----------------------------------------------------------------")
    print("2. DIAGNOSTIC BAND STATISTICS & RADIOMETRIC DISTRIBUTION")
    print("-----------------------------------------------------------------")

    stats_summary = []

    for b, label in DIAGNOSTIC_BANDS:
        if b > common_count:
            print(f"Skipping {label}: band index exceeds raster band count ({common_count}).")
            continue

        if USE_RASTERIO:
            arr1 = ds1.read(b)
            arr2 = ds2.read(b)
        else:
            arr1 = ds1.GetRasterBand(b).ReadAsArray()
            arr2 = ds2.GetRasterBand(b).ReadAsArray()

        equal = np.array_equal(arr1, arr2)

        m1 = arr1 != nodata1
        m2 = arr2 != nodata2

        valid1 = arr1[m1]
        valid2 = arr2[m2]

        min1, max1 = (np.min(valid1), np.max(valid1)) if np.any(valid1) else (0, 0)
        min2, max2 = (np.min(valid2), np.max(valid2)) if np.any(valid2) else (0, 0)
        mean1, mean2 = (np.mean(valid1), np.mean(valid2)) if np.any(valid1) else (0.0, 0.0)
        std1, std2 = (np.std(valid1), np.std(valid2)) if np.any(valid1) else (0.0, 0.0)
        p2_1, p98_1 = np.percentile(valid1, [2, 98]) if np.any(valid1) else (0, 0)
        p2_2, p98_2 = np.percentile(valid2, [2, 98]) if np.any(valid2) else (0, 0)

        print(f"{label}:")
        print(f"  Exact Array Match     : {equal}")
        print(f"  Stack 1 Range [Min-Max]: [{min1} .. {max1}] | Mean: {mean1:.2f} | Std: {std1:.2f} | P(2-98%): ({p2_1:.1f}, {p98_1:.1f})")
        print(f"  Stack 2 Range [Min-Max]: [{min2} .. {max2}] | Mean: {mean2:.2f} | Std: {std2:.2f} | P(2-98%): ({p2_2:.1f}, {p98_2:.1f})\n")

        stats_summary.append({
            "band": b,
            "label": label,
            "equal": equal,
            "mean1": mean1,
            "mean2": mean2,
            "p2_1": p2_1, "p98_1": p98_1,
            "p2_2": p2_2, "p98_2": p98_2
        })

    print("-----------------------------------------------------------------")
    print("3. SPECTRAL PROFILE EXTRACTION & RADIOMETRIC CONSISTENCY")
    print("-----------------------------------------------------------------")

    # Read diagnostic bands to auto-detect sample pixels
    b_nir = min(50, common_count)
    b_red = min(29, common_count)
    b_swir = min(150, common_count)

    if USE_RASTERIO:
        nir_arr = ds1.read(b_nir).astype(np.float32)
        red_arr = ds1.read(b_red).astype(np.float32)
        swir_arr = ds1.read(b_swir).astype(np.float32)
    else:
        nir_arr = ds1.GetRasterBand(b_nir).ReadAsArray().astype(np.float32)
        red_arr = ds1.GetRasterBand(b_red).ReadAsArray().astype(np.float32)
        swir_arr = ds1.GetRasterBand(b_swir).ReadAsArray().astype(np.float32)

    valid_mask = (nir_arr > 0) & (red_arr > 0) & (swir_arr > 0)

    # Calculate NDVI-like ratio for vegetation detection: (NIR - Red) / (NIR + Red)
    denom = nir_arr + red_arr
    denom[denom == 0] = 1.0
    ndvi_ratio = np.where(valid_mask, (nir_arr - red_arr) / denom, -1.0)

    # 1. Vegetation Pixel: Max NDVI
    if np.any(valid_mask):
        veg_y, veg_x = np.unravel_index(np.argmax(ndvi_ratio), ndvi_ratio.shape)
    else:
        veg_y, veg_x = h1 // 2, w1 // 2

    # 2. Soil Pixel: High SWIR with low NDVI
    soil_metric = np.where(valid_mask & (ndvi_ratio < 0.25), swir_arr, 0.0)
    if np.any(soil_metric > 0):
        soil_y, soil_x = np.unravel_index(np.argmax(soil_metric), soil_metric.shape)
    else:
        soil_y, soil_x = h1 // 2, w1 // 2

    # 3. Center Pixel: middle of valid area
    center_y, center_x = h1 // 2, w1 // 2

    sample_targets = [
        ("Vegetation Pixel (Max NIR/Red)", int(veg_x), int(veg_y)),
        ("Soil / Bare Earth (High SWIR)", int(soil_x), int(soil_y)),
        ("Scene Center Pixel", int(center_x), int(center_y))
    ]

    if custom_points:
        for idx_p, (px, py) in enumerate(custom_points, start=1):
            sample_targets.append((f"Custom Point #{idx_p}", int(px), int(py)))

    # Extract profiles across all bands
    wavelengths = [get_wavelength(b) for b in range(1, common_count + 1)]
    profiles_data = []

    print(f"Sampling {len(sample_targets)} diagnostic spectral locations across {common_count} bands:")
    for target_label, px, py in sample_targets:
        print(f"  Target: {target_label} at pixel (X={px}, Y={py})")

        spec1 = []
        spec2 = []

        for b in range(1, common_count + 1):
            if USE_RASTERIO:
                v1 = float(ds1.read(b, window=rasterio.windows.Window(px, py, 1, 1))[0, 0])
                v2 = float(ds2.read(b, window=rasterio.windows.Window(px, py, 1, 1))[0, 0])
            else:
                v1 = float(ds1.GetRasterBand(b).ReadAsArray(px, py, 1, 1)[0, 0])
                v2 = float(ds2.GetRasterBand(b).ReadAsArray(px, py, 1, 1)[0, 0])

            # Convert to spectral radiance W/(m^2*sr*um)
            sf = get_scale_factor(b)
            rad1 = v1 / sf if (8 <= b <= 57 or 77 <= b <= 224) and v1 > 0 else 0.0
            rad2 = v2 / sf if (8 <= b <= 57 or 77 <= b <= 224) and v2 > 0 else 0.0

            spec1.append(rad1)
            spec2.append(rad2)

        profiles_data.append({
            "label": target_label,
            "x": px,
            "y": py,
            "spec1": spec1,
            "spec2": spec2
        })

    # Export to CSV
    try:
        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            header = ["Band", "Wavelength_nm", "Spectrometer"]
            for target in profiles_data:
                header.append(f"{target['label']}_Stack1_Radiance")
                header.append(f"{target['label']}_Stack2_Radiance")
            writer.writerow(header)

            for b_idx in range(common_count):
                b_num = b_idx + 1
                wl = wavelengths[b_idx]
                spec_name = "VNIR" if b_num <= 70 else "SWIR"
                row = [b_num, f"{wl:.2f}", spec_name]
                for target in profiles_data:
                    row.append(f"{target['spec1'][b_idx]:.4f}")
                    row.append(f"{target['spec2'][b_idx]:.4f}")
                writer.writerow(row)
        print(f"\n[OK] Spectral profile data exported -> '{output_csv}'")
    except Exception as e:
        print(f"[Notice] Could not write CSV export: {e}")

    # Plot spectral curves with Matplotlib
    if HAVE_MATPLOTLIB:
        print(f"Generating spectral comparison plot -> '{output_plot}'...")
        fig, axes = plt.subplots(len(sample_targets), 1, figsize=(12, 4 * len(sample_targets)), sharex=True)
        if len(sample_targets) == 1:
            axes = [axes]

        # Identify calibrated band indices (masking zero / uncalibrated bands)
        valid_b_indices = [
            i for i in range(common_count)
            if (8 <= (i + 1) <= 57) or (77 <= (i + 1) <= 224)
        ]
        wl_sub = [wavelengths[i] for i in valid_b_indices]

        for ax, target in zip(axes, profiles_data):
            s1_sub = [target["spec1"][i] for i in valid_b_indices]
            s2_sub = [target["spec2"][i] for i in valid_b_indices]

            ax.plot(wl_sub, s1_sub, color="#1f77b4", linewidth=2.0, label="Stack 1 (Candidate)", alpha=0.9)
            ax.plot(wl_sub, s2_sub, color="#ff7f0e", linewidth=1.5, linestyle="--", label="Stack 2 (Reference)", alpha=0.9)

            # Highlight water vapor absorption zones
            ax.axvspan(1350, 1450, color="gray", alpha=0.25, label="H2O Absorption (~1400 nm)")
            ax.axvspan(1800, 1950, color="lightgray", alpha=0.35, label="H2O Absorption (~1900 nm)")

            ax.set_title(f"Spectral Radiance Profile: {target['label']} at Pixel ({target['x']}, {target['y']})", fontsize=12, fontweight="bold")
            ax.set_ylabel(r"Spectral Radiance $\left[\frac{W}{m^2 \cdot sr \cdot \mu m}\right]$", fontsize=10)
            ax.grid(True, linestyle=":", alpha=0.6)
            ax.legend(loc="upper right", framealpha=0.9)

        axes[-1].set_xlabel("Wavelength (nm)", fontsize=11, fontweight="bold")
        axes[-1].set_xlim(400, 2450)
        plt.tight_layout()
        plt.savefig(output_plot, dpi=200)
        plt.close(fig)
        print(f"[OK] High-resolution spectral comparison plot saved -> '{output_plot}'\n")
    else:
        print("[Notice] Matplotlib not available; skipped plot creation (CSV profile exported).")

    # Clean up file handles
    if USE_RASTERIO:
        ds1.close()
        ds2.close()
    else:
        ds1 = None
        ds2 = None

    print("=================================================================")
    print("                     COMPARISON COMPLETED                        ")
    print("=================================================================")


def main():
    parser = argparse.ArgumentParser(
        description="NASA EO-1 Hyperion Hyperspectral Stack Comparison Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("stack1", help="Path to first ERDAS Imagine stack (.img)")
    parser.add_argument("stack2", help="Path to second ERDAS Imagine stack (.img)")
    parser.add_argument("--save-plot", default="hyperion_spectral_comparison.png",
                        help="Output path for spectral signature plot (PNG)")
    parser.add_argument("--csv", default="hyperion_spectral_profiles.csv",
                        help="Output path for spectral profiles table (CSV)")
    parser.add_argument("--points", default=None,
                        help="Optional custom pixel coordinates to sample, formatted as 'X1,Y1;X2,Y2'")

    args = parser.parse_args()

    custom_points = []
    if args.points:
        try:
            for pair in args.points.split(";"):
                if "," in pair:
                    x_str, y_str = pair.split(",", 1)
                    custom_points.append((int(x_str.strip()), int(y_str.strip())))
        except Exception as e:
            print(f"[Notice] Failed to parse custom points '{args.points}': {e}")

    compare_rasters(
        args.stack1,
        args.stack2,
        output_plot=args.save_plot,
        output_csv=args.csv,
        custom_points=custom_points
    )


if __name__ == "__main__":
    main()
