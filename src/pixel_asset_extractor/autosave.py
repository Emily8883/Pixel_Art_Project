from __future__ import annotations

from pathlib import Path

from .project_model import SpriteProject
from .project_store import load_project, save_project


def autosave_path(project_path: str | Path) -> Path:
    path = Path(project_path)
    return path.with_suffix(path.suffix + ".autosave")


def save_autosave(project: SpriteProject) -> Path:
    if project.path is None:
        raise ValueError("Project has no known path")
    path = autosave_path(project.path)
    save_project(project, path)
    return path


def has_newer_autosave(project_path: str | Path) -> bool:
    autosave = autosave_path(project_path)
    main = Path(project_path)
    return autosave.exists() and (not main.exists() or autosave.stat().st_mtime > main.stat().st_mtime)


def recover_autosave(project_path: str | Path) -> SpriteProject:
    return load_project(autosave_path(project_path))

