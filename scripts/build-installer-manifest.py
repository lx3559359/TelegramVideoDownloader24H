"""Generate the static update manifest from a completed installer artifact."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from tg_video_downloader.installer_update import SITE, parse_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('installer', type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--notes', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    expected = f'TelegramVideoDownloader-v{args.version}-Windows-x64-Setup.exe'
    if args.installer.name != expected:
        parser.error('Installer filename must match version')
    with args.installer.open('rb') as file:
        digest = hashlib.file_digest(file, 'sha256').hexdigest()
    value = dict(schema=1, version=args.version, url=f'{SITE}/downloads/{expected}',
                 size=args.installer.stat().st_size, sha256=digest, notes=args.notes)
    parse_manifest(value)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Manifest written: {args.output}')


if __name__ == '__main__':
    main()
