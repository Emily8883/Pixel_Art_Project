# AGENTS.md

This repository contains Pixel Asset Extractor, a Python 3.11 desktop application built with PySide6.

## Working Notes

- Keep edits ASCII unless a file already requires Unicode.
- Prefer `apply_patch` for source changes.
- Preserve exact pixel values when exporting crops.
- Do not add automatic background removal yet.
- Use `Path` for file handling so Windows paths work reliably.

## Project Layout

- `src/pixel_asset_extractor/` contains the application package.
- `tests/` contains pytest coverage for config and image processing.
- `config/` may store saved crop JSON files.
- `output/` is for exported PNG crops.
