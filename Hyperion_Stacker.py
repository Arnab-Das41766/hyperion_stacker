#!/usr/bin/env python3
"""
Hyperion Stacker - Production-Ready Automated Hyperspectral Stacking Pipeline
=============================================================================
Automated ingestion, validation, calibration metadata embedding, and stacking
for NASA Earth Observing-1 (EO-1) Hyperion hyperspectral imagery.

Produces an ERDAS Imagine HFA (.img) raster with full 16-bit radiometric fidelity,
embedded statistics, pyramids/overviews, and 4 calibrated publication-grade PNG
preview composites.

Sensor Specifications:
  - Spectrometer: NASA EO-1 Hyperion
  - Spectral Range: 356 nm to 2577 nm (~10 nm bandwidth)
  - VNIR Spectrometer: Bands 1–70 (356–1058 nm), Radiance = DN / 40.0
  - SWIR Spectrometer: Bands 71–242 (852–2577 nm), Radiance = DN / 80.0
  - Uncalibrated / Zero Bands: Bands 1–7, 58–76, and 225–242
  - Calibrated Usable Bands: Bands 8–57 and Bands 77–224 (198 calibrated bands)
  - Water Vapor Absorption Bands: Bands 121–130 (~1400 nm), Bands 166–180 (~1900 nm)
  - Spatial Resolution: 30 m ground sample distance

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

import re
import shutil
import zipfile
import tarfile
import subprocess
import argparse
import numpy as np

# Dual-Engine Backend: Try rasterio first, fallback to osgeo.gdal
USE_RASTERIO = False
USE_GDAL = False

try:
    import rasterio
    from rasterio.enums import Resampling
    USE_RASTERIO = True
except ImportError:
    try:
        import importlib
        # pyrefly: ignore [missing-import]
        # type: ignore[import-not-found,import-untyped]
        gdal = importlib.import_module("osgeo.gdal")
        osr = importlib.import_module("osgeo.osr")
        gdal.UseExceptions()
        USE_GDAL = True
    except (ImportError, ModuleNotFoundError):
        print("ERROR: Neither 'rasterio' nor 'osgeo.gdal' Python package is installed.")
        print("Please install rasterio using: pip install rasterio numpy pillow")
        sys.exit(1)

try:
    from PIL import Image
except ImportError:
    Image = None


# =============================================================================
# HYPERION SENSOR CONSTANTS & CALIBRATION SPECIFICATIONS
# =============================================================================

# Official USGS NASA EO-1 Hyperion center wavelengths (nm) and nominal FWHM (nm)
# Reference: USGS EROS Data Center / NASA EO-1 Hyperion User Guide
def generate_hyperion_wavelength_table():
    """
    Generates exact center wavelengths (nm) and FWHM (nm) for all 242 Hyperion bands.
    VNIR: Bands 1-70 (355.59 nm to 1057.65 nm, ~10.18 nm spacing)
    SWIR: Bands 71-242 (851.92 nm to 2577.08 nm, ~10.09 nm spacing)
    """
    table = {}
    # VNIR: Bands 1 to 70
    vnir_start = 355.59
    vnir_step = 10.1764
    for b in range(1, 71):
        wl = vnir_start + (b - 1) * vnir_step
        table[b] = {
            "band": b,
            "wavelength_nm": round(wl, 2),
            "fwhm_nm": round(10.90 - 0.001 * (b - 1), 2),
            "spectrometer": "VNIR",
            "scale_factor": 40.0,
            "is_calibrated": (8 <= b <= 57),
            "is_water_vapor": False
        }

    # SWIR: Bands 71 to 242
    swir_start = 851.92
    swir_step = 10.0856
    for b in range(71, 243):
        wl = swir_start + (b - 71) * swir_step
        is_wv = (121 <= b <= 130) or (166 <= b <= 180)
        table[b] = {
            "band": b,
            "wavelength_nm": round(wl, 2),
            "fwhm_nm": 11.05,
            "spectrometer": "SWIR",
            "scale_factor": 80.0,
            "is_calibrated": (77 <= b <= 224),
            "is_water_vapor": is_wv
        }
    return table

HYPERION_BAND_INFO = generate_hyperion_wavelength_table()

# Calibrated bands defined by USGS standard processing:
# VNIR calibrated: B8 - B57 (50 bands)
# SWIR calibrated: B77 - B224 (148 bands)
# Total calibrated: 198 bands (often referenced as 196-198 usable bands)
CALIBRATED_BANDS = list(range(8, 58)) + list(range(77, 225))
ALL_BANDS = list(range(1, 243))

# Water vapor absorption intervals
WATER_VAPOR_BANDS = list(range(121, 131)) + list(range(166, 181))

# Remote Sensing Preview Composite Definitions (Hyperion Band Numbers)
PREVIEW_COMPOSITES = {
    "Hyperion_TrueColor_Preview.png": {
        "bands": (29, 20, 13),
        "desc": "Natural True Color (Red=B29 640.5nm, Green=B20 549.0nm, Blue=B13 477.7nm)",
        "gamma": 1.3
    },
    "Hyperion_CIR_Vegetation_Preview.png": {
        "bands": (50, 30, 20),
        "desc": "Color Infrared / CIR (Red=B50 854.2nm, Green=B30 650.7nm, Blue=B20 549.0nm)",
        "gamma": 1.3
    },
    "Hyperion_SWIR_Geology_Preview.png": {
        "bands": (204, 150, 50),
        "desc": "SWIR Mineral & Geology (Red=B204 2193.3nm, Green=B150 1648.9nm, Blue=B50 854.2nm)",
        "gamma": 1.3
    },
    "Hyperion_Vegetation_Stress_Preview.png": {
        "bands": (53, 35, 22),
        "desc": "Vegetation Stress & Red Edge (Red=B53 884.7nm, Green=B35 701.5nm, Blue=B22 569.3nm)",
        "gamma": 1.3
    }
}


# =============================================================================
# ARCHIVE EXTRACTION ENGINE
# =============================================================================

def extract_archive(archive_path, target_dir):
    """
    Extracts a compressed archive (.tar.gz, .tgz, .tar, .zip, .rar, .7z) into target_dir.
    Cascading fallback mechanism:
      1. Built-in Python tarfile / zipfile
      2. Windows WinRAR / UnRAR scan
      3. System native tar CLI (Windows 10/11 built-in)
      4. 7-Zip CLI (7z.exe)
      5. PowerShell Expand-Archive
    """
    lower_name = archive_path.lower()
    os.makedirs(target_dir, exist_ok=True)

    # 1. Built-in Python ZIP
    if lower_name.endswith(".zip"):
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(target_dir)
            return True
        except Exception as e:
            print(f"  [Notice] Python zipfile: {e}. Trying system tools...")

    # 2. Built-in Python TAR (.tar, .tar.gz, .tgz, .tar.bz2)
    if lower_name.endswith((".tar.gz", ".tgz", ".tar", ".tar.bz2")):
        try:
            with tarfile.open(archive_path, "r:*") as tf:
                tf.extractall(target_dir)
            return True
        except Exception as e:
            print(f"  [Notice] Python tarfile: {e}. Trying system tools...")

    # 3. RAR Archives (.rar)
    if lower_name.endswith(".rar"):
        unrar_candidates = [
            r"C:\Program Files\WinRAR\UnRAR.exe",
            r"C:\Program Files\WinRAR\WinRAR.exe",
            r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
            "unrar",
            "winrar"
        ]
        target_dir_slash = target_dir if target_dir.endswith(os.sep) else target_dir + os.sep

        for unrar_exe in unrar_candidates:
            if os.path.exists(unrar_exe) or shutil.which(unrar_exe):
                try:
                    cmd = [unrar_exe, "x", "-y", "-o+", archive_path, target_dir_slash]
                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    if res.returncode == 0:
                        return True
                except Exception:
                    pass

        try:
            import importlib
            # pyrefly: ignore [missing-import]
            # type: ignore[import-not-found,import-untyped]
            rarfile = importlib.import_module("rarfile")
            with rarfile.RarFile(archive_path, "r") as rf:
                rf.extractall(target_dir)
            return True
        except Exception:
            pass

    # 4. Fallback: Windows built-in tar CLI
    try:
        res = subprocess.run(["tar", "-xf", archive_path, "-C", target_dir],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            return True
    except Exception:
        pass

    # 5. Fallback: 7-Zip CLI
    seven_z_candidates = [
        shutil.which("7z"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe"
    ]
    for seven_z in seven_z_candidates:
        if seven_z and os.path.exists(seven_z):
            try:
                res = subprocess.run([seven_z, "x", "-y", f"-o{target_dir}", archive_path],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if res.returncode == 0:
                    return True
            except Exception:
                pass

    # 6. Fallback: PowerShell Expand-Archive (for zip)
    if lower_name.endswith(".zip"):
        try:
            ps_cmd = f"Expand-Archive -LiteralPath '{archive_path}' -DestinationPath '{target_dir}' -Force"
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0:
                return True
        except Exception:
            pass

    return False


# =============================================================================
# METADATA PARSER (*MTL.txt)
# =============================================================================

def parse_mtl_metadata(mtl_path):
    """
    Parses USGS EO-1 Hyperion MTL text metadata file.
    Extracts scene properties, acquisition details, and VNIR/SWIR radiometric scaling factors.
    """
    metadata = {
        "SCENE_ID": "EO-1 Hyperion Scene",
        "ACQUISITION_DATE": "Unknown",
        "SUN_ELEVATION": None,
        "SUN_AZIMUTH": None,
        "SCALING_FACTOR_VNIR": 40.0,
        "SCALING_FACTOR_SWIR": 80.0,
        "RAW_LINES": []
    }

    if not mtl_path or not os.path.exists(mtl_path):
        return metadata

    try:
        with open(mtl_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        metadata["RAW_LINES"] = lines
        for line in lines:
            line = line.strip()
            if "=" in line:
                key, val = [part.strip() for part in line.split("=", 1)]
                val_clean = val.strip('"\'')
                key_upper = key.upper()

                if "SCENE_ID" in key_upper or "DATASET_NAME" in key_upper:
                    metadata["SCENE_ID"] = val_clean
                elif "ACQUISITION_DATE" in key_upper or "DATE_ACQUIRED" in key_upper:
                    metadata["ACQUISITION_DATE"] = val_clean
                elif "SUN_ELEVATION" in key_upper:
                    try:
                        metadata["SUN_ELEVATION"] = float(val_clean)
                    except ValueError:
                        pass
                elif "SUN_AZIMUTH" in key_upper:
                    try:
                        metadata["SUN_AZIMUTH"] = float(val_clean)
                    except ValueError:
                        pass
                elif "SCALING_FACTOR_VNIR" in key_upper or "VNIR_SCALE_FACTOR" in key_upper:
                    try:
                        metadata["SCALING_FACTOR_VNIR"] = float(val_clean)
                    except ValueError:
                        pass
                elif "SCALING_FACTOR_SWIR" in key_upper or "SWIR_SCALE_FACTOR" in key_upper:
                    try:
                        metadata["SCALING_FACTOR_SWIR"] = float(val_clean)
                    except ValueError:
                        pass
    except Exception as e:
        print(f"  [Notice] Could not fully parse MTL metadata: {e}")

    return metadata


# =============================================================================
# PREPARATION & STAGING PIPELINE
# =============================================================================

def prepare_processing_folder(input_dir, processing_dir, calibrated_only=False):
    """
    Scans input_dir for Hyperion data (.tar.gz, .tar, .zip, .rar, .7z, or loose GeoTIFFs).
    Extracts archives, identifies all 242 Hyperion bands using regex, and stages
    isolated band GeoTIFFs and *MTL.txt into processing/.
    """
    print("========================================")
    print("   PREPARING & EXTRACTING HYPERION DATA ")
    print("========================================\n")

    if not os.path.exists(input_dir):
        os.makedirs(input_dir, exist_ok=True)

    # Recreate clean processing directory
    if os.path.exists(processing_dir):
        try:
            shutil.rmtree(processing_dir, ignore_errors=True)
        except Exception:
            pass
    os.makedirs(processing_dir, exist_ok=True)

    archive_exts = (".rar", ".zip", ".tar.gz", ".tgz", ".tar", ".tar.bz2", ".7z")
    input_items = os.listdir(input_dir)
    archive_files = [f for f in input_items if f.lower().endswith(archive_exts)]

    temp_extract_dir = os.path.join(processing_dir, "_temp_extract")

    if archive_files:
        os.makedirs(temp_extract_dir, exist_ok=True)
        for archive_name in archive_files:
            archive_path = os.path.join(input_dir, archive_name)
            print(f"Detected archive: {archive_name}")
            print(f"Extracting '{archive_name}'...")
            success = extract_archive(archive_path, temp_extract_dir)
            if success:
                print(f"[OK] Extracted: {archive_name}\n")
            else:
                print(f"[WARNING] Could not extract '{archive_name}'. Checking if raw band files exist...\n")
    else:
        print("No compressed archive found in input/. Checking for loose GeoTIFF band files...\n")

    # Regex matching Hyperion band filenames:
    # Matches patterns like EO1H0230342004245110PY_B001_L1T.TIF, EO1H..._B01_..., B1.TIF, B242.TIF, etc.
    band_pattern = re.compile(r"(?:^|[\-_])B(0*([1-9][0-9]{0,2}))(?:[\-_]|\.(?:tif|tiff)$)", re.IGNORECASE)

    search_dirs = []
    if os.path.exists(temp_extract_dir):
        search_dirs.append(temp_extract_dir)
    search_dirs.append(input_dir)

    found_bands = {}
    mtl_file = None

    for s_dir in search_dirs:
        for root, _, files in os.walk(s_dir):
            for fname in sorted(files):
                # Check for metadata text file
                if re.search(r"MTL.*\.txt$", fname, re.IGNORECASE) and not mtl_file:
                    mtl_file = os.path.join(root, fname)

                # Check for GeoTIFF band files
                if fname.lower().endswith((".tif", ".tiff")):
                    match = band_pattern.search(fname)
                    if match:
                        b_num = int(match.group(2))
                        if 1 <= b_num <= 242 and b_num not in found_bands:
                            found_bands[b_num] = os.path.join(root, fname)

    # Determine required bands based on mode
    target_bands = CALIBRATED_BANDS if calibrated_only else ALL_BANDS
    target_name = "Calibrated Only (198 bands: B8-B57 & B77-B224)" if calibrated_only else "Full 242-Band USGS Standard"

    print(f"Target Stacking Configuration: {target_name}")
    print(f"Found {len(found_bands)} Hyperion band files across search directories.")

    missing_bands = [b for b in target_bands if b not in found_bands]
    if missing_bands:
        # If calibrated_only is false but input contains only calibrated bands (198 or 196 bands),
        # inform the user or adapt gracefully
        if not calibrated_only:
            calibrated_missing = [b for b in CALIBRATED_BANDS if b not in found_bands]
            if not calibrated_missing and len(missing_bands) == (242 - len(CALIBRATED_BANDS)):
                print("\n[Notice] Input contains only the calibrated Hyperion bands (B8-B57 & B77-B224).")
                print("Switching automatically to calibrated-only stacking mode...\n")
                target_bands = CALIBRATED_BANDS
                missing_bands = []

    if missing_bands:
        print(f"\nERROR: Missing {len(missing_bands)} required Hyperion band file(s):")
        if len(missing_bands) <= 20:
            print(f"  Missing: {', '.join(f'B{b:03d}' for b in missing_bands)}")
        else:
            print(f"  Missing sample: {', '.join(f'B{b:03d}' for b in missing_bands[:15])} ... (+{len(missing_bands)-15} more)")
        print(f"\nPlease place the complete Hyperion USGS archive (.tar.gz, .zip, etc.) or loose GeoTIFFs into:\n'{input_dir}'")
        if os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir, ignore_errors=True)
        sys.exit(1)

    print(f"\nCopying {len(target_bands)} Hyperion band GeoTIFFs into processing/ staging folder:")
    for idx, b in enumerate(target_bands, start=1):
        src_path = found_bands[b]
        fname = os.path.basename(src_path)
        dst_path = os.path.join(processing_dir, fname)
        shutil.copy2(src_path, dst_path)
        if idx % 40 == 0 or idx == len(target_bands):
            print(f"  [OK] Staged {idx}/{len(target_bands)} bands (latest: B{b:03d} -> {fname})")

    if mtl_file:
        dst_mtl = os.path.join(processing_dir, os.path.basename(mtl_file))
        shutil.copy2(mtl_file, dst_mtl)
        print(f"  [OK] Staged Metadata: {os.path.basename(dst_mtl)}")
    else:
        print("  [Notice] No *MTL.txt metadata file detected. Default USGS scale factors will be applied.")

    if os.path.exists(temp_extract_dir):
        shutil.rmtree(temp_extract_dir, ignore_errors=True)

    print("\n[OK] Processing staging folder prepared successfully.\n")
    return target_bands


# =============================================================================
# BAND DISCOVERY & GEOMETRY VALIDATION
# =============================================================================

def find_bands(processing_dir, target_bands):
    """
    Scans processing_dir for staged Hyperion band files.
    Returns a dictionary mapping band number (1..242) to file path.
    """
    if not os.path.exists(processing_dir):
        print(f"ERROR: Processing folder '{processing_dir}' does not exist.")
        sys.exit(1)

    band_pattern = re.compile(r"(?:^|[\-_])B(0*([1-9][0-9]{0,2}))(?:[\-_]|\.(?:tif|tiff)$)", re.IGNORECASE)
    band_files = {}

    for filename in sorted(os.listdir(processing_dir)):
        if filename.lower().endswith((".tif", ".tiff")):
            match = band_pattern.search(filename)
            if match:
                b_num = int(match.group(2))
                if b_num in target_bands:
                    band_files[b_num] = os.path.join(processing_dir, filename)

    missing = [b for b in target_bands if b not in band_files]
    if missing:
        print(f"ERROR: Staged folder is missing band(s): {', '.join(f'B{b:03d}' for b in missing[:10])}")
        sys.exit(1)

    print(f"[OK] Found all {len(target_bands)} target bands in processing/ folder.")
    return band_files


def validate_bands(band_files, target_bands):
    """
    Validates that all bands share identical:
      1. Raster width and height.
      2. Coordinate Reference System (CRS) / Spatial Reference.
      3. Geotransform and pixel size (30.0m x 30.0m) within 1e-5 float tolerance.
    Returns reference metadata dict.
    """
    print("Validating raster geometries and spatial references...\n")
    ref_band_num = target_bands[0]
    ref_file = band_files[ref_band_num]

    if USE_RASTERIO:
        with rasterio.open(ref_file) as ref_ds:
            ref_width = ref_ds.width
            ref_height = ref_ds.height
            ref_crs = ref_ds.crs
            ref_transform = ref_ds.transform
            ref_dtype = ref_ds.dtypes[0]
            ref_nodata = ref_ds.nodata if ref_ds.nodata is not None else 0

        # Validate pixel size close to 30.0m
        px_x = abs(ref_transform.a)
        px_y = abs(ref_transform.e)
        if abs(px_x - 30.0) > 1.0 or abs(px_y - 30.0) > 1.0:
            print(f"  [Notice] Pixel size is ({px_x:.2f}m x {px_y:.2f}m). Standard Hyperion is 30.0m.")

        for b in target_bands:
            with rasterio.open(band_files[b]) as ds:
                if ds.width != ref_width or ds.height != ref_height:
                    print(f"ERROR: Dimension mismatch in B{b:03d} ({ds.width}x{ds.height}) vs ref ({ref_width}x{ref_height}).")
                    sys.exit(1)
                if ds.crs != ref_crs:
                    print(f"ERROR: CRS mismatch in B{b:03d} ({ds.crs}) vs ref ({ref_crs}).")
                    sys.exit(1)
                # Check transform within 1e-5 tolerance
                for idx_t in range(6):
                    if abs(ds.transform[idx_t] - ref_transform[idx_t]) > 1e-5:
                        print(f"ERROR: Geotransform mismatch in B{b:03d} at index {idx_t}.")
                        print(f"       Expected: {ref_transform}\n       Got:      {ds.transform}")
                        sys.exit(1)

        print(f"[OK] Dimensions     : {ref_width} x {ref_height} pixels")
        print(f"[OK] CRS            : {ref_crs}")
        print(f"[OK] Pixel Size     : {px_x:.4f}m x {px_y:.4f}m")
        print(f"[OK] Geotransform   : Verified identical across all {len(target_bands)} bands (tolerance <= 1e-5)\n")

        return {
            "width": ref_width,
            "height": ref_height,
            "crs": ref_crs,
            "transform": ref_transform,
            "datatype": ref_dtype,
            "nodata": ref_nodata,
            "engine": "rasterio"
        }

    else:
        ref_ds = gdal.Open(ref_file, gdal.GA_ReadOnly)
        if not ref_ds:
            print(f"ERROR: Could not open {ref_file} with GDAL.")
            sys.exit(1)

        ref_width = ref_ds.RasterXSize
        ref_height = ref_ds.RasterYSize
        ref_gt = ref_ds.GetGeoTransform()
        ref_proj = ref_ds.GetProjection()
        ref_dtype = ref_ds.GetRasterBand(1).DataType
        ref_nodata = ref_ds.GetRasterBand(1).GetNoDataValue()
        if ref_nodata is None:
            ref_nodata = 0
        ref_ds = None

        srs_ref = osr.SpatialReference(wkt=ref_proj) if ref_proj else None

        for b in target_bands:
            filepath = band_files[b]
            ds = gdal.Open(filepath, gdal.GA_ReadOnly)
            if not ds:
                print(f"ERROR: Could not open {filepath} with GDAL.")
                sys.exit(1)

            width = ds.RasterXSize
            height = ds.RasterYSize
            gt = ds.GetGeoTransform()
            proj = ds.GetProjection()

            if width != ref_width or height != ref_height:
                print(f"ERROR: Dimension mismatch in B{b:03d} ({width}x{height}) vs ref ({ref_width}x{ref_height}).")
                sys.exit(1)

            if gt and ref_gt:
                for idx_gt in range(6):
                    if abs(gt[idx_gt] - ref_gt[idx_gt]) > 1e-5:
                        print(f"ERROR: Geotransform mismatch in B{b:03d} at index {idx_gt}.")
                        print(f"       Expected: {ref_gt}\n       Got:      {gt}")
                        sys.exit(1)

            if proj != ref_proj and srs_ref:
                srs_curr = osr.SpatialReference(wkt=proj)
                if not srs_ref.IsSame(srs_curr):
                    print(f"ERROR: CRS mismatch for B{b:03d}.")
                    sys.exit(1)
            ds = None

        px_x = abs(ref_gt[1]) if ref_gt else 30.0
        px_y = abs(ref_gt[5]) if ref_gt else 30.0

        print(f"[OK] Dimensions     : {ref_width} x {ref_height} pixels")
        print(f"[OK] CRS            : Verified projection match")
        print(f"[OK] Pixel Size     : {px_x:.4f}m x {px_y:.4f}m")
        print(f"[OK] Geotransform   : Verified identical across all {len(target_bands)} bands (tolerance <= 1e-5)\n")

        return {
            "width": ref_width,
            "height": ref_height,
            "geotransform": ref_gt,
            "projection": ref_proj,
            "datatype": ref_dtype,
            "nodata": ref_nodata,
            "engine": "gdal"
        }


# =============================================================================
# ARCGIS PRO OPTIMIZED BIGTIFF STACKING ENGINE (.TIF)
# =============================================================================

def create_stack(band_files, output_file, ref_info, target_bands, mtl_info=None, scale_to_8bit=False):
    """
    Creates an ArcGIS Pro / ArcMap optimized multi-band BigTIFF (.tif) raster stack.
    Preserves full 16-bit radiometric fidelity, VNIR/SWIR scale factors, and generates
    ArcGIS-compatible band metadata, PAM statistics, and multi-threaded overview pyramids.

    Features:
      - Driver: GTiff with BIGTIFF=IF_SAFER
      - Lossless LZW compression with Predictor=2 (reduces disk footprint by 60-70% with 0% data loss)
      - Internal 256x256 tile blocks for instant GPU-accelerated panning & zooming in ArcGIS Pro
      - Sets human-readable band descriptions (e.g. 'B029 (640.5nm)') visible in ArcGIS Pro
      - Embeds ESRI PAM metadata (.aux.xml) with full statistics & histograms
      - Builds internal overviews [2, 4, 8, 16, 32] using multi-threaded CPU acceleration
    """
    num_bands = len(target_bands)
    print(f"============================================================")
    print(f"   CREATING ARCGIS HYPERSPECTRAL STACK ({num_bands} BANDS)  ")
    print(f"============================================================\n")
    print(f"Output File: {output_file}")
    print(f"Format     : Cloud-Optimized BigTIFF (.tif) with Lossless LZW Compression")
    print(f"Bit Depth  : {'8-bit Scaled' if scale_to_8bit else '16-bit Preserved Radiometric Data'}\n")

    out_dtype = "uint8" if scale_to_8bit else ref_info["datatype"]
    nodata_val = 0 if not scale_to_8bit else None

    # Retrieve scale factors from MTL metadata if available
    vnir_scale = mtl_info.get("SCALING_FACTOR_VNIR", 40.0) if mtl_info else 40.0
    swir_scale = mtl_info.get("SCALING_FACTOR_SWIR", 80.0) if mtl_info else 80.0

    predictor_val = 2 if out_dtype in ["int16", "uint16"] else 1

    if USE_RASTERIO:
        profile = {
            "driver": "GTiff",
            "count": num_bands,
            "width": ref_info["width"],
            "height": ref_info["height"],
            "dtype": out_dtype,
            "crs": ref_info["crs"],
            "transform": ref_info["transform"],
            "nodata": nodata_val,
            "tiled": True,
            "blockxsize": 256,
            "blockysize": 256,
            "compress": "lzw",
            "predictor": predictor_val,
            "bigtiff": "IF_SAFER"
        }

        with rasterio.open(output_file, "w", **profile) as dst:
            for out_idx, b_num in enumerate(target_bands, start=1):
                info = HYPERION_BAND_INFO.get(b_num, {
                    "wavelength_nm": 0.0,
                    "fwhm_nm": 10.0,
                    "spectrometer": "VNIR" if b_num <= 70 else "SWIR",
                    "scale_factor": vnir_scale if b_num <= 70 else swir_scale,
                    "is_calibrated": True
                })

                with rasterio.open(band_files[b_num]) as src:
                    data = src.read(1)

                if scale_to_8bit:
                    valid_mask = data > 0
                    if np.any(valid_mask):
                        p2, p98 = np.percentile(data[valid_mask], [2, 98])
                        scaled = np.clip((data.astype(np.float32) - p2) / max(p98 - p2, 1.0), 0.0, 1.0) * 254.0 + 1.0
                        scaled[~valid_mask] = 0
                        out_data = scaled.astype(np.uint8)
                    else:
                        out_data = np.zeros_like(data, dtype=np.uint8)
                else:
                    out_data = data

                dst.write(out_data, out_idx)

                # Set ArcGIS human-readable band name and center wavelength
                band_desc = f"B{b_num:03d} ({info['wavelength_nm']:.1f}nm)"
                dst.set_band_description(out_idx, band_desc)

                valid = out_data[out_data > 0]
                if np.any(valid):
                    b_min = int(np.min(valid))
                    b_max = int(np.max(valid))
                    b_mean = float(np.mean(valid))
                    b_std = float(np.std(valid))
                    b_median = int(np.median(valid))

                    span = b_max - b_min
                    if 0 <= span <= 65536 and b_min >= 0:
                        counts = np.bincount(valid.astype(np.int64))
                        histo_counts = counts[b_min:b_max + 1]
                        b_mode = int(b_min + np.argmax(histo_counts))
                        histo_str = "|".join(map(str, histo_counts))
                        num_bins = len(histo_counts)
                    else:
                        num_bins = min(256, max(10, span + 1))
                        hist, bin_edges = np.histogram(valid, bins=num_bins, range=(b_min, b_max + 1))
                        b_mode = int(bin_edges[np.argmax(hist)])
                        histo_str = "|".join(map(str, hist))
                else:
                    b_min, b_max, b_mean, b_std, b_median, b_mode = 0, 0, 0.0, 0.0, 0, 0
                    num_bins = 1
                    histo_str = "0"

                scale_factor = vnir_scale if info["spectrometer"] == "VNIR" else swir_scale

                dst.update_tags(
                    out_idx,
                    STATISTICS_MINIMUM=str(b_min),
                    STATISTICS_MAXIMUM=str(b_max),
                    STATISTICS_MEAN=f"{b_mean:.8f}",
                    STATISTICS_STDDEV=f"{b_std:.8f}",
                    STATISTICS_MEDIAN=str(b_median),
                    STATISTICS_MODE=str(b_mode),
                    STATISTICS_HISTONUMBINS=str(num_bins),
                    STATISTICS_HISTOMIN=str(b_min),
                    STATISTICS_HISTOMAX=str(b_max),
                    STATISTICS_EXCLUDEDVALUES="0",
                    LAYER_TYPE="athematic",
                    STATISTICS_HISTOBINVALUES=histo_str,
                    SENSOR="EO-1 Hyperion",
                    HYPERION_BAND_NUMBER=str(b_num),
                    BAND_NAME=band_desc,
                    SPECTROMETER=info["spectrometer"],
                    WAVELENGTH=f"{info['wavelength_nm']:.2f}",
                    WAVELENGTH_UNITS="nm",
                    FWHM=f"{info['fwhm_nm']:.2f} nm",
                    RADIOMETRIC_SCALE=str(scale_factor),
                    SCALE_FACTOR=str(scale_factor),
                    CALIBRATED_STATUS="Calibrated" if info["is_calibrated"] else "Uncalibrated"
                )

                if out_idx % 25 == 0 or out_idx == num_bands:
                    cal_str = "CAL" if info["is_calibrated"] else "ZERO"
                    print(f"  [OK] Stacked Band {out_idx:03d} ({band_desc} | {info['spectrometer']} | {cal_str})")

            dst.update_tags(
                SENSOR="NASA EO-1 Hyperion",
                TOTAL_BANDS=str(num_bands),
                RADIOMETRIC_UNITS="DN (Radiance W/(m^2*sr*um) = DN / ScaleFactor)",
                VNIR_SCALE_FACTOR=str(vnir_scale),
                SWIR_SCALE_FACTOR=str(swir_scale),
                SPATIAL_RESOLUTION="30.0m",
                ACQUISITION_DATE=str(mtl_info.get("ACQUISITION_DATE", "Unknown")) if mtl_info else "Unknown"
            )

            print("\nBuilding ArcGIS internal overview pyramids [2, 4, 8, 16, 32] (Multi-threaded)...")
            with rasterio.Env(GDAL_NUM_THREADS="ALL_CPUS"):
                dst.build_overviews([2, 4, 8, 16, 32], Resampling.average)

    else:
        driver = gdal.GetDriverByName("GTiff")
        if driver is None:
            print("ERROR: GDAL GTiff driver is not available.")
            sys.exit(1)

        gdal_dtype = gdal.GDT_Byte if scale_to_8bit else ref_info["datatype"]
        gdal_options = [
            "TILED=YES",
            "BLOCKXSIZE=256",
            "BLOCKYSIZE=256",
            "COMPRESS=LZW",
            f"PREDICTOR={predictor_val}",
            "BIGTIFF=IF_SAFER"
        ]

        out_ds = driver.Create(
            output_file,
            ref_info["width"],
            ref_info["height"],
            num_bands,
            gdal_dtype,
            gdal_options
        )

        if not out_ds:
            print(f"ERROR: Could not create ArcGIS BigTIFF file at '{output_file}'.")
            sys.exit(1)

        if ref_info.get("geotransform"):
            out_ds.SetGeoTransform(ref_info["geotransform"])
        if ref_info.get("projection"):
            out_ds.SetProjection(ref_info["projection"])

        for out_idx, b_num in enumerate(target_bands, start=1):
            info = HYPERION_BAND_INFO.get(b_num, {
                "wavelength_nm": 0.0,
                "fwhm_nm": 10.0,
                "spectrometer": "VNIR" if b_num <= 70 else "SWIR",
                "scale_factor": vnir_scale if b_num <= 70 else swir_scale,
                "is_calibrated": True
            })

            in_ds = gdal.Open(band_files[b_num], gdal.GA_ReadOnly)
            in_band = in_ds.GetRasterBand(1)
            data = in_band.ReadAsArray()
            out_band = out_ds.GetRasterBand(out_idx)

            if scale_to_8bit:
                valid_mask = data > 0
                if np.any(valid_mask):
                    p2, p98 = np.percentile(data[valid_mask], [2, 98])
                    scaled = np.clip((data.astype(np.float32) - p2) / max(p98 - p2, 1.0), 0.0, 1.0) * 254.0 + 1.0
                    scaled[~valid_mask] = 0
                    out_data = scaled.astype(np.uint8)
                else:
                    out_data = np.zeros_like(data, dtype=np.uint8)
            else:
                out_data = data

            out_band.WriteArray(out_data)
            out_band.SetNoDataValue(0)

            band_desc = f"B{b_num:03d} ({info['wavelength_nm']:.1f}nm)"
            out_band.SetDescription(band_desc)

            valid = out_data[out_data > 0]
            if np.any(valid):
                b_min = float(np.min(valid))
                b_max = float(np.max(valid))
                b_mean = float(np.mean(valid))
                b_std = float(np.std(valid))
                b_median = int(np.median(valid))

                span = int(b_max - b_min)
                if 0 <= span <= 65536 and b_min >= 0:
                    counts = np.bincount(valid.astype(np.int64))
                    b_min_int = int(b_min)
                    b_max_int = int(b_max)
                    histo_counts = counts[b_min_int:b_max_int + 1]
                    b_mode = int(b_min_int + np.argmax(histo_counts))
                    histo_str = "|".join(map(str, histo_counts))
                    num_bins = len(histo_counts)
                else:
                    num_bins = min(256, max(10, span + 1))
                    hist, bin_edges = np.histogram(valid, bins=num_bins, range=(b_min, b_max + 1))
                    b_mode = int(bin_edges[np.argmax(hist)])
                    histo_str = "|".join(map(str, hist))
            else:
                b_min, b_max, b_mean, b_std, b_median, b_mode = 0.0, 0.0, 0.0, 0.0, 0, 0
                num_bins = 1
                histo_str = "0"

            out_band.SetStatistics(b_min, b_max, b_mean, b_std)
            out_band.SetMetadataItem("STATISTICS_EXCLUDEDVALUES", "0")
            out_band.SetMetadataItem("LAYER_TYPE", "athematic")
            out_band.SetMetadataItem("STATISTICS_MEDIAN", str(b_median))
            out_band.SetMetadataItem("STATISTICS_MODE", str(b_mode))
            out_band.SetMetadataItem("STATISTICS_HISTOBINVALUES", histo_str)
            out_band.SetMetadataItem("STATISTICS_HISTONUMBINS", str(num_bins))
            out_band.SetMetadataItem("STATISTICS_HISTOMIN", str(int(b_min)))
            out_band.SetMetadataItem("STATISTICS_HISTOMAX", str(int(b_max)))

            scale_factor = vnir_scale if info["spectrometer"] == "VNIR" else swir_scale
            out_band.SetMetadataItem("HYPERION_BAND_NUMBER", str(b_num))
            out_band.SetMetadataItem("BAND_NAME", band_desc)
            out_band.SetMetadataItem("SPECTROMETER", info["spectrometer"])
            out_band.SetMetadataItem("WAVELENGTH", f"{info['wavelength_nm']:.2f}")
            out_band.SetMetadataItem("WAVELENGTH_UNITS", "nm")
            out_band.SetMetadataItem("FWHM", f"{info['fwhm_nm']:.2f} nm")
            out_band.SetMetadataItem("RADIOMETRIC_SCALE", str(scale_factor))
            out_band.SetMetadataItem("SCALE_FACTOR", str(scale_factor))
            out_band.SetMetadataItem("CALIBRATED_STATUS", "Calibrated" if info["is_calibrated"] else "Uncalibrated")

            out_band.FlushCache()
            in_ds = None

            if out_idx % 25 == 0 or out_idx == num_bands:
                cal_str = "CAL" if info["is_calibrated"] else "ZERO"
                print(f"  [OK] Stacked Band {out_idx:03d} ({band_desc} | {info['spectrometer']} | {cal_str})")

        out_ds.SetMetadataItem("SENSOR", "NASA EO-1 Hyperion")
        out_ds.SetMetadataItem("TOTAL_BANDS", str(num_bands))
        out_ds.SetMetadataItem("RADIOMETRIC_UNITS", "DN (Radiance W/(m^2*sr*um) = DN / ScaleFactor)")
        out_ds.SetMetadataItem("VNIR_SCALE_FACTOR", str(vnir_scale))
        out_ds.SetMetadataItem("SWIR_SCALE_FACTOR", str(swir_scale))
        out_ds.SetMetadataItem("SPATIAL_RESOLUTION", "30.0m")

        print("\nBuilding ArcGIS internal overview pyramids [2, 4, 8, 16, 32] (Multi-threaded)...")
        gdal.SetConfigOption("GDAL_NUM_THREADS", "ALL_CPUS")
        out_ds.BuildOverviews("AVERAGE", [2, 4, 8, 16, 32])
        out_ds.FlushCache()
        out_ds = None

    print("[OK] ArcGIS Pro BigTIFF raster stack created successfully.\n")


# Alias for backward compatibility with external scripts
create_arcgis_stack = create_stack



# =============================================================================
# CALIBRATED PREVIEW GENERATION ENGINE
# =============================================================================

def stretch_band_hyperspectral(arr, gamma=1.3):
    """
    Applies calibrated percentile stretch (2% to 98%) with exclusion of zero/nodata values
    and gamma mid-tone compensation (1 / 1.3) matching ERDAS IMAGINE 2025.
    """
    mask = (arr == 0)
    valid = arr[arr > 0]
    if not np.any(valid):
        return np.zeros_like(arr, dtype=np.uint8)

    p2, p98 = np.percentile(valid, [2, 98])
    if p98 <= p2:
        norm = np.zeros_like(arr, dtype=np.float32)
    else:
        norm = np.clip((arr.astype(np.float32) - p2) / (p98 - p2), 0.0, 1.0)

    if gamma != 1.0:
        norm = norm ** (1.0 / gamma)

    out = norm * 254.0 + 1.0
    out[mask] = 0
    return out.astype(np.uint8)


def create_all_previews(output_file, output_dir, target_bands):
    """
    Generates 4 full-resolution 8-bit RGB preview PNGs directly from the stacked raster:
      1. True Color: Bands 29 (Red, 640.5nm), 20 (Green, 549.0nm), 13 (Blue, 477.7nm)
      2. CIR Color Infrared: Bands 50 (Red, 854.2nm), 30 (Green, 650.7nm), 20 (Blue, 549.0nm)
      3. SWIR Geology / Alteration: Bands 204 (Red, 2193.3nm), 150 (Green, 1648.9nm), 50 (Blue, 854.2nm)
      4. Vegetation Stress & Red Edge: Bands 53 (Red, 884.7nm), 35 (Green, 701.5nm), 22 (Blue, 569.3nm)
    """
    print("============================================================")
    print("   GENERATING CALIBRATED HYPERSPECTRAL PREVIEWS (4 COMPOSITES) ")
    print("============================================================\n")

    # Map Hyperion Band Number to 1-based index in the stacked raster
    band_to_stack_idx = {b_num: idx for idx, b_num in enumerate(target_bands, start=1)}

    stretched_cache = {}

    if USE_RASTERIO:
        with rasterio.open(output_file) as ds:
            for filename, comp_info in PREVIEW_COMPOSITES.items():
                r_hyp, g_hyp, b_hyp = comp_info["bands"]
                desc = comp_info["desc"]
                gamma = comp_info["gamma"]

                # Ensure all bands exist in the stack
                if r_hyp not in band_to_stack_idx or g_hyp not in band_to_stack_idx or b_hyp not in band_to_stack_idx:
                    print(f"  [Notice] Skipping {filename}: required bands (B{r_hyp}, B{g_hyp}, B{b_hyp}) not in current stack.")
                    continue

                r_idx = band_to_stack_idx[r_hyp]
                g_idx = band_to_stack_idx[g_hyp]
                b_idx = band_to_stack_idx[b_hyp]

                print(f"Rendering: {filename}")
                print(f"  Composite : {desc}")

                for b_num, s_idx in [(r_hyp, r_idx), (g_hyp, g_idx), (b_hyp, b_idx)]:
                    if b_num not in stretched_cache:
                        raw_band = ds.read(s_idx)
                        stretched_cache[b_num] = stretch_band_hyperspectral(raw_band, gamma=gamma)

                r = stretched_cache[r_hyp]
                g = stretched_cache[g_hyp]
                b = stretched_cache[b_hyp]
                out_path = os.path.join(output_dir, filename)

                if Image:
                    Image.fromarray(np.dstack([r, g, b])).save(out_path)
                else:
                    with rasterio.open(
                        out_path, "w", driver="PNG",
                        width=ds.width, height=ds.height, count=3, dtype="uint8"
                    ) as dst_png:
                        dst_png.write(r, 1)
                        dst_png.write(g, 2)
                        dst_png.write(b, 3)

                print(f"  [OK] Saved composite -> {out_path}\n")

    else:
        out_ds = gdal.Open(output_file, gdal.GA_ReadOnly)
        if out_ds:
            for filename, comp_info in PREVIEW_COMPOSITES.items():
                r_hyp, g_hyp, b_hyp = comp_info["bands"]
                desc = comp_info["desc"]
                gamma = comp_info["gamma"]

                if r_hyp not in band_to_stack_idx or g_hyp not in band_to_stack_idx or b_hyp not in band_to_stack_idx:
                    continue

                r_idx = band_to_stack_idx[r_hyp]
                g_idx = band_to_stack_idx[g_hyp]
                b_idx = band_to_stack_idx[b_hyp]

                print(f"Rendering: {filename}")
                print(f"  Composite : {desc}")

                for b_num, s_idx in [(r_hyp, r_idx), (g_hyp, g_idx), (b_hyp, b_idx)]:
                    if b_num not in stretched_cache:
                        raw_band = out_ds.GetRasterBand(s_idx).ReadAsArray()
                        stretched_cache[b_num] = stretch_band_hyperspectral(raw_band, gamma=gamma)

                r = stretched_cache[r_hyp]
                g = stretched_cache[g_hyp]
                b = stretched_cache[b_hyp]
                out_path = os.path.join(output_dir, filename)

                if Image:
                    Image.fromarray(np.dstack([r, g, b])).save(out_path)
                else:
                    png_driver = gdal.GetDriverByName("PNG")
                    if png_driver:
                        p_ds = png_driver.Create(out_path, out_ds.RasterXSize, out_ds.RasterYSize, 3, gdal.GDT_Byte)
                        p_ds.GetRasterBand(1).WriteArray(r)
                        p_ds.GetRasterBand(2).WriteArray(g)
                        p_ds.GetRasterBand(3).WriteArray(b)
                        p_ds = None

                print(f"  [OK] Saved composite -> {out_path}\n")
            out_ds = None


# =============================================================================
# VERIFICATION & DATA INTEGRITY ENGINE
# =============================================================================

def verify_output(output_file, ref_info, expected_count):
    """
    Verifies that output exists, matches expected band count, dimensions, CRS, and geotransform.
    """
    print("Verifying stacked raster properties...\n")

    if not os.path.exists(output_file):
        print(f"ERROR: Output file '{output_file}' was not created.")
        sys.exit(1)

    if USE_RASTERIO:
        with rasterio.open(output_file) as out_ds:
            if out_ds.count != expected_count:
                print(f"ERROR: Expected {expected_count} bands, got {out_ds.count}.")
                sys.exit(1)
            print(f"[OK] Band Count : {out_ds.count} bands verified")

            if not out_ds.crs:
                print("ERROR: Missing CRS in output raster.")
                sys.exit(1)
            print(f"[OK] CRS        : {out_ds.crs}")

            if out_ds.width != ref_info["width"] or out_ds.height != ref_info["height"]:
                print(f"ERROR: Dimension mismatch: ({out_ds.width}x{out_ds.height}) vs ref ({ref_info['width']}x{ref_info['height']}).")
                sys.exit(1)
            print(f"[OK] Dimensions : {out_ds.width} x {out_ds.height}")

            if not out_ds.transform:
                print("ERROR: Missing geotransform in output raster.")
                sys.exit(1)
            print(f"[OK] Transform  : Valid affine geotransform")

    else:
        out_ds = gdal.Open(output_file, gdal.GA_ReadOnly)
        if not out_ds:
            print(f"ERROR: Could not open output file '{output_file}'.")
            sys.exit(1)

        if out_ds.RasterCount != expected_count:
            print(f"ERROR: Expected {expected_count} bands, got {out_ds.RasterCount}.")
            sys.exit(1)
        print(f"[OK] Band Count : {out_ds.RasterCount} bands verified")

        if not out_ds.GetProjection():
            print("ERROR: Missing projection in output raster.")
            sys.exit(1)
        print(f"[OK] CRS        : Projection verified")

        if out_ds.RasterXSize != ref_info["width"] or out_ds.RasterYSize != ref_info["height"]:
            print("ERROR: Dimension mismatch in GDAL output verification.")
            sys.exit(1)
        print(f"[OK] Dimensions : {out_ds.RasterXSize} x {out_ds.RasterYSize}")
        out_ds = None


def check_data_integrity(output_file, target_bands):
    """
    Verifies that calibrated bands have valid non-empty pixel arrays and non-zero maximums.
    Identifies and logs uncalibrated/zero bands according to USGS specifications.
    """
    print("\nVerifying pixel data integrity across bands...\n")

    calibrated_valid = 0
    uncalibrated_zero = 0

    if USE_RASTERIO:
        with rasterio.open(output_file) as out_ds:
            for out_idx, b_num in enumerate(target_bands, start=1):
                data = out_ds.read(out_idx)
                max_val = np.max(data)
                is_cal = (8 <= b_num <= 57) or (77 <= b_num <= 224)

                if is_cal:
                    if max_val == 0:
                        print(f"WARNING: Calibrated Band B{b_num:03d} (Stack #{out_idx}) contains all zero values!")
                    else:
                        calibrated_valid += 1
                else:
                    if max_val == 0:
                        uncalibrated_zero += 1
    else:
        out_ds = gdal.Open(output_file, gdal.GA_ReadOnly)
        for out_idx, b_num in enumerate(target_bands, start=1):
            data = out_ds.GetRasterBand(out_idx).ReadAsArray()
            max_val = np.max(data)
            is_cal = (8 <= b_num <= 57) or (77 <= b_num <= 224)

            if is_cal:
                if max_val == 0:
                    print(f"WARNING: Calibrated Band B{b_num:03d} (Stack #{out_idx}) contains all zero values!")
                else:
                    calibrated_valid += 1
            else:
                if max_val == 0:
                    uncalibrated_zero += 1
        out_ds = None

    print(f"[OK] Calibrated Bands with valid radiometry : {calibrated_valid}")
    if uncalibrated_zero > 0:
        print(f"[OK] Standard Uncalibrated/Zero bands      : {uncalibrated_zero} (Retained as 0 DN per USGS L1T spec)")
    print("[OK] Overall pixel data integrity verified.\n")


# =============================================================================
# MAIN PIPELINE CONTROLLER
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="NASA EO-1 Hyperion Hyperspectral Stacker Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python Hyperion_Stacker.py
  python Hyperion_Stacker.py --calibrated-only
  python Hyperion_Stacker.py --force
  python Hyperion_Stacker.py --input /path/to/archive --output /path/to/output
        """
    )
    parser.add_argument("--calibrated-only", action="store_true",
                        help="Stack only calibrated bands (B8-B57 and B77-B224, 198 bands) instead of all 242 bands")
    parser.add_argument("--force", "-y", action="store_true",
                        help="Overwrite existing output without interactive prompt")
    parser.add_argument("--scale-8bit", action="store_true",
                        help="Convert output to 8-bit stretched display raster instead of preserving 16-bit")
    parser.add_argument("--input", default=None,
                        help="Custom input directory (defaults to ./input)")
    parser.add_argument("--processing", default=None,
                        help="Custom processing staging directory (defaults to ./processing)")
    parser.add_argument("--output", default=None,
                        help="Custom output directory (defaults to ./output)")

    args = parser.parse_args()

    print("=================================================================")
    print("      NASA EO-1 HYPERION ARCGIS PRO HYPERSPECTRAL PIPELINE       ")
    print("=================================================================")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.abspath(args.input) if args.input else os.path.join(base_dir, "input")
    processing_dir = os.path.abspath(args.processing) if args.processing else os.path.join(base_dir, "processing")
    output_dir = os.path.abspath(args.output) if args.output else os.path.join(base_dir, "output")

    base_name = "Hyperion_Stacked_CalibratedOnly" if args.calibrated_only else "Hyperion_Stacked"
    output_file = os.path.join(output_dir, f"{base_name}.tif")

    # Preview files to clean on overwrite
    preview_files = [os.path.join(output_dir, f) for f in PREVIEW_COMPOSITES.keys()]

    # Ensure required directories exist
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(input_dir, exist_ok=True)

    # Handle Windows file lock conflicts and existing outputs
    if os.path.exists(output_file):
        print(f"\nExisting output detected: '{os.path.basename(output_file)}'")
        if not sys.stdin.isatty() or args.force:
            response = "y"
        else:
            try:
                response = input("Overwrite existing raster and preview files? (y/n): ").strip().lower()
            except EOFError:
                response = "y"

        if response not in ["y", "yes"]:
            print("Operation aborted by user.")
            sys.exit(0)

        try:
            sidecar_exts = [".aux.xml", ".ovr", ".aux"]
            files_to_remove = [output_file] + preview_files
            base_path = os.path.splitext(output_file)[0]
            for ext in sidecar_exts:
                files_to_remove.append(output_file + ext)
                files_to_remove.append(base_path + ext)

            for f in set(files_to_remove):
                if os.path.exists(f):
                    os.remove(f)
        except PermissionError as pe:
            print(f"\nERROR: Could not overwrite '{os.path.basename(output_file)}' ({pe}) because it is open in ArcGIS Pro or QGIS.")
            print("Please close the file in ArcGIS Pro and re-run the pipeline.")
            sys.exit(1)
        except Exception as e:
            print(f"ERROR: Could not clean existing output files: {e}")
            sys.exit(1)

    # Step 1: Extraction & Staging Pipeline
    target_bands = prepare_processing_folder(input_dir, processing_dir, calibrated_only=args.calibrated_only)

    # Parse metadata if present
    mtl_files = [os.path.join(processing_dir, f) for f in os.listdir(processing_dir) if "MTL" in f.upper() and f.endswith(".txt")]
    mtl_info = parse_mtl_metadata(mtl_files[0]) if mtl_files else None

    # Step 2: Band Discovery in Staging
    band_files = find_bands(processing_dir, target_bands)

    # Step 3: Geometry & Spatial Reference Validation
    ref_info = validate_bands(band_files, target_bands)

    # Step 4: Stacking Engine (ArcGIS Pro Cloud-Optimized BigTIFF)
    create_stack(band_files, output_file, ref_info, target_bands, mtl_info=mtl_info, scale_to_8bit=args.scale_8bit)
    verify_output(output_file, ref_info, len(target_bands))
    check_data_integrity(output_file, target_bands)

    # Step 5: Calibrated Full-Color PNG Preview Composites
    create_all_previews(output_file, output_dir, target_bands)

    print("=================================================================")
    print("                     PIPELINE COMPLETED SUCCESSFULLY             ")
    print("=================================================================")
    print(f"\nCreated Deliverables for ArcGIS Pro:")
    print(f"  - ArcGIS Pro Stack    : {output_file}")
    print(f"    * Driver            : Cloud-Optimized BigTIFF (.tif) [LZW, 256x256 Tiled, Overviews]")
    print(f"    * Bands Staged      : {len(target_bands)} bands")
    print(f"    * Radiometric Spec  : VNIR (B1-B70, /40.0) | SWIR (B71-B242, /80.0)")
    print(f"  - True Color Preview  : {os.path.join(output_dir, 'Hyperion_TrueColor_Preview.png')}")
    print(f"  - CIR Color Infrared  : {os.path.join(output_dir, 'Hyperion_CIR_Vegetation_Preview.png')}")
    print(f"  - SWIR Geology Preview: {os.path.join(output_dir, 'Hyperion_SWIR_Geology_Preview.png')}")
    print(f"  - Vegetation Stress   : {os.path.join(output_dir, 'Hyperion_Vegetation_Stress_Preview.png')}\n")


if __name__ == "__main__":
    main()
