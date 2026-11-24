# 🛰️ Hyperion Stacker

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Sensor](https://img.shields.io/badge/sensor-NASA%20EO--1%20Hyperion-0b3d91.svg)](https://www.usgs.gov/centers/eros/science/earth-observing-1-eo-1)
[![Format](https://img.shields.io/badge/format-ERDAS%20Imagine%20(.img)-orange.svg)](https://www.hexagon.com/)
[![GIS Support](https://img.shields.io/badge/GIS-ERDAS%20%7C%20ArcGIS%20Pro%20%7C%20QGIS%20%7C%20ENVI-blueviolet.svg)](#software-compatibility)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A production-ready, fully automated Python pipeline to unpack, validate, calibrate, and stack **NASA EO-1 Hyperion hyperspectral** single-band GeoTIFFs into an **ERDAS Imagine HFA (`.img`)** multi-band raster. 

Features multi-archive extraction (`.tar.gz`, `.tar`, `.zip`, `.rar`, `.7z`), automated staging, strict geometric validation, VNIR/SWIR radiometric scaling factor embedding ($40.0$ and $80.0$), ERDAS-compliant statistics and histograms, fast overview pyramid generation (`[2, 4, 8, 16, 32, 64]`), and 4 calibrated publication-grade remote sensing PNG composites matching the visual contrast of **ERDAS IMAGINE 2025**.

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
   │ 4. Hyperspectral Stacking & ERDAS Imagine (.img) Generation                │
   │    • Native HFA driver preserving 16-bit integer radiometric data          │
   │    • Embeds VNIR (/40.0) & SWIR (/80.0) scale factors, wavelengths & FWHM │
   │    • Computes ERDAS-compliant tags: STATISTICS_MINIMUM, MAXIMUM, MEAN,     │
   │      STDDEV, MEDIAN, MODE, HISTOBINVALUES, EXCLUDEDVALUES="0", athematic   │
   │    • Builds internal pyramids [2, 4, 8, 16, 32, 64] (average resampling)   │
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
├── Hyperion_Stacker.py            # Main automated pipeline script
├── compare_hyperion_stacks.py     # Verification & spectral profile comparison tool
├── README.md                      # Complete documentation & band table
│
├── input/                         # Drop folder for archives (.tar.gz, .zip, etc.) or loose GeoTIFFs
│   └── .gitkeep
│
├── processing/                    # Automated staging folder for isolated B001–B242 GeoTIFFs
│   └── .gitkeep
│
└── output/                        # Stacked raster and hyperspectral preview composites
    ├── Hyperion_Stacked.img       # 242-band (or 198-band) 16-bit ERDAS Imagine raster stack
    ├── Hyperion_Stacked.img.aux.xml
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
- **matplotlib** (optional, used by `compare_hyperion_stacks.py` for plotting spectral curves)

### Quick Install (Windows / macOS / Linux)

Open PowerShell or terminal and run:

```cmd
pip install rasterio numpy pillow matplotlib
```

> **Archive Extraction Support**:
> - `.tar.gz`, `.tar`, and `.zip` archives are unpacked automatically using Python's built-in libraries.
> - `.rar` archives automatically detect installed WinRAR (`C:\Program Files\WinRAR\UnRAR.exe`).
> - `.7z` archives use installed 7-Zip (`C:\Program Files\7-Zip\7z.exe`).

---

## 🚀 Quick Start Guide

### 1. Place Input Data
Download a NASA EO-1 Hyperion scene from [USGS EarthExplorer](https://earthexplorer.usgs.gov/) (EO-1 Hyperion Level 1T / 1GST / 1R) and place the archive directly into `input/`:
- **Archives supported**: `EO1H*.tar.gz`, `EO1H*.tar`, `EO1H*.zip`, `EO1H*.rar`, `EO1H*.7z`
- **Loose files supported**: Folder containing `*_B001_*.TIF` through `*_B242_*.TIF` and `*_MTL.txt`

| **`output/Hyperion_Stacked.img`** | Full 16-bit 242-band ERDAS Imagine HFA raster stack with embedded statistics & overviews |
| **`output/Hyperion_Stacked.tif`** | ArcGIS Pro / ArcMap optimized Cloud-Optimized BigTIFF with lossless LZW compression & 256x256 internal tiling |
| **`output/Hyperion_TrueColor_Preview.png`** | Natural True Color RGB composite (Bands 29 - 20 - 13) |
| **`output/Hyperion_CIR_Vegetation_Preview.png`** | Color Infrared / CIR composite (Bands 50 - 30 - 20) |
| **`output/Hyperion_SWIR_Geology_Preview.png`** | Shortwave Infrared mineral / geology composite (Bands 204 - 150 - 50) |
| **`output/Hyperion_Vegetation_Stress_Preview.png`** | Red-edge & vegetation chlorophyll stress composite (Bands 53 - 35 - 22) |

---

## 🚀 Quick Start Guide

### 1. Place Input Data
Download a NASA EO-1 Hyperion scene from [USGS EarthExplorer](https://earthexplorer.usgs.gov/) and place the archive directly into `input/`:
- **Archives supported**: `EO1H*.tar.gz`, `EO1H*.tar`, `EO1H*.zip`, `EO1H*.rar`, `EO1H*.7z`
- **Loose files supported**: Folder containing `*_B001_*.TIF` through `*_B242_*.TIF` and `*_MTL.txt`

### 2. Run the Stacker Pipeline

#### Default: Generate Both ERDAS Imagine (.img) and ArcGIS Pro BigTIFF (.tif)
```cmd
python Hyperion_Stacker.py
```

#### Option: Generate Specifically for ArcGIS Pro (.tif with LZW Compression & Internal Tiling)
```cmd
python Hyperion_ArcGIS_Stacker.py
# Or via main script:
python Hyperion_Stacker.py --format tif
```

#### Option: Stack Calibrated Bands Only (198 Bands)
Excludes zero-value uncalibrated bands (B1–B7, B58–B76, B225–B242):
```cmd
python Hyperion_Stacker.py --calibrated-only
```

#### Additional CLI Options:
- `--format {img,tif,both}`: Select output raster format (default: `both`).
- `--force` or `-y`: Overwrite existing outputs without confirmation prompt.
- `--scale-8bit`: Generate an 8-bit stretched display stack instead of preserving 16-bit radiometry.
- `--input <path>`: Specify a custom input directory.
- `--output <path>`: Specify a custom output directory.

### 3. Generated Deliverables
The pipeline automatically produces in `output/`:
1. **`Hyperion_Stacked.img`**: Full 16-bit hyperspectral ERDAS Imagine HFA raster with embedded scale factors, band wavelengths, ERDAS statistics, and overviews.
2. **`Hyperion_TrueColor_Preview.png`**: Natural human perception RGB composite.
3. **`Hyperion_CIR_Vegetation_Preview.png`**: Standard Color Infrared (CIR) composite highlighting canopy density.
4. **`Hyperion_SWIR_Geology_Preview.png`**: Mineral and lithological alteration composite.
5. **`Hyperion_Vegetation_Stress_Preview.png`**: Red-edge and physiological chlorophyll stress composite.

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

## 🎨 Automated Hyperspectral Preview Composites

| Preview Composite | Band Assignment | Center Wavelengths | Remote Sensing & Geospatial Application |
| :--- | :---: | :---: | :--- |
| **`Hyperion_TrueColor_Preview.png`** | **Red: B29<br>Green: B20<br>Blue: B13** | Red: ~640.5 nm<br>Green: ~549.0 nm<br>Blue: ~477.7 nm | **Natural Color**: Approximates human visual perception of land cover, terrain, and coastal features. |
| **`Hyperion_CIR_Vegetation_Preview.png`** | **Red: B50<br>Green: B30<br>Blue: B20** | Red: ~854.2 nm<br>Green: ~650.7 nm<br>Blue: ~549.0 nm | **Color Infrared (CIR / FCC)**: Healthy vegetation appears vivid crimson red; water bodies appear dark blue to black. |
| **`Hyperion_SWIR_Geology_Preview.png`** | **Red: B204<br>Green: B150<br>Blue: B50** | Red: ~2193.3 nm<br>Green: ~1648.9 nm<br>Blue: ~854.2 nm | **SWIR Mineral & Lithology**: Diagnostic for clay alteration, alunite, kaolinite, carbonates, and soil discrimination. |
| **`Hyperion_Vegetation_Stress_Preview.png`** | **Red: B53<br>Green: B35<br>Blue: B22** | Red: ~884.7 nm<br>Green: ~701.5 nm<br>Blue: ~569.3 nm | **Vegetation Stress & Red Edge**: Uses the steep chlorophyll red-edge reflectance shift (~700 nm) to detect crop drought and blight. |

---

## 🔍 Spectral Verification Tool (`compare_hyperion_stacks.py`)

A specialized hyperspectral comparison tool is included to compare any two `.img` stacks:

```cmd
python compare_hyperion_stacks.py output/Hyperion_Stacked.img reference/reference_stack.img
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

```cmd
# Custom pixel sampling:
python compare_hyperion_stacks.py stack1.img stack2.img --points "150,200;300,450" --save-plot comparison.png
```

---

## 🛠️ Software Compatibility

The stacked `.img` raster uses the native GDAL `HFA` driver with ERDAS IMAGINE standard statistics, histograms, and overviews:

- **ERDAS IMAGINE** (2020, 2022, 2025): Immediate contrast rendering, spectral profile tool, metadata viewing.
- **ArcGIS Pro / ArcMap**: Native support with automated pyramid acceleration.
- **QGIS (3.x)**: Full multispectral/hyperspectral band exploration and Profile Tool plugin support.
- **ENVI**: Full spectral library and continuous wavelength profile support.

---

## 📄 License
This project is open-source and released under the [MIT License](LICENSE).
