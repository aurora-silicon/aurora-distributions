#!/usr/bin/env python3
"""Check that every live Aurora update feed and installer catalog is served and
authentic.

Each distribution's public key is read from its repository through the GitHub
API, not from the Pages site being checked, so a hijacked or stale site fails
signature verification instead of passing on HTTP 200. Exits 1 when any check
fails and writes a Markdown report.
"""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SITE = 'https://aurorasilicon.org'
ORG_SITE = 'aurora-silicon/aurora-silicon.github.io'
DOMAIN = 'aurorasilicon.org'
SPKI_ED25519 = bytes.fromhex('302a300506032b6570032100')


class Check:
    def __init__(self):
        self.rows = []

    def record(self, name, ok, detail=''):
        self.rows.append((name, ok, detail))

    def run(self, name, function):
        try:
            detail = function() or ''
            self.record(name, True, detail)
        except Exception as error:  # every failure becomes a report row
            self.record(name, False, str(error).strip().splitlines()[-1][:300] if str(error).strip() else repr(error))

    @property
    def healthy(self):
        return all(ok for _, ok, _ in self.rows)

    def markdown(self):
        lines = ['| Check | Result | Detail |', '|---|---|---|']
        lines += [f'| {name} | {"ok" if ok else "**FAILED**"} | {detail} |' for name, ok, detail in self.rows]
        return '\n'.join(lines) + '\n'


def fetch(url, limit=64 * 1024 * 1024):
    request = urllib.request.Request(url, headers={'User-Agent': 'aurora-feed-health'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'{url} exceeds {limit} bytes')
    return data


def gh_json(route):
    return json.loads(subprocess.check_output(['gh', 'api', route], text=True, stderr=subprocess.DEVNULL))


def repo_file(repository, path):
    data = gh_json(f'repos/{repository}/contents/{path}?ref=main')
    return base64.b64decode(data['content'])


class Keyring:
    def __init__(self, directory, key, fingerprint):
        self.home = Path(directory)
        self.home.mkdir(mode=0o700)
        subprocess.run(['gpg', '--homedir', str(self.home), '--batch', '--import'], input=key,
                       check=True, capture_output=True)
        listing = subprocess.check_output(['gpg', '--homedir', str(self.home), '--batch', '--with-colons',
                                           '--list-keys'], text=True)
        if fingerprint not in [line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:')]:
            raise ValueError('published key does not match its recorded fingerprint')

    def verify(self, data, signature, work):
        payload, sig = Path(work) / 'payload', Path(work) / 'payload.sig'
        payload.write_bytes(data)
        sig.write_bytes(signature)
        subprocess.run(['gpg', '--homedir', str(self.home), '--batch', '--verify', str(sig), str(payload)],
                       check=True, capture_output=True)


def ed25519_verify(public, data, signature, work):
    key, payload, sig = Path(work) / 'key.der', Path(work) / 'data', Path(work) / 'sig'
    key.write_bytes(SPKI_ED25519 + public)
    payload.write_bytes(data)
    sig.write_bytes(signature)
    subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER', '-inkey', str(key), '-rawin',
                    '-in', str(payload), '-sigfile', str(sig)], check=True, capture_output=True)


def channels(base):
    """Channels a feed serves, from its channels.json; testing when absent."""
    try:
        data = json.loads(fetch(f'{base}/channels.json'))
        return sorted(data.get('channels', {})) or ['testing']
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return ['testing']
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--site', default=SITE)
    args = parser.parse_args()
    check = Check()
    with tempfile.TemporaryDirectory(prefix='aurora-health-') as temporary:
        work = Path(temporary)

        def org_site():
            # Every feed lives under the custom domain this repository's
            # Pages site holds; making it private (Free plan) or disabling
            # Pages takes the domain, and with it every feed, offline.
            repo = gh_json(f'repos/{ORG_SITE}')
            if repo['private'] or repo['visibility'] != 'public':
                raise ValueError(f'{ORG_SITE} is not public: Pages and the custom domain stop serving')
            if not repo.get('has_pages'):
                raise ValueError(f'{ORG_SITE} has Pages disabled: {DOMAIN} serves nothing')
            try:
                pages = gh_json(f'repos/{ORG_SITE}/pages')
            except subprocess.CalledProcessError:
                return 'public, Pages enabled (custom domain settings not readable with this token)'
            if pages.get('cname') != DOMAIN:
                raise ValueError(f'{ORG_SITE} Pages custom domain is {pages.get("cname")!r}, not {DOMAIN}')
            return f'public, Pages enabled, cname {DOMAIN}, HTTPS ' + ('enforced' if pages.get('https_enforced') else 'NOT enforced')

        check.run('Organization site and custom domain', org_site)
        check.run('Site root', lambda: f'{len(fetch(args.site + "/"))} bytes')

        def keyring(name, repository, key):
            try:
                return Keyring(work / name, repo_file(repository, key),
                               repo_file(repository, 'keys/fingerprint.txt').decode().strip())
            except Exception as error:
                check.record(f'{repository} signing key', False, str(error)[:300])
                return None

        def channel_list(base, name):
            try:
                return channels(base)
            except Exception as error:
                check.record(f'{name} channel list', False, str(error)[:300])
                return ['testing']

        alarm = keyring('alarm', 'aurora-silicon/alarm-aurora', 'keys/aurora-alarm.asc')
        alarm_base = f'{args.site}/alarm-aurora'
        for channel in channel_list(alarm_base, 'ALARM') if alarm else []:
            def alarm_feed(channel=channel):
                url = f'{alarm_base}/{channel}/aarch64/aurora.db'
                data = fetch(url)
                alarm.verify(data, fetch(url + '.sig'), work)
                manifest = json.loads(fetch(f'{alarm_base}/{channel}/aarch64/manifest.json'))
                return f'signed database, {len(manifest["packages"])} packages'
            check.run(f'ALARM {channel} feed', alarm_feed)

        fedora = keyring('fedora', 'aurora-silicon/fedora-aurora', 'keys/RPM-GPG-KEY-aurora')
        fedora_base = f'{args.site}/fedora-aurora'
        for channel in channel_list(fedora_base, 'Fedora') if fedora else []:
            def fedora_feed(channel=channel):
                base = f'{fedora_base}/44/{channel}/aarch64'
                repomd = fetch(f'{base}/repodata/repomd.xml')
                fedora.verify(repomd, fetch(f'{base}/repodata/repomd.xml.asc'), work)
                import hashlib
                import xml.etree.ElementTree as ET
                ns = '{http://linux.duke.edu/metadata/repo}'
                primary = next(d for d in ET.fromstring(repomd).findall(f'{ns}data') if d.get('type') == 'primary')
                data = fetch(f'{base}/{primary.find(f"{ns}location").get("href")}')
                if hashlib.sha256(data).hexdigest() != primary.find(f'{ns}checksum').text:
                    raise ValueError('primary metadata does not match the signed repomd.xml')
                return 'signed repomd.xml, primary metadata matches'
            check.run(f'Fedora {channel} repository', fedora_feed)

        def nix_cache():
            info = fetch(f'{args.site}/nixos-aurora/cache/nix-cache-info').decode()
            if 'StoreDir: /nix/store' not in info:
                raise ValueError('nix-cache-info is not a Nix binary cache descriptor')
            served = fetch(f'{args.site}/nixos-aurora/cache.pub').decode().strip()
            expected = repo_file('aurora-silicon/nixos-aurora', 'keys/cache.pub').decode().strip()
            if served != expected:
                raise ValueError('the served cache key differs from keys/cache.pub')
            return 'cache descriptor and public key match'
        check.run('NixOS binary cache', nix_cache)

        catalog_key = (ROOT / 'keys/catalog.ed25519.pub').read_bytes()
        installer = f'{args.site}/aurora-distributions/installer'

        def signed(path):
            def run():
                ed25519_verify(catalog_key, fetch(f'{installer}/{path}'), fetch(f'{installer}/{path}.sig'), work)
                return 'Ed25519 signature valid'
            return run
        check.run('Installer catalog', signed('catalog.json'))
        check.run('Native downloads manifest', signed('native-downloads.json'))
        for channel in ('edge', 'rc', 'stable'):
            check.run(f'Installer {channel} channel',
                      lambda channel=channel: f'{len(json.loads(fetch(f"{installer}/channels/{channel}/catalog.signed.json")))} fields')
    report = ('## Aurora feed health: ' + ('healthy' if check.healthy else 'FAILING') + '\n\n' + check.markdown())
    args.report.write_text(report)
    print(report)
    sys.exit(0 if check.healthy else 1)


if __name__ == '__main__':
    main()
