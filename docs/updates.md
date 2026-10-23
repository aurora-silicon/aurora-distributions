# How Aurora updates reach installed Macs

This is the overview for all three distributions. Each distribution repository has its own runbook in
`docs/updates.md` ([ALARM](https://github.com/aurora-silicon/alarm-aurora/blob/main/docs/updates.md),
[Fedora](https://github.com/aurora-silicon/fedora-aurora/blob/main/docs/updates.md),
[NixOS](https://github.com/aurora-silicon/nixos-aurora/blob/main/docs/updates.md)).

## The pipeline

```
aura main ─┐                       ┌─ PR "Track upstream: …" (bot branch auto/track-sources)
linux      ├─ track-sources.yml ───┤    CI dispatched on the exact head; merged when green
mesa       ┘  (every 30 min)       └─ merge commit on main
                                          │
                          build workflow on main (or the PR's dispatched build, reused)
                                          │
              publish workflow: resolve newest build per package ─ collect (no secrets)
                                          │                         verify artifact digest,
                                          │                         attestation, version order
                                          ▼
                     sign (testing-signing environment, key on stdin, no network)
                                          │
                     immutable signed snapshot release + build provenance attestation
                                          │
              deploy (channel = testing) ──► Pages: aurorasilicon.org/<repo>/…  ──► installed systems
                                          │
              promote.yml (stable-promotion environment: required reviewer) ──► stable channel
                                          │
              optional: images.yml ─► payload-after-images.yml ─► signed payload release
                                          │
              adoption into the installer catalogs: adopt-set.yml here, by hand, as today
```

Every step is a GitHub Actions workflow, every artifact a GitHub Release asset, every feed a GitHub Pages
site. Nothing runs outside GitHub.

| Distribution | Testing feed | Stable feed | What a channel is |
|---|---|---|---|
| Arch Linux ARM | `aurorasilicon.org/alarm-aurora/testing/aarch64` | `…/stable/aarch64` | a copy of one signed snapshot release (`testing-YYYYMMDD.N`) |
| Fedora | `aurorasilicon.org/fedora-aurora/44/testing/aarch64` | `…/44/stable/aarch64` | the signed metadata of one snapshot; RPMs stay at that release's URLs |
| NixOS | Git ref `channel-testing` | Git ref `channel-stable` | a nixos-aurora commit whose outputs are in the one signed cache, `aurorasilicon.org/nixos-aurora/cache` |

Promotion copies already-signed bytes to the stable path (ALARM, Fedora) or moves the stable ref (NixOS).
Nothing is rebuilt or re-signed, so stable receives exactly what testing users ran. A snapshot can be
promoted only if testing has served it, and only after `STABLE_MIN_SOAK_HOURS`.

## What installed systems do today

| | ALARM | Fedora | NixOS |
|---|---|---|---|
| Feed | `[aurora]` in `/etc/pacman.conf`, `Server = …/testing/aarch64`, `SigLevel = Required DatabaseRequired` | `/etc/yum.repos.d/aurora.repo`, `baseurl=…/44/testing/aarch64`, `gpgcheck=1`, `repo_gpgcheck=1` | `extra-substituters` = the Aurora cache, `extra-trusted-public-keys` = `aurora-nixos-1:…` |
| Trust | Aurora key locally signed at build; `aurora-keyring` package | `/etc/pki/rpm-gpg/RPM-GPG-KEY-aurora` (no package owns it) | key compiled into the system from `keys/cache.pub` |
| Follows | testing | testing | nothing: `/etc/nixos/flake.nix` pins the exact image commit |
| Kernel or aura update | pacman hook runs `update-m1n1`: aura + every DTB of the newest kernel + U-Boot into the ESP Stage 2; mkinitcpio rebuilds the initramfs | `kernel-install` runs dracut and `update-m1n1` (RPM triggers on the aura and U-Boot paths) | a rebuild installs the new kernel and initrd, but **leaves Stage 2 (aura and DTBs) as installed** |
| Device firmware | read from the ESP at boot by the initramfs hook; never redistributed | same (dracut module) | same (initrd service) |

No channel is selectable today: every image hard-codes testing (or, for NixOS, no channel at all). The
installer's own channels (`stable`, `rc`, `edge` in `aurora-installer/macOS/Release/release.json`, default
`edge`) choose installer catalogs, not package feeds.

## Images and the installer

`images.yml` in each repository now takes a `channel` input. The default keeps today's URLs (testing; for
NixOS `pinned`), so nothing changes on installed systems until someone builds images with
`channel=stable`. Building stable images is the moment installed systems start following stable; it is a
deliberate decision. For NixOS, `channel=testing|stable` writes
`github:aurora-silicon/nixos-aurora/channel-<channel>` into `/etc/nixos/flake.nix`, so
`nix flake update --flake /etc/nixos` followed by a rebuild takes the newest signed update.

The installer keeps choosing payloads through its signed catalogs. Payload releases can be produced
automatically after an image build (`AUTO_PAYLOADS`), but adopting them into the catalogs stays
`adopt-set.yml` here, run by a person.

## Switches

Everything automatic is off until its repository variable is `true`. With all of them off the workflows
behave like today's manual ones, minus the run-ID bookkeeping.

| Variable | Effect |
|---|---|
| `TRACK_SOURCES` | Open or refresh the pin PR every 30 minutes |
| `AUTO_MERGE_SOURCES` | Merge that PR once every CI run on its exact head has passed and its policy allows it |
| `AUTO_PUBLISH_TESTING` | Publish a signed snapshot after each successful main build (and every 6 h) |
| `AUTO_DEPLOY_TESTING` | Make that snapshot the live testing feed |
| `AUTO_IMAGES` | Rebuild the three images after a testing deploy |
| `AUTO_PAYLOADS` | Publish a signed payload release after each image build |
| `STABLE_MIN_SOAK_HOURS` | Minimum time on testing before promotion (default 0) |
| `TRACKER_APP_CLIENT_ID` | Use this GitHub App (key in the `source-tracking` environment) instead of the workflow token |

Merge policy per source lives in each repository's `tracking.json`. A proposal is never merged
automatically when upstream history was rewritten, when the kernel's version changes
(`auto-same-version`), when the archive cannot be proven to equal the commit's Git tree, when a recipe
needs a hand edit (for example a changed Cargo git dependency), or when the kernel and Mesa disagree on the
GPU UAPI (`updatelib.uapi_report`: any constant, record size or field offset both define differently).

## Security

### Signing keys

| Key | Signs | Lives in | Used by |
|---|---|---|---|
| ALARM package key `A33623FB…` | packages, pacman databases | `alarm-aurora` environment `testing-signing` | `publish.yml` job `sign` |
| Fedora RPM key (`fedora-aurora/keys/fingerprint.txt`) | RPMs, `repomd.xml`, metadata archive | `fedora-aurora` environment `testing-signing` | `publish.yml` job `sign` |
| Nix cache key `aurora-nixos-1` | narinfo files | `nixos-aurora` environment `testing-signing` | `publish-cache.yml` job `sign` |
| Installer catalog key (`keys/catalog.ed25519.pub`) | catalogs, download manifests | `catalog-signing` environment in all four repositories | `build-payload.yml` job `sign`, `native-only-catalogs.yml`, `native-developer-build.yml` |

Rules the workflows follow:

- A signing job holds no write token, runs only on `main` (the environment's branch policy), receives a
  candidate whose SHA-256 the preceding unprivileged job recorded, and checks it before signing.
- The key reaches the signer on stdin of a container started with `--network none`; package-manager
  installs happen in an earlier step without the secret. Nothing echoes it; GitHub masks it in logs.
- Build jobs never see a key. The NixOS build job used to sign its own export with the cache key on every
  push or manual run, from any branch; it now exports unsigned, and only the publisher signs metadata it
  verified.
- Pull-request and fork workflows never reference a signing environment. The tracker's write-scoped jobs
  apply a patch the read-only planner produced, refuse a patch that touches anything but `sources.json` and
  the tracked recipes, and never execute fetched code.
- Every third-party action is pinned by commit SHA. Every workflow starts from `permissions: {}` and
  grants each job only what it uses.

**Until the user moves them, the keys are repository secrets**, which any workflow on any branch pushed by
a writer can read. The workflows already name the environments, so moving the secrets needs no code
change (`scripts/setup-update-environments.sh`).

### Provenance

- Build jobs attest what they produce (`actions/attest-build-provenance`): ALARM packages, Fedora RPMs,
  NixOS cache artifacts, filesystem images.
- Publication verifies those attestations (`gh attestation verify --signer-workflow … --source-digest
  <commit>`) before signing, for every build whose workflow attests.
- Snapshot, cache and payload releases carry a `SHA256SUMS` and an attestation covering every asset.
  Deployment and promotion verify it; snapshots published before this change are accepted with a note in
  the run summary.
- Source archives: every pin carries a SHA-256 (and, for NixOS, the same hash in SRI form). The tracker
  additionally recomputes the archive's Git tree hash and compares it with the commit's tree, so the hash
  pinned is the hash of exactly that commit. Fedora and NixOS builds verify every archive against
  `sources.json`; ALARM's git-sourced recipes are pinned by commit ID (see Gaps).

### Threat model

| Threat | Mitigation | Residual risk |
|---|---|---|
| A compromised or careless PR branch exfiltrates a signing key | Keys only in environments restricted to `main`; signing jobs consume verified artifacts only | Until secrets move to environments, any branch workflow can read them |
| The bot proposes a malicious pin | Pins move only forward along the tracked branch; Git tree proof; CI on the exact head; merge withheld on rewritten history, version change or UAPI conflict | A malicious commit merged upstream is faithfully tracked: upstream review is the control |
| A tampered CI artifact is published | Artifact digest from the API, attestation tied to workflow and commit, re-verification of package signatures before upload | A compromised GitHub-hosted runner during the build |
| Rollback or freeze attack (old signed snapshot served) | Deploys refuse a lower package version (pacman/RPM rules) or a pin that moves backwards, unless `allow-downgrade` is approved in `stable-promotion` | A client that never checks feed freshness keeps whatever it last saw |
| Promotion of something untested | Stable only accepts what testing served, after the soak time, with a required reviewer | Reviewer approves without looking |
| Pages hijack or domain loss | Clients verify signatures with keys installed in the image, never fetched from the feed; `feeds-health.yml` verifies live signatures with keys read from the repositories | **Every feed, cache and catalog depends on `aurora-silicon/aurora-silicon.github.io` staying public with Pages and the `aurorasilicon.org` custom domain.** On the Free plan, making it private turns Pages off and takes all of them offline (as on 2026-10-23, 01:46 UTC) |
| Key compromise | Rotation through the keyring package / repo package / trusted-key list (below); separate environments per repository | Installed systems trust the old key until they update |
| Supply chain of actions and images | Actions pinned by SHA; the signer container is pinned by digest | Build containers still use `fedora:44` by tag |

### Verification checklist

Before turning on automation, and after any change to these workflows:

1. Environments exist with branch policy `main`; `stable-promotion` has required reviewers; each signing
   secret exists only in its environment (`gh secret list -R <repo>` shows none of them).
2. `gh api repos/aurora-silicon/<repo>/actions/permissions/workflow` shows `default_workflow_permissions:
   read` and `can_approve_pull_request_reviews: false`.
3. A `dry-run` of each publish workflow succeeds: it resolves builds without a run list, verifies
   attestations, signs with a throwaway key, and (NixOS) has Nix verify every signature.
4. The newest snapshot release has `SHA256SUMS`, and `gh attestation verify <asset> --repo
   aurora-silicon/<repo>` passes for a package and for the database or metadata archive.
5. On an installed system: `pacman -Syu` reports `aurora.db` signature OK; `dnf makecache` succeeds with
   `repo_gpgcheck=1`; `nix store verify --store https://aurorasilicon.org/nixos-aurora/cache
   --trusted-public-keys "$(cat keys/cache.pub)" <a kernel path>` passes.
6. `feeds-health.yml` is green; its issue is closed.
7. `channels.json` on each repository's `channels` branch names the snapshot the site serves.

## Key rotation and revocation

The rule for every distribution: ship the new public key to installed systems, signed by the old key, and
wait for adoption before the new key signs anything; revoke the old one afterwards.

- **ALARM.** Add the new public key to `packages/aurora-keyring/aurora.key` and its fingerprint as a new
  line in `aurora-fingerprint`; bump `pkgrel`; publish. The package's install script now runs
  `pacman-key --populate aurora`, so upgrades import and trust the new key (it did nothing on upgrade
  before). Update `keys/aurora-alarm.asc` and `keys/fingerprint.txt`, move the new secret into
  `testing-signing`, publish. Later list the old fingerprint in `aurora-revoked` and drop it from
  `aurora-fingerprint`.
- **Fedora.** dnf trusts the keys listed in `gpgkey=`; today that file is written by the image and owned
  by no package, so a new key cannot reach installed systems. Needed first: an `aurora-repos` package that
  owns `/etc/yum.repos.d/aurora.repo` and the key files, byte-identical to what images write today, so the
  first update adopts them without an `.rpmnew`. Then rotation is: ship the second key and list both in
  `gpgkey=`; switch signing; drop the old key. `assemble-repository.py` rejects base snapshots signed by
  another key, so the first snapshot after a switch must be complete (no carried packages).
- **NixOS.** Systems trust `extra-trusted-public-keys` from the flake. Add the new key next to the old
  one (both trusted), have users rebuild, then sign with both keys: narinfo files carry several `Sig:`
  lines, and `sign-cache-metadata.py` keeps signatures it does not replace. Finally drop the old key.
- **Installer catalogs.** The trust root is compiled into the installer (`trust-root.ed25519.pub`); a
  new key needs a new installer release.

If a key leaks: delete the secret, publish a snapshot signed with the replacement as above, mark the old
fingerprint revoked (ALARM `aurora-revoked`, Fedora key removal, NixOS key removal), and rotate the
installer only if the catalog key leaked.

## Robustness

- Every archive download retries rate limits and server errors with backoff that honours `Retry-After`,
  and moves to the next source on a checksum mismatch. Builds read archives first from each repository's
  `source-cache` release, which the tracker fills with verified copies; codeload is the fallback.
- NixOS CI places pinned archives in the store before building, so fetchurl's three quick retries no
  longer decide whether a kernel build starts (`scripts/prefetch-sources.py`).
- The empty NixOS artifact: run 39747554132's first kernel attempt failed on a codeload 429 and, because
  the upload ran `if: always()`, left a 136-byte `nix-kernel-closure` beside the real 1.2 GB one from the
  re-run. Failed attempts now upload under `failed-…-attempt-N`, and every collector uses only the newest
  artifact of each name.
- Publishing derives its inputs: the newest qualifying build per package, carrying unchanged packages from
  the newest snapshot. When a package has no publishable build (for example after a builder change or an
  expired artifact), the publish run requests a main build of exactly those packages and finishes; the
  build's completion triggers publication again.

## Decisions for the maintainers

1. **Stable channel.** Recommended: yes, with images switching to stable once one snapshot has been
   promoted. Until then installed systems stay on testing and `AUTO_DEPLOY_TESTING` should stay off.
2. **Who approves promotion.** Recommended: the `stable-promotion` environment with at least two named
   reviewers, self-review allowed while the team is small.
3. **Automatic merging of kernel and Mesa pins.** Recommended: aura and Mesa `auto`, kernel
   `auto-same-version`, with `AUTO_MERGE_SOURCES` on only after a week of manual merges.
4. **Separate stable keys.** Optional. dnf and Nix can trust a stable-only key per channel; pacman cannot
   scope trust by repository. Recommended: one key per distribution for now.
5. **NixOS Stage 2 on updates.** Installed NixOS never refreshes aura or the DTBs. Decide whether
   `nixosModules.installed` should regenerate Stage 2 the way `update-m1n1` does.
6. **Who opens the tracker's PRs.** The workflow token cannot open pull requests while "Allow GitHub
   Actions to create and approve pull requests" is off, as it is in all four repositories. Either turn it
   on, or (recommended) install a GitHub App with Contents, Pull requests and Actions write and set
   `TRACKER_APP_CLIENT_ID` plus the `TRACKER_APP_PRIVATE_KEY` secret in the `source-tracking`
   environment; the tracker then uses it. App merges also start the normal push builds, and work with
   branch protection that requires reviews.

## Gaps not closed here

- Signing keys stay readable by every workflow until they move into environments.
- `main` has no branch protection in any repository.
- ALARM recipes for aura, the kernel, Mesa, U-Boot and others use `git+…#commit=` sources with
  `sha256sums=('SKIP')`, pinned by commit ID (SHA-1) rather than by the archive SHA-256 in
  `sources.json`. Moving them to verified archives touches the kernel and Mesa recipes and is left to the
  kernel and Mesa owners.
- Fedora images build from the live testing feed, not from an immutable snapshot.
- The Fedora repository key and `.repo` file are owned by no package (see rotation).
