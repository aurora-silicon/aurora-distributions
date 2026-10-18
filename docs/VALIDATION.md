# Distribution validation

Image assembly, Linux VM checks and physical Mac qualification are separate records. The nine current image builds and their signed payloads are listed in `site/installer/profile-builds.json`. Physical installation remains pending for every listed profile; Stable and RC have no promoted images.

## Native installer integration

[Installer PR #5](https://github.com/aurora-silicon/aurora-installer/pull/5) integrated the native macOS installer, Recovery app and nine-profile delivery into `aurora-installer/main` at `44a6dc9fe83a9efe1c70b07cf6e94a49c2d851ae`. Its Git tree is identical to the qualified source `f0d217364539e3cebff975689bcde2cf8d6045e7`; the root Windows CLI remains preserved.

[Source CI 39158235607](https://github.com/aurora-silicon/aurora-installer/actions/runs/39158235607) passed portable checks, strict Swift formatting, Debug/Release tests, real macOS developer peer-binding fixtures, image-builder source checks and engine reproduction. [App/package CI 39158743064](https://github.com/aurora-silicon/aurora-distributions/actions/runs/39158743064) built the actual nine-profile 2.1.1 artifacts and verified their signatures, sealed catalogs, expanded package and exact app/helper peer requirements. The [2.1.1 developer release](https://github.com/aurora-silicon/aurora-installer/releases/tag/native-dev-20261018.2) and signed app-download manifest identify those artifacts.

These records establish source integration and developer packaging. Physical installation, native Recovery, radio behavior and hardware update/reboot qualification remain pending. Public catalogs continue to exclude Neo; private research candidates do not change that admission policy.

## Final NixOS minimal image

[Captured verification evidence](https://github.com/aurora-silicon/nixos-aurora/releases/tag/vm-verification-20261018.1) covers the final minimal image from [image CI 39139388776](https://github.com/aurora-silicon/nixos-aurora/actions/runs/39139388776), source `8af4b5448d7e04933d136f713514b6dc662889d6`. This was a developer-run native AArch64 KVM test using the shipped kernel and initrd.

The CI archive, Boot and root filesystem hashes were verified before embedding. Reading the embedded filesystem bytes back from the sparse fixture produced the same hashes. The fixture supplied an empty vendor-firmware archive and its own ESP partition UUID. RAM unit overrides adapted the console and diagnostic logging; the production hostname services were used unchanged.

The test completed factory encryption and owner creation, then checked a cold passphrase unlock, password login and sudo. It verified the hostname, root/home subvolumes, Boot/ESP mounts, DNS resolution, the owner password hash, retirement of both bootstrap-key copies and retention of only LUKS slot 1.

The installed `nixos-rebuild switch --flake /etc/nixos#aurora` command built and activated the owner's configuration. Owner files and the kernel hash remained unchanged; the factory-only hostname helper disappeared. The test then read the regenerated GRUB configuration from the powered-off fixture, extracted its matching new initrd and booted the exact rebuilt generation. The account, unlock, mount, hostname and DNS checks passed again. This tested rebuilding at the same pinned source revision; source-version upgrades remain pending.

The initial validation harness incorrectly required `systemd-resolved.service`. The evaluated Nix configuration has NetworkManager enabled with default DNS and resolved disabled. That failed harness run is retained in the evidence. The corrected tests check resolver configuration and actual DNS resolution. No production image changes were needed.

`verification.json` contains the source and filesystem identities, exact outcomes, rebuilt generation and hashes of the captured logs and scripts. Its raw Ed25519 signature uses the pinned Aurora catalog root. From a trusted checkout of this repository, verify the downloaded report and signature with:

```sh
python3 - <<'PY'
from pathlib import Path
public = Path('keys/catalog.ed25519.pub').read_bytes()
assert len(public) == 32
Path('verification-key.der').write_bytes(bytes.fromhex('302a300506032b6570032100') + public)
PY
openssl pkeyutl -verify -pubin -keyform DER -inkey verification-key.der \
  -rawin -in verification.json -sigfile verification.json.sig
```

The proof archive includes the captured logs and scripts, including the initial harness failure. Compare their sizes and SHA-256 values with the authenticated report. The large source image and test disks are retained in a private off-host archive with exact restore instructions.

## Remaining qualification

These checks cover Linux owner setup, encryption and configuration rebuilds in a QEMU virt fixture. Physical Mac installation, native Recovery, display/peripheral behavior, source-version upgrades and hardware reboot qualification remain pending. KDE and GNOME assembly checks do not establish their graphical behavior on a Mac. Neo remains available for the owner's Omarchy trial until that device is free for the requested Fedora KDE installation.
