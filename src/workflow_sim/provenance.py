"""Content identity of the installed runtime, independent of a source checkout."""
import hashlib
import importlib.metadata
from pathlib import Path
import platform
from . import __version__
from .contracts import digest

def provenance():
    root = Path(__file__).parent
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*.py'))}
    return {'version': __version__, 'library_sha256': digest(files), 'library_files': files,
            'python': platform.python_version(), 'platform': platform.platform(),
            'dependencies': {name: importlib.metadata.version(name) for name in ('celery', 'kombu', 'billiard', 'time-machine')}}

