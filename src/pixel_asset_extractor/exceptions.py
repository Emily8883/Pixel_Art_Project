from __future__ import annotations


class PixelAssetExtractorError(Exception):
    """Base error for the application."""


class ImageLoadError(PixelAssetExtractorError):
    """Raised when an image cannot be loaded."""


class CropError(PixelAssetExtractorError):
    """Raised when a crop operation is invalid."""


class ConfigError(PixelAssetExtractorError):
    """Raised when a configuration file is invalid."""
