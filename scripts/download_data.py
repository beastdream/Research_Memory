"""Download small verified HF files and the official SciFact archive.

Run from repo root: python scripts/download_data.py
Use --with-longmemeval for optional 15 MB pilot benchmark.
Never downloads full 105 GB unarXive archives.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests
from huggingface_hub import HfApi, hf_hub_download

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'datasets' / 'raw'
MANIFEST = ROOT / 'datasets' / 'manifests' / 'downloads.jsonl'
SCIFACT_URL = 'https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz'


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def log_file(path: Path, source: str, revision: str = ''):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    record = {
        'source': source, 'revision': revision,
        'path': str(path.relative_to(ROOT)),
        'size_bytes': path.stat().st_size, 'sha256': digest(path),
        'downloaded_at_utc': datetime.now(timezone.utc).isoformat()
    }
    with MANIFEST.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    print(f'OK: {path.relative_to(ROOT)} ({record["size_bytes"]:,} bytes)')


def hf_download(repo: str, filename: str, local_subfolder: str, revision: str):
    location = RAW / local_subfolder
    location.mkdir(parents=True, exist_ok=True)
    path = Path(hf_hub_download(
        repo_id=repo, repo_type='dataset', filename=filename,
        revision=revision, local_dir=location
    ))
    log_file(path, repo, revision)


def pick_hf_file(repo: str, choices: list[str], revision: str = "main") -> tuple[str, str]:
    # Freeze the exact repository commit used to download this file.
    api = HfApi()
    info = api.repo_info(repo_id=repo, repo_type='dataset', revision=revision)
    filenames = set(api.list_repo_files(repo_id=repo, repo_type='dataset', revision=info.sha))
    for candidate in choices:
        if candidate in filenames:
            return candidate, info.sha
    raise RuntimeError(f'No expected file found in {repo} at {revision}; check Files tab. Candidates: {choices}')


def download_scifact():
    folder = RAW / 'scifact'
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / 'data.tar.gz'
    if not archive.exists() or archive.stat().st_size == 0:
        print('Downloading SciFact archive...')
        tmp = folder / 'data.tar.gz.part'
        with requests.get(SCIFACT_URL, stream=True, timeout=(20, 120)) as response:
            response.raise_for_status()
            with tmp.open('wb') as file:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if block:
                        file.write(block)
        tmp.replace(archive)
    log_file(archive, SCIFACT_URL)
    with tarfile.open(archive, mode='r:gz') as tar:
        # Never blindly extract paths or links from an archive.
        members = tar.getmembers()
        base = folder.resolve()
        for member in members:
            target = (folder / member.name).resolve()
            if not target.is_relative_to(base) or not (member.isfile() or member.isdir()):
                raise RuntimeError(f'Unsafe archive member: {member.name}')
        tar.extractall(path=folder, members=members)
    print('SciFact extracted:', folder)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--with-longmemeval', action='store_true')
    args = parser.parse_args()
    sources = [
        ('ines-besrour/unarxive_2024', 'unarxive', ['sample/preview.jsonl'], 'main'),
        ('allenai/qasper', 'qasper', ['qasper/train/0000.parquet'], 'refs/convert/parquet'),
        ('allenai/qasper', 'qasper', ['qasper/validation/0000.parquet'], 'refs/convert/parquet'),
    ]
    for repo, subfolder, choices, revision in sources:
        filename, sha = pick_hf_file(repo, choices, revision=revision)
        print('Downloading', repo, filename)
        hf_download(repo, filename, subfolder, revision=sha)
    download_scifact()
    if args.with_longmemeval:
        repo = 'xiaowu0162/longmemeval-cleaned'
        filename, sha = pick_hf_file(repo, ['longmemeval_oracle.json'])
        hf_download(repo, filename, 'longmemeval', revision=sha)
    print('DONE. No full unarXive archive downloaded.')

if __name__ == '__main__':
    main()
