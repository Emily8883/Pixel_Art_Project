# Pixel Asset Extractor

Pixel Asset Extractor is a Python 3.11 desktop app for extracting exact PNG crops from large pixel-art reference sheets.

## Features

- Open PNG reference sheets
- Zoom and pan the source canvas
- Draw a manual crop rectangle
- Preview the raw crop and cleaned crop
- Pick a background color with an eyedropper
- Adjust tolerance with a synced slider and spin box
- Remove connected background pixels with 4-way or 8-way connectivity
- Preview before, after, and split comparison modes
- Save and load versioned project JSON files
- Export raw and cleaned PNGs without resizing

## Install

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Launch

```bash
python main.py
```

## Test

```bash
pytest
```

## Notes

- Clean exports are RGBA PNGs with transparency preserved.
- Preview backgrounds and checkerboards are for inspection only and are never exported.
- Old version-1 crop configs still load.
