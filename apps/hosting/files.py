"""
Small, UI-neutral helpers for the File Manager screens: limits, breadcrumbs, and what may be opened in the editor.
The real safety checks (paths that stay inside the website's folder) live in the provider's sandbox, not here.
"""
from .adapters.sandbox import clean_relative

MAX_EDIT_BYTES = 1_000_000  # the in-browser editor opens files up to about 1 MB
MAX_UPLOAD_BYTES = 10_000_000
MAX_DOWNLOAD_BYTES = 25_000_000

EDITABLE_EXTENSIONS = {
    ".html", ".htm", ".css", ".js", ".json", ".txt", ".md", ".xml", ".svg", ".php", ".py", ".ini", ".conf",
    ".htaccess", ".env", ".csv", ".yml", ".yaml", ".log", ".sql", ".sh", ".ts", ".scss", ".less",
}
EDITABLE_NAMES = {".htaccess", ".htpasswd", "robots.txt", "readme", "license", "makefile"}


def normalise(path) -> str:
    """The path as the provider will see it, or "" for the website's main folder. Raises HostingError if unsafe."""
    return clean_relative(path)


def parent_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def breadcrumbs(path: str):
    """[(label, path)] from the main folder down to ``path``."""
    crumbs = [("Website files", "")]
    walked = ""
    for part in [p for p in path.split("/") if p]:
        walked = join(walked, part)
        crumbs.append((part, walked))
    return crumbs


def is_editable_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in EDITABLE_NAMES:
        return True
    dot = lowered.rfind(".")
    return dot != -1 and lowered[dot:] in EDITABLE_EXTENSIONS


def decode_text(data: bytes):
    """The text of a file, or None when it is not valid UTF-8 text (so the editor never mangles a binary file)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
