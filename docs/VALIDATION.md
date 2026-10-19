# Distribution validation

Image assembly, Linux VM checks and physical Mac qualification are separate records. The nine current image builds and their signed payloads are listed in `site/installer/profile-builds.json`. A private Fedora KDE Neo installation has reached the hardware desktop; full installation/update/reboot qualification remains incomplete. Stable and RC have no promoted images.

## Neo Fedora KDE developer installation, 2026-10-19

The private developer installer built from `6dca86b6faf5a5387b09d6df1aad887266837bc6` completed native Recovery handoff and installed the refreshed Fedora KDE payload. [Source qualification 39198257551](https://github.com/aurora-silicon/aurora-installer/actions/runs/39198257551) passed. This revision preserves the signed app when preparing Recovery and discovers a completed Recovery target before rejecting a new allocation. The developer build source pin now selects that qualified revision; the public installer download is unchanged until its next artifact publication.

On Neo, the installed kernel `7.2.10-2.fc44.aurora.aarch64` reached a native KDE Wayland session with KWin reporting the Apple A18 Pro renderer. LUKS2 in-place encryption completed in 75 seconds; the resulting AES-XTS volume retained only the owner's Argon2id keyslot after setup. This is an observed installed-system state, not a subsequent owner-passphrase cold-boot test.

Hardware testing exposed an account-console/Plymouth acquisition issue and a text-getty handoff after account creation. [Shared platform fix a659e1a](https://github.com/aurora-silicon/aurora-platform/commit/a659e1aa48d8d755da7835a986ddd579702f5797) passed real systemd console acquisition and encrypted conversion fixtures in [CI 39200154080](https://github.com/aurora-silicon/aurora-platform/actions/runs/39200154080). All three distributions now pin that source as firstboot 0.1.3; their refreshed images still require factory installation qualification.

Wi-Fi connected and completed an HTTP request after changing the saved transition-network profile from SAE to WPA2-PSK; WPA3 remains unsupported by the current MT7932 driver. The owner confirmed Bluetooth works. Speaker testing identified two missing build dependencies: the MAX98360A codec driver and Fedora's PipeWire LV2 module. Loading the matching codec module bound the speaker card, started its protection daemon and exposed the protected DSP output. Audible speaker output and reboot persistence are not yet qualified. The Fedora kernel also lacks SELinux support despite the userspace enforcing configuration; that remains an open release blocker.

The Neo candidate uses privately supplied radio policies. Public catalogs continue to exclude Neo, and these observations do not qualify firmware-policy generation, GNOME/minimal hardware, ALARM/NixOS hardware or an end-to-end Swift installation backend. The separate native Swift branch has qualified authenticated partial firmware extraction, but the installed backend still uses its pinned Python engine.

## Native interface and authenticated firmware library

[Full installer qualification 39202750062](https://github.com/aurora-silicon/aurora-installer/actions/runs/39202750062) passed at `c0cae4a226fd1fffd58374cd2014a327678d51f9`: portable checks, native macOS Debug/Release tests, app/helper peer binding, engine reproduction and image-builder source checks. [Native visual review 39202327317](https://github.com/aurora-silicon/aurora-installer/actions/runs/39202327317) passed and rendered chooser, allocation, preparation, downloading and installation components in light and dark mode. The allocation controls fit at the default window size. Progress reports verified engine stages rather than a percentage inferred from elapsed time. This revision is merged and selected for the next developer artifact build; published downloads remain unchanged until that build is released.

The Swift firmware library uses authenticated partial ranges and native crypto/decompression. Its [live qualification 39199310535](https://github.com/aurora-silicon/aurora-installer/actions/runs/39199310535) fetched 15,996,940 bytes from the pinned J700 IPSW and independently verified 13 outputs totaling 32,014,794 bytes. It does not derive or qualify the missing Neo radio power policies. Firmware download checkpoints are being tested separately. The production installation backend still uses the pinned Python engine; these component results do not satisfy the requested complete Swift migration or physical installation/resume qualification.

## Native installer integration

[Installer PR #5](https://github.com/aurora-silicon/aurora-installer/pull/5) integrated the native macOS installer, Recovery app and nine-profile delivery into `aurora-installer/main` at `44a6dc9fe83a9efe1c70b07cf6e94a49c2d851ae`. Its Git tree is identical to the qualified source `f0d217364539e3cebff975689bcde2cf8d6045e7`; the root Windows CLI remains preserved.

[Source CI 39158235607](https://github.com/aurora-silicon/aurora-installer/actions/runs/39158235607) passed portable checks, strict Swift formatting, Debug/Release tests, real macOS developer peer-binding fixtures, image-builder source checks and engine reproduction. [App/package CI 39158743064](https://github.com/aurora-silicon/aurora-distributions/actions/runs/39158743064) built the actual nine-profile 2.1.1 artifacts and verified their signatures, sealed catalogs, expanded package and exact app/helper peer requirements. The [2.1.1 developer release](https://github.com/aurora-silicon/aurora-installer/releases/tag/native-dev-20261018.2) and signed app-download manifest identify those artifacts.

These records establish source integration and developer packaging. Physical installation, native Recovery, radio behavior and hardware update/reboot qualification remain pending. Public catalogs continue to exclude Neo; private research candidates do not change that admission policy.

## Fedora peripheral activation and signed updates

The current Fedora images use source `a91300a21a36dc749e356b007d26afee24b89767`: [minimal CI 39175156782](https://github.com/aurora-silicon/fedora-aurora/actions/runs/39175156782), [KDE CI 39175158654](https://github.com/aurora-silicon/fedora-aurora/actions/runs/39175158654) and [GNOME CI 39175160773](https://github.com/aurora-silicon/fedora-aurora/actions/runs/39175160773). All three image builds and their signed payload publications passed. Their Edge catalogs bind the verified image provenance, engine and payload asset digests. Public model coverage remains 34 models, excluding Neo and J614s.

The images use the [testing-20261018.7 signed RPM snapshot](https://github.com/aurora-silicon/fedora-aurora/releases/tag/testing-20261018.7). `aurora-remix-scripts-44-3` orders Fedora's KDE setup after Aurora owner creation and skips that setup once the owner is complete. Its migration helper removes only the exact factory-generated setup autologin configuration, preserving administrator changes. The signed package passed an installed-system upgrade and cold-boot owner handoff check; fresh-image encryption and cold-boot checks are in progress.

These images include `aurora-firmware-44-3`, whose RPM systemd scriptlets apply the peripheral service preset on fresh installation. Image assembly now rejects a disabled, masked or static peripheral service. Installed-system tests checked fresh-install activation and preservation of enabled, disabled and masked administrator choices across signed 44-2/44-3 transactions. Existing 44-2 developer installations need `sudo systemctl preset --now aurora-peripherals.service` to apply the new default explicitly.

An encrypted KDE AArch64 KVM installation from the preceding image was updated with the signed 44-3 RPM and its initrd regenerated. A cold boot using that exact regenerated initrd passed owner passphrase unlock, login and sudo, persistent hostname and mount checks, retirement of both bootstrap keys, retention of only LUKS slot 1, active NetworkManager and peripheral services, DNS resolution, and a package reinstall from the live HTTPS signed repository. Unsigned RPM installation was rejected. The live reinstall exercised the same package version; the earlier lifecycle tests exercised version changes. The [testing-20261018.6 RPM snapshot](https://github.com/aurora-silicon/fedora-aurora/releases/tag/testing-20261018.6) and live repository metadata were checked against the published asset digests and pinned signing key.

The [signed Fedora verification bundle](https://github.com/aurora-silicon/fedora-aurora/releases/tag/vm-verification-20261018.1) contains these installed-system checks, their scripts, captured logs and initial harness failure. Its `verification.json` and raw Ed25519 signature use the same pinned catalog root and verification command shown below for NixOS. The report authenticates every file inside the proof archive; it explicitly excludes fresh-image and hardware qualification.

The [current KDE verification bundle](https://github.com/aurora-silicon/fedora-aurora/releases/tag/vm-verification-20261018.2) covers the exact a913 image. Factory root encryption completed before the original test harness interrupted the first userspace boot, prior to account creation. That disk and its failure log were retained. A new overlay resumed Linux owner creation and passed a cold owner-passphrase boot, login/sudo, persistent hostname and mounts, retirement of both bootstrap keys, retention of only LUKS slot 1, active NetworkManager/peripheral services and DNS resolution. Both boots also exercised the production KDE handoff helper and verified that the real `plasma-setup.service` condition skipped setup without changing the Linux password or LUKS metadata.

This is a factory/resume/cold-boot result; it does not establish an uninterrupted factory boot. The archive retains the harness interruption, the corrected off-host storage guard and the executed scripts. Its signed report is `fedora-current-verification2.json`, with a raw `.json.sig` signature using the pinned catalog key. The included prior-image GUI diagnostics use VM-only QPainter/Qt software rendering. These checks boot the shipped kernel and initrd directly; default OpenGL, the Fedora EFI chain, native Recovery, device firmware and physical Mac behavior remain unqualified.

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

These checks cover Linux owner setup, encryption and configuration rebuilds in a QEMU virt fixture. Physical Mac installation, native Recovery, display/peripheral behavior, kernel upgrades and hardware reboot qualification remain pending. NixOS rebuilding at a new source revision remains pending. KDE and GNOME assembly checks do not establish their graphical behavior on a Mac. Neo remains available for the owner's Omarchy trial until that device is free for the requested Fedora KDE installation.
