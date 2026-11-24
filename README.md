# 🛰️ Hyperion Stacker for ArcGIS Pro

[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Sensor](https://img.shields.io/badge/sensor-NASA%20EO--1%20Hyperion-0b3d91.svg)](https://www.usgs.gov/centers/eros/science/earth-observing-1-eo-1)
[![Format](https://img.shields.io/badge/format-ArcGIS%20Pro%20BigTIFF%20(.tif)-brightgreen.svg)](https://www.esri.com/en-us/arcgis/products/arcgis-pro/overview)
[![GIS Support](https://img.shields.io/badge/GIS-ArcGIS%20Pro%20%7C%20ArcMap%20%7C%20QGIS%20%7C%20ENVI-blueviolet.svg)](#software-compatibility)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A production-ready, fully automated Python pipeline to unpack, validate, calibrate, and stack **NASA EO-1 Hyperion hyperspectral** single-band GeoTIFFs into an **ArcGIS Pro Cloud-Optimized BigTIFF (`.tif`)** multi-band raster with embedded band wavelengths and 4 calibrated preview composites.

---

## ⚡ ArcGIS Pro Optimizations

The pipeline is tuned specifically for maximum performance and compatibility with **ArcGIS Pro** and **ArcMap**:

* **Cloud-Optimized BigTIFF (`GTiff`, `BIGTIFF=IF_SAFER`)**: Breaks the 4 GB GeoTIFF barrier while remaining natively readable by ArcGIS Pro without needing external format conversions.
* **Lossless LZW Compression with Horizontal Differencing (`PREDICTOR=2`)**: Reduces hyperspectral file size by **~60–70%** (e.g., from ~1.8 GB uncompressed down to ~450 MB) with zero radiometric data loss.
* **$256 \times 256$ Internal Tiling (`TILED=YES`)**: Enables instantaneous hardware-accelerated panning, zooming, and dynamic contrast stretching across all 242 bands.
* **Embedded Band Descriptions (`set_band_description`)**: Each band is labeled as `B029 (640.5nm)`, `B050 (854.2nm)`, etc., allowing ArcGIS Pro's **Contents Pane** and **Spectral Profile Chart** to automatically recognize band numbers and center wavelengths.
* **Multi-Threaded Overview Pyramids (`GDAL_NUM_THREADS="ALL_CPUS"`)**: Generates overview pyramids (`[2, 4, 8, 16, 32]`) using all CPU cores in parallel, preventing UI freezes when opening large hyperspectral scenes.
* **Embedded GDAL PAM Statistics**: Pre-computes minimum, maximum, mean, standard deviation, and histograms (`.aux.xml`) for instant initial display in ArcGIS Pro.

---

## 🔬 Sensor & Data Specifications

The **NASA Earth Observing-1 (EO-1) Hyperion** sensor was the first civilian spaceborne imaging spectrometer capable of providing continuous spectral coverage across 242 channels:

* **Spatial Resolution**: 30 m ground sample distance (GSD).
* **Spectral Resolution**: 242 spectral bands covering 356 nm to 2577 nm with ~10 nm bandwidth.
* **VNIR Spectrometer**: Bands 1–70 (355.59 nm to 1057.65 nm).
  * **Radiometric Scaling**: $\text{Radiance } L = \frac{\text{DN}}{40.0} \; \left[\frac{\text{W}}{\text{m}^2 \cdot \text{sr} \cdot \mu\text{m}}\right]$
  * **Calibrated Bands**: Bands 8–57 (426.82 nm to 925.41 nm).
  * **Uncalibrated / Zero Bands**: Bands 1–7 and overlap bands 58–70.
* **SWIR Spectrometer**: Bands 71–242 (851.92 nm to 2577.08 nm).
  * **Radiometric Scaling**: $\text{Radiance } L = \frac{\text{DN}}{80.0} \; \left[\frac{\text{W}}{\text{m}^2 \cdot \text{sr} \cdot \mu\text{m}}\right]$
  * **Calibrated Bands**: Bands 77–224 (912.45 nm to 2395.53 nm).
  * **Uncalibrated / Zero Bands**: Overlap bands 71–76 and detector noise bands 225–242.
* **Atmospheric Water Vapor Absorption Windows**:
  * **$1400\text{ nm}$ Window**: Bands 121–130 (~1356 nm to 1447 nm)
  * **$1900\text{ nm}$ Window**: Bands 166–180 (~1800 nm to 1952 nm)
* **Standard Stack**: 242-band full USGS stack (or 198-band calibrated subset via `--calibrated-only`).

---

## 🏗️ Architecture & Pipeline Workflow

```text
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             NASA EO-1 HYPERION PIPELINE                          │
└──────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
   ┌────────────────────────────────────────────────────────────────────────────┐
   │ 1. Ingestion & Multi-Format Archive Extraction                             │
   │    • Drag & drop into input/ (.tar.gz, .tgz, .tar, .zip, .rar, .7z)       │
   │    • Cascading extractor: tarfile/zipfile -> WinRAR -> tar -> 7z -> PS     │
   └────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
   ┌────────────────────────────────────────────────────────────────────────────┐
   │ 2. Automated Staging & Metadata Parsing (processing/)                      │
   │    • Regex filter isolates Hyperion bands: B001 to B242 (.TIF)             │
   │    • Parses *MTL.txt metadata for acquisition details & scaling factors    │
   └────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
   ┌────────────────────────────────────────────────────────────────────────────┐
   │ 3. Strict Geometric & Spatial Validation                                   │
   │    • Dual engine: rasterio with osgeo.gdal fallback                        │
   │    • Verifies width, height, CRS, and geotransform (tolerance <= 1e-5)    │
   └────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
   ┌────────────────────────────────────────────────────────────────────────────┐
   │ 4. ArcGIS Pro BigTIFF Stacking (.tif)                                      │
   │    • Cloud-Optimized BigTIFF preserving 16-bit radiometric fidelity       │
   │    • Lossless LZW compression with predictor 2 (~60-70% size reduction)    │
   │    • 256x256 internal tiling for GPU viewport acceleration in ArcGIS Pro   │
   │    • Embeds band descriptions: "B029 (640.5nm)" for Spectral Profile Charts│
   │    • Parallel overview generation [2, 4, 8, 16, 32] via ALL_CPUS          │
   └────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
   ┌────────────────────────────────────────────────────────────────────────────┐
   │ 5. Automated 4-Composite Calibrated Preview Generation                     │
   │    • 2% to 98% percentile stretch with gamma (1 / 1.3) mid-tone tuning     │
   │    • True Color, CIR Infrared, SWIR Geology, and Vegetation Stress         │
   └────────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Directory Structure

```text
hyperion stacker/
│
├── Hyperion_Stacker.py            # Main automated pipeline script (ArcGIS Pro output)
├── Hyperion_ArcGIS_Stacker.py     # Standalone convenience runner for ArcGIS Pro
├── compare_hyperion_stacks.py     # Verification & spectral profile comparison tool
├── test_hyperion_stacker.py       # Full automated test suite
├── README.md                      # Complete documentation & band table
│
├── input/                         # Drop folder for archives (.tar.gz, .zip, etc.) or loose GeoTIFFs
│   └── .gitkeep
│
├── processing/                    # Automated staging folder for isolated B001–B242 GeoTIFFs
│   └── .gitkeep
│
└── output/                        # Stacked raster and hyperspectral preview composites
    ├── Hyperion_Stacked.tif       # 242-band 16-bit ArcGIS Pro Cloud-Optimized BigTIFF
    ├── Hyperion_Stacked.tif.aux.xml
    ├── Hyperion_TrueColor_Preview.png
    ├── Hyperion_CIR_Vegetation_Preview.png
    ├── Hyperion_SWIR_Geology_Preview.png
    └── Hyperion_Vegetation_Stress_Preview.png
```

---

## ⚙️ Prerequisites & Installation

### Requirements
- **Python 3.10+** (Tested on Python 3.11, 3.12, 3.13)
- **rasterio** or **GDAL**
- **numpy**
- **pillow**
- **matplotlib** (optional, used by `compare_hyperion_stacks.py` for spectral curve plotting)

### Quick Install (Windows / macOS / Linux)

Open PowerShell or terminal and run:

```cmd
pip install rasterio numpy pillow matplotlib
```

---

## 🚀 Quick Start Guide

### 1. Place Input Data
Download a NASA EO-1 Hyperion scene from [USGS EarthExplorer](https://earthexplorer.usgs.gov/) (EO-1 Hyperion Level 1T / 1GST / 1R) and place the archive directly into `input/`:
- **Archives supported**: `EO1H*.tar.gz`, `EO1H*.tar`, `EO1H*.zip`, `EO1H*.rar`, `EO1H*.7z`
- **Loose files supported**: Folder containing `*_B001_*.TIF` through `*_B242_*.TIF` and `*_MTL.txt`

### 2. Run the Stacker Pipeline

#### Standard Execution (Full 242 Bands)
```cmd
python Hyperion_Stacker.py
```

#### Calibrated Bands Only (198 Bands)
Excludes zero-value uncalibrated bands (B1–B7, B58–B76, B225–B242):
```cmd
python Hyperion_Stacker.py --calibrated-only
```

#### Additional CLI Options:
- `--force` or `-y`: Overwrite existing output without confirmation prompt.
- `--scale-8bit`: Generate an 8-bit stretched display stack instead of preserving 16-bit radiometry.
- `--input <path>`: Specify a custom input directory.
- `--processing <path>`: Specify a custom staging directory.
- `--output <path>`: Specify a custom output directory.

---

## 🗺️ How to Use the Stacked Raster in ArcGIS Pro

1. **Add Data**:
   - Open ArcGIS Pro, click **Add Data**, and select `output/Hyperion_Stacked.tif`.
   - The raster renders instantly thanks to the embedded overviews and $256 \times 256$ tiles.

2. **Select Band Combinations (Symbology Pane)**:
   - In the **Contents** pane, right-click `Hyperion_Stacked.tif` $\rightarrow$ **Symbology**.
   - Set **Primary Symbology** to **RGB Video / RGB Composite**:
     - **True Color**: Red = Band 29 (640.5 nm), Green = Band 20 (549.0 nm), Blue = Band 13 (477.7 nm).
     - **Color Infrared (CIR)**: Red = Band 50 (854.2 nm), Green = Band 30 (650.7 nm), Blue = Band 20 (549.0 nm).
     - **SWIR Geology**: Red = Band 204 (2193.3 nm), Green = Band 150 (1648.9 nm), Blue = Band 50 (854.2 nm).

3. **Plot Continuous Spectral Profiles**:
   - Select `Hyperion_Stacked.tif` in the **Contents** pane.
   - On the top ribbon, navigate to **Imagery** $\rightarrow$ **Spectral Profile Chart**.
   - Click any pixel (e.g. vegetation canopy, water body, or rock outcrop) on the map.
   - ArcGIS Pro will automatically draw the 242-band continuous spectral reflectance curve using the embedded wavelength metadata!

---

## 🎨 Automated Hyperspectral Preview Composites

| Preview Composite | Band Assignment | Center Wavelengths | Remote Sensing Application |
| :--- | :---: | :---: | :--- |
| **`Hyperion_TrueColor_Preview.png`** | **Red: B29<br>Green: B20<br>Blue: B13** | Red: ~640.5 nm<br>Green: ~549.0 nm<br>Blue: ~477.7 nm | **Natural Color**: Approximates human visual perception of land cover and terrain. |
| **`Hyperion_CIR_Vegetation_Preview.png`** | **Red: B50<br>Green: B30<br>Blue: B20** | Red: ~854.2 nm<br>Green: ~650.7 nm<br>Blue: ~549.0 nm | **Color Infrared (CIR)**: Healthy vegetation appears vivid crimson red; water appears dark blue/black. |
| **`Hyperion_SWIR_Geology_Preview.png`** | **Red: B204<br>Green: B150<br>Blue: B50** | Red: ~2193.3 nm<br>Green: ~1648.9 nm<br>Blue: ~854.2 nm | **SWIR Mineral & Lithology**: Diagnostic for clay alteration, alunite, kaolinite, and carbonates. |
| **`Hyperion_Vegetation_Stress_Preview.png`** | **Red: B53<br>Green: B35<br>Blue: B22** | Red: ~884.7 nm<br>Green: ~701.5 nm<br>Blue: ~569.3 nm | **Vegetation Stress & Red Edge**: Uses the steep chlorophyll red-edge reflectance shift (~700 nm) to detect crop drought. |

---

## 📊 Comprehensive Hyperion Band Mapping Table

| Band Range | Hyperion Band # | Center Wavelength | Spectrometer | Scale Factor | Status & Calibration Role |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **B001 – B007** | Bands 1–7 | 355.6 – 416.6 nm | VNIR | $40.0$ | **Uncalibrated**: Zero-fill bands in USGS distributions |
| **B008 – B057** | Bands 8–57 | 426.8 – 925.4 nm | VNIR | $40.0$ | **Calibrated VNIR** (50 bands): Visible & NIR science |
| **B058 – B070** | Bands 58–70 | 935.6 – 1057.7 nm | VNIR | $40.0$ | **Uncalibrated Overlap**: Detector overlap with SWIR |
| **B071 – B076** | Bands 71–76 | 851.9 – 902.4 nm | SWIR | $80.0$ | **Uncalibrated Overlap**: Detector overlap with VNIR |
| **B077 – B120** | Bands 77–120 | 912.5 – 1346.3 nm | SWIR | $80.0$ | **Calibrated SWIR-1**: Biomass, water index, shortwave |
| **B121 – B130** | Bands 121–130 | 1356.4 – 1447.3 nm | SWIR | $80.0$ | ⚠️ **Atmospheric Water Vapor Absorption (~1400 nm)** |
| **B131 – B165** | Bands 131–165 | 1457.3 – 1790.1 nm | SWIR | $80.0$ | **Calibrated SWIR-1**: Moisture, snow/ice, mineralogy |
| **B166 – B180** | Bands 166–180 | 1800.2 – 1951.6 nm | SWIR | $80.0$ | ⚠️ **Atmospheric Water Vapor Absorption (~1900 nm)** |
| **B181 – B224** | Bands 181–224 | 1961.7 – 2395.5 nm | SWIR | $80.0$ | **Calibrated SWIR-2**: Clays, carbonates, hydrothermal alteration |
| **B225 – B242** | Bands 225–242 | 2405.6 – 2577.1 nm | SWIR | $80.0$ | **Uncalibrated / Low SNR**: Terminated SWIR detector bands |

---

## 🔍 Spectral Verification Tool (`compare_hyperion_stacks.py`)

A specialized hyperspectral comparison tool is included to verify and cross-compare stacked rasters:

```cmd
python compare_hyperion_stacks.py output/Hyperion_Stacked.tif reference/reference_stack.tif
```

### Capabilities:
- **Geometry & CRS Check**: Verifies band counts, dimensions, projection, and geotransform.
- **Diagnostic Band Statistics**: Compares distributions across key bands (`B13`, `B20`, `B29`, `B50`, `B150`, `B204`).
- **Automated Spectral Profile Extraction**:
  - Auto-locates healthy vegetation, soil, and center pixels.
  - Converts Digital Numbers (DN) to spectral radiance $L$ using VNIR/SWIR scale factors:
    $$L_{\text{VNIR}} = \frac{\text{DN}}{40.0}, \quad L_{\text{SWIR}} = \frac{\text{DN}}{80.0}$$
  - Graphs full continuous spectral signatures (400–2450 nm) with shaded water vapor absorption gaps (~1400 nm and ~1900 nm).
  - Exports a high-resolution PNG figure (`hyperion_spectral_comparison.png`) and tabular data (`hyperion_spectral_profiles.csv`).

---

## 🛠️ Software Compatibility

The stacked `.tif` raster is a 100% standard GeoTIFF/BigTIFF compatible with:

- **ArcGIS Pro** (2.x, 3.x) & **ArcMap**: Full multi-band symbology and Spectral Profile Chart support.
- **QGIS (3.x)**: Full multispectral/hyperspectral band exploration and Profile Tool plugin support.
- **ENVI**: Direct drag-and-drop continuous spectral wavelength analysis.
- **Python / GDAL / Rasterio**: Direct array access and geospatial analysis.

---

## 📄 License
This project is open-source and released under the [MIT License](LICENSE).
