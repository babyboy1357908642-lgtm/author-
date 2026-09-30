"""Create a source-only deployment archive, without Git or runtime data."""
import io
import zipfile
from pathlib import Path

ROOT_FILES = (
    ".env.example", ".gitignore", ".dockerignore", "Dockerfile",
    "README.md", "RAILWAY.md", "railway.json", "requirements.txt",
    "requirements.lock", "main.py", "run.py",
)


def source_zip(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    paths = [root / name for name in ROOT_FILES]
    package = root / "auction_bot"
    if not package.is_symlink():
        paths.extend(package.rglob("*.py"))
    archive = io.BytesIO()
    archive.name = "auth-bot-code.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(paths):
            relative = path.relative_to(root)
            # Reject links at every level, including links to private runtime files.
            if any((root / Path(*relative.parts[:i])).is_symlink()
                   for i in range(1, len(relative.parts) + 1)):
                continue
            if "__pycache__" in relative.parts or not path.is_file():
                continue
            output.write(path, "auth-bot/" + relative.as_posix())
    archive.seek(0)
    return archive
