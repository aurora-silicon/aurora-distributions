#!/usr/bin/env python3
"""Authenticate native app downloads and materialize legacy Pages links."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 256 * 1024 * 1024
URL = re.compile(r'https://github\.com/aurora-silicon/aurora-installer/releases/download/'
                 r'native-dev-[0-9]{8}(?:\.[1-9][0-9]*)?/'
                 r'Aurora-Installer-[0-9]+\.[0-9]+\.[0-9]+-Developer\.(zip|pkg)')


def read_manifest(site, public):
    path = site/'installer/native-downloads.json'
    signature = path.with_suffix('.json.sig')
    if not path.exists() and not signature.exists():
        return None
    if (path.is_symlink() or signature.is_symlink() or not path.is_file()
            or not signature.is_file() or path.stat().st_size > 65536
            or signature.stat().st_size != 64 or len(public) != 32):
        raise ValueError('unsafe or incomplete native download manifest')
    with tempfile.TemporaryDirectory(prefix='aurora-download-signature-') as temporary:
        key = Path(temporary)/'key.der'
        key.write_bytes(bytes.fromhex('302a300506032b6570032100') + public)
        subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER',
                        '-inkey', str(key), '-rawin', '-in', str(path), '-sigfile', str(signature)],
                       check=True, stdout=subprocess.DEVNULL)
    manifest = json.loads(path.read_bytes())
    if manifest['schema'] != 1 or set(manifest['assets']) != {'zip', 'pkg'}:
        raise ValueError('expected ZIP and package download records')
    destinations = set()
    for kind, asset in manifest['assets'].items():
        match = URL.fullmatch(asset['url'])
        if (not match or match[1] != kind or type(asset['bytes']) is not int
                or not 0 < asset['bytes'] <= LIMIT
                or not re.fullmatch('[a-f0-9]{64}', asset['sha256'])):
            raise ValueError('invalid native release URL, size or digest')
        for alias in asset.get('compatibility_paths', []):
            parts = PurePosixPath(alias).parts
            if (not alias.startswith('installer/') or alias != '/'.join(parts)
                    or any(part in {'.', '..'} or not re.fullmatch('[a-zA-Z0-9_.-]+', part)
                           for part in parts)
                    or not alias.endswith('.'+kind) or alias in destinations):
                raise ValueError('invalid or duplicate compatibility download path')
            destinations.add(alias)
            target = site/alias
            if any(parent.is_symlink() for parent in [target, *target.parents] if parent != site.parent):
                raise ValueError('symlink in compatibility download path')
    return manifest


def fetch(site, public, opener=urllib.request.urlopen):
    manifest = read_manifest(site, public)
    if manifest is None:
        print('No native download manifest; no compatibility files requested')
        return
    # Verify every release file before placing any compatibility aliases.
    with tempfile.TemporaryDirectory(prefix='aurora-native-downloads-') as temporary:
        files = {}
        for kind, asset in manifest['assets'].items():
            path = Path(temporary)/kind
            digest, size = hashlib.sha256(), 0
            request = urllib.request.Request(asset['url'], headers={'User-Agent': 'Aurora-release-verifier/1'})
            with opener(request, timeout=90) as response, path.open('wb') as stream:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > asset['bytes']:
                        raise ValueError('download exceeds its signed size')
                    digest.update(chunk)
                    stream.write(chunk)
            if size != asset['bytes'] or digest.hexdigest() != asset['sha256']:
                raise ValueError('native download size or checksum mismatch')
            files[kind] = path
        for kind, asset in manifest['assets'].items():
            for alias in asset.get('compatibility_paths', []):
                destination = site/alias
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                    pending = Path(stream.name)
                try:
                    shutil.copyfile(files[kind], pending)
                    pending.chmod(0o644)
                    pending.replace(destination)
                finally:
                    pending.unlink(missing_ok=True)
    print('Verified both native release assets and prepared compatibility downloads')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', type=Path, default=ROOT/'site')
    args = parser.parse_args()
    fetch(args.site.absolute(), (ROOT/'keys/catalog.ed25519.pub').read_bytes())
