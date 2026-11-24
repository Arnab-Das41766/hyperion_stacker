#!/usr/bin/env python3
"""
Test Suite for Hyperion Stacker Pipeline
=========================================
Generates synthetic NASA EO-1 Hyperion scenes (242 bands + MTL metadata),
packs into .tar.gz archive, runs Hyperion_Stacker.py end-to-end (full stack
and --calibrated-only), tests preview generation, and tests compare_hyperion_stacks.py.
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

import shutil
import tarfile
import subprocess
import numpy as np
import rasterio
from rasterio.transform import from_origin

def run_tests():
    print("=================================================================")
    print("         RUNNING COMPREHENSIVE HYPERION STACKER TEST SUITE       ")
    print("=================================================================")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    test_sandbox = os.path.join(base_dir, "_test_sandbox")
    if os.path.exists(test_sandbox):
        shutil.rmtree(test_sandbox, ignore_errors=True)

    test_input = os.path.join(test_sandbox, "input")
    test_processing = os.path.join(test_sandbox, "processing")
    test_output = os.path.join(test_sandbox, "output")
    os.makedirs(test_input, exist_ok=True)
    os.makedirs(test_processing, exist_ok=True)
    os.makedirs(test_output, exist_ok=True)

    # 1. Generate synthetic 242 bands
    print("\n[Step 1] Creating synthetic 242-band Hyperion GeoTIFF dataset...")
    width, height = 100, 100
    transform = from_origin(500000.0, 3000000.0, 30.0, 30.0)
    crs = "EPSG:32631"

    raw_bands_dir = os.path.join(test_sandbox, "raw_bands")
    os.makedirs(raw_bands_dir, exist_ok=True)

    x = np.linspace(0, 1, width)
    y = np.linspace(0, 1, height)
    xx, yy = np.meshgrid(x, y)

    for b in range(1, 243):
        fname = f"EO1H0230342004245110PY_B{b:03d}_L1T.TIF"
        fpath = os.path.join(raw_bands_dir, fname)

        arr = np.zeros((height, width), dtype=np.int16)

        # Calibrated VNIR bands (8 to 57)
        if 8 <= b <= 57:
            wl = 355.59 + (b - 1) * 10.1764
            if wl < 500:
                val = 200 + 100 * xx
            elif wl < 600:
                val = 400 + 150 * xx
            elif wl < 700:
                val = 250 + 80 * xx
            else:  # NIR plateau
                val = 1800 + 800 * (1 - yy)
            arr = np.clip(val, 50, 4000).astype(np.int16)

        # Calibrated SWIR bands (77 to 224)
        elif 77 <= b <= 224:
            if (121 <= b <= 130) or (166 <= b <= 180):
                # Water vapor attenuation
                val = 30 + 10 * xx
            else:
                val = 1200 * yy + 600 * xx + 300
            arr = np.clip(val, 20, 5000).astype(np.int16)

        with rasterio.open(
            fpath, "w",
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype="int16",
            crs=crs,
            transform=transform,
            nodata=0
        ) as dst:
            dst.write(arr, 1)

    # Create metadata file
    mtl_path = os.path.join(raw_bands_dir, "EO1H0230342004245110PY_MTL_L1T.TXT")
    with open(mtl_path, "w", encoding="utf-8") as f:
        f.write("GROUP = L1_METADATA_FILE\n")
        f.write("  SPACECRAFT_ID = \"EO-1\"\n")
        f.write("  SENSOR_ID = \"Hyperion\"\n")
        f.write("  ACQUISITION_DATE = 2004-09-01\n")
        f.write("  SUN_ELEVATION = 58.243\n")
        f.write("  SUN_AZIMUTH = 124.812\n")
        f.write("  SCALING_FACTOR_VNIR = 40.0\n")
        f.write("  SCALING_FACTOR_SWIR = 80.0\n")
        f.write("END_GROUP = L1_METADATA_FILE\n")

    # Pack into USGS EarthExplorer-style .tar.gz archive
    archive_name = "EO1H0230342004245110PY.tar.gz"
    archive_path = os.path.join(test_input, archive_name)
    print(f"\n[Step 2] Packaging into .tar.gz archive: {archive_path}")
    with tarfile.open(archive_path, "w:gz") as tar:
        for f in os.listdir(raw_bands_dir):
            tar.add(os.path.join(raw_bands_dir, f), arcname=f)

    # 2. Test Hyperion_Stacker.py end-to-end
    print("\n[Step 3] Executing Hyperion_Stacker.py end-to-end on test dataset...")
    stacker_script = os.path.join(base_dir, "Hyperion_Stacker.py")
    cmd = [
        sys.executable,
        stacker_script,
        "--input", test_input,
        "--processing", test_processing,
        "--output", test_output,
        "--force"
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    print(res.stdout)
    if res.returncode != 0:
        print(f"FAILED: Hyperion_Stacker exited with code {res.returncode}")
        print(res.stderr)
        sys.exit(1)

    # 3. Verify created outputs
    print("\n[Step 4] Checking output artifacts in output/ ...")
    stacked_tif = os.path.join(test_output, "Hyperion_Stacked.tif")
    assert os.path.exists(stacked_tif), f"Missing ArcGIS stacked raster: {stacked_tif}"

    previews = [
        "Hyperion_TrueColor_Preview.png",
        "Hyperion_CIR_Vegetation_Preview.png",
        "Hyperion_SWIR_Geology_Preview.png",
        "Hyperion_Vegetation_Stress_Preview.png"
    ]
    for p in previews:
        p_path = os.path.join(test_output, p)
        assert os.path.exists(p_path), f"Missing preview composite: {p_path}"
        assert os.path.getsize(p_path) > 0, f"Empty preview composite: {p_path}"
        print(f"  [VERIFIED] {p} ({os.path.getsize(p_path):,} bytes)")

    # Inspect ArcGIS stacked raster properties
    with rasterio.open(stacked_tif) as ds_tif:
        assert ds_tif.count == 242, f"Expected 242 bands in .tif, got {ds_tif.count}"
        assert ds_tif.width == width and ds_tif.height == height
        assert ds_tif.crs.to_string() == "EPSG:32631"
        assert ds_tif.dtypes[0] == "int16"
        assert ds_tif.profile.get("compress") == "lzw"
        assert ds_tif.profile.get("tiled") is True
        assert len(ds_tif.descriptions) == 242

        tags_b29 = ds_tif.tags(29)
        assert "STATISTICS_MINIMUM" in tags_b29
        assert "STATISTICS_HISTOBINVALUES" in tags_b29
        assert "SCALE_FACTOR" in tags_b29
        assert tags_b29["SPECTROMETER"] == "VNIR"
        assert tags_b29["SCALE_FACTOR"] == "40.0"

        tags_b204 = ds_tif.tags(204)
        assert tags_b204["SPECTROMETER"] == "SWIR"
        assert tags_b204["SCALE_FACTOR"] == "80.0"

        ovr_factors = ds_tif.overviews(1)
        print(f"  [VERIFIED] ArcGIS BigTIFF: LZW Compressed, 256x256 Tiled, Band 29='{ds_tif.descriptions[28]}'")
        print(f"  [VERIFIED] Overviews built: {ovr_factors}")
        assert len(ovr_factors) > 0, "No overviews built in .tif!"

    print("  [VERIFIED] ArcGIS Pro BigTIFF (.tif) Stack verified.")

    # 4. Test compare_hyperion_stacks.py
    print("\n[Step 5] Testing compare_hyperion_stacks.py...")
    compare_script = os.path.join(base_dir, "compare_hyperion_stacks.py")
    comp_plot = os.path.join(test_output, "spectral_test_plot.png")
    comp_csv = os.path.join(test_output, "spectral_test_profiles.csv")

    comp_cmd = [
        sys.executable,
        compare_script,
        stacked_tif,
        stacked_tif,
        "--save-plot", comp_plot,
        "--csv", comp_csv
    ]
    res_comp = subprocess.run(comp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    print(res_comp.stdout)
    if res_comp.returncode != 0:
        print(f"FAILED: compare_hyperion_stacks exited with code {res_comp.returncode}")
        print(res_comp.stderr)
        sys.exit(1)

    assert os.path.exists(comp_plot), f"Missing plot: {comp_plot}"
    assert os.path.exists(comp_csv), f"Missing CSV: {comp_csv}"
    print(f"  [VERIFIED] Spectral comparison plot generated: {comp_plot} ({os.path.getsize(comp_plot):,} bytes)")
    print(f"  [VERIFIED] Spectral profiles CSV exported: {comp_csv} ({os.path.getsize(comp_csv):,} bytes)")

    # 5. Test Hyperion_ArcGIS_Stacker.py
    print("\n[Step 6] Testing standalone Hyperion_ArcGIS_Stacker.py...")
    arcgis_script = os.path.join(base_dir, "Hyperion_ArcGIS_Stacker.py")
    res_arc = subprocess.run([
        sys.executable, arcgis_script,
        "--input", test_input,
        "--processing", test_processing,
        "--output", test_output,
        "--force"
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res_arc.returncode != 0:
        print(f"FAILED: Hyperion_ArcGIS_Stacker exited with code {res_arc.returncode}")
        print(res_arc.stderr)
        sys.exit(1)
    print("  [VERIFIED] Standalone Hyperion_ArcGIS_Stacker executed successfully.")

    # 5. Test --calibrated-only mode
    print("\n[Step 7] Testing --calibrated-only mode (198 bands)...")
    cal_cmd = [
        sys.executable,
        stacker_script,
        "--calibrated-only",
        "--input", test_input,
        "--processing", test_processing,
        "--output", test_output,
        "--force"
    ]
    res_cal = subprocess.run(cal_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res_cal.returncode != 0:
        print(f"FAILED: --calibrated-only mode exited with code {res_cal.returncode}")
        print(res_cal.stderr)
        sys.exit(1)

    cal_stacked = os.path.join(test_output, "Hyperion_Stacked_CalibratedOnly.tif")
    assert os.path.exists(cal_stacked), f"Missing calibrated-only stack: {cal_stacked}"
    with rasterio.open(cal_stacked) as ds_cal:
        assert ds_cal.count == 198, f"Expected 198 bands in calibrated-only mode, got {ds_cal.count}"
        print(f"  [VERIFIED] Calibrated-only stack created with {ds_cal.count} bands.")

    # Clean up test sandbox
    shutil.rmtree(test_sandbox, ignore_errors=True)

    print("\n=================================================================")
    print("      ALL HYPERION STACKER VERIFICATION TESTS PASSED (100%)       ")
    print("=================================================================\n")


if __name__ == "__main__":
    run_tests()
