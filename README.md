# Aurora distributions

Distribution catalogs and download metadata for the native Aurora installer. Aurora Fedora Remix, Aurora Alarm and Aurora NixOS each have KDE Plasma, GNOME and minimal profiles. Hardware revisions and package sources live in their individual repositories; this repository authenticates and publishes their delivery metadata.

Catalogs use an independent Ed25519 key. Only its raw public key is committed under `keys/`; signing uses a protected secret outside the source tree. Catalogs are schema 5: every enabled model is bound to an explicit installation profile, payload and metadata digest, and names no engine, because the installer runs only its built-in native engine. Large payloads use independently pinned parts and retain a whole-file digest.

Development builds and device qualification are separate. `site/installer/profile-builds.json` records assembly evidence, including incomplete profiles. Private device calibration and Neo policy inputs are never uploaded here.

The installer is published as prereleases on [aurora-installer releases](https://github.com/aurora-silicon/aurora-installer/releases); each bundles all nine profiles. [Release procedures](docs/RELEASING.md) cover signed delivery and publication; [validation evidence](docs/VALIDATION.md) records tested behavior and remaining hardware qualification.

[How updates reach installed Macs](docs/updates.md) describes the update channels, the automated pipeline from source branches to signed feeds, its threat model and the key procedures. `feeds-health.yml` checks every live feed and catalog every 30 minutes.

[Flicker-free boot](docs/flicker-free-boot.md) describes how the boot keeps one picture on screen from power-on to the desktop, stage by stage, the branding each distribution configures, and how to verify a machine.

[Using the whole disk](docs/whole-disk.md) explains how to remove macOS so Aurora gets its space: what is kept, what changes, the owner it needs and how to get macOS back.

[Getting macOS back](docs/restore-macos.md) explains `aurora-restore`: choose a macOS version and Apple's installer installs it from Recovery, next to Aurora or in its place.

## Installer channels

Testing (Nightly) retains development/prerelease deliveries. Edge carries only signed, qualified releases from both Linux and Mesa `aurora-edge` branches; a new install defaults to Edge when the profile's Edge catalog admits and proves the Mac (`edgeQualification.devices`), and to Nightly otherwise, while an explicit pick always wins. Existing Testing packages are never promoted or relabelled as Edge. [Channel identity and migration](docs/channels.md) describes the separate endpoints, rollback floors and qualification contract. This support change does not publish a first Edge payload.
