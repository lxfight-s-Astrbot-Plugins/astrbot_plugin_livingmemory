from __future__ import annotations

from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR_NAME = "astrbot_plugin_livingmemory"
OUTPUT_ZIP = ROOT / f"{PLUGIN_DIR_NAME}.zip"

INCLUDE_ROOT_FILES = [
    "main.py",
    "metadata.yaml",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "_conf_schema.json",
    "logo.png",
]

INCLUDE_DIRS = [
    "core",
    "storage",
    "webui",
    "static",
]

EXCLUDE_PARTS = {
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    "data",
    "tests",
    ".claude",
    "htmlcov",
}

EXCLUDE_NAMES = {
    ".coverage",
    "pytest_output.txt",
    "CLAUDE.md",
    "run_test.sh",
    "需要解决的问题.txt",
}

EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def should_skip(path: Path) -> bool:
    if any(part in EXCLUDE_PARTS for part in path.parts):
        return True
    if path.name in EXCLUDE_NAMES:
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    return False


def iter_files() -> list[Path]:
    files: list[Path] = []

    for relative_path in INCLUDE_ROOT_FILES:
        path = ROOT / relative_path
        if path.is_file() and not should_skip(path.relative_to(ROOT)):
            files.append(path)

    for relative_dir in INCLUDE_DIRS:
        base_dir = ROOT / relative_dir
        if not base_dir.exists():
            continue
        for path in sorted(base_dir.rglob("*")):
            if path.is_file() and not should_skip(path.relative_to(ROOT)):
                files.append(path)

    return files


def build_zip() -> Path:
    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()

    files = iter_files()
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr(f"{PLUGIN_DIR_NAME}/", "")
        for path in files:
            relative_path = path.relative_to(ROOT).as_posix()
            archive_path = f"{PLUGIN_DIR_NAME}/{relative_path}"
            zip_file.write(path, archive_path)

    return OUTPUT_ZIP


if __name__ == "__main__":
    output_zip = build_zip()
    with zipfile.ZipFile(output_zip) as zip_file:
        names = zip_file.namelist()
    print(f"Built: {output_zip.name}")
    print(f"Files: {len(names)}")
    for name in names[:80]:
        print(name)