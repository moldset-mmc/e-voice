"""Write interoperable SHA-256 sums for all release packages."""
import hashlib
from pathlib import Path
import sys


def write(directory):
    packages = sorted(path for path in directory.glob('E-Voice-*') if path.name.endswith(('.exe', '.pkg', '.deb', '.tar.gz')))
    if not packages:
        raise ValueError('No release packages found')
    lines = []
    for path in packages:
        with path.open('rb') as stream:
            lines.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}\n")
    (directory / 'SHA256SUMS.txt').write_text(''.join(lines), encoding='ascii', newline='\n')


if __name__ == '__main__':
    write(Path(sys.argv[1]))
