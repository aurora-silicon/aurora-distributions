# Aurora distributions

Distribution catalogs and download metadata for the native Aurora installer. Fedora Aurora Remix, Aurora ALARM and Aurora NixOS each have KDE Plasma, GNOME and minimal profiles. Hardware revisions and package sources live in their individual repositories; this repository authenticates and publishes their delivery metadata.

Catalogs use an independent Ed25519 key. Only its raw public key is committed under `keys/`; signing uses a protected secret outside the source tree. Every enabled model is bound to an explicit installation profile, payload, metadata and engine digest. Large payloads use independently pinned parts and retain a whole-file digest.

Development builds and device qualification are separate. `site/installer/profile-builds.json` records assembly evidence, including incomplete profiles. Private device calibration and Neo policy inputs are never uploaded here.

The [developer installer](https://github.com/aurora-silicon/aurora-installer/releases/tag/native-dev-20261018) includes all nine profiles. [Release procedures](docs/RELEASING.md) cover signed delivery and publication; [validation evidence](docs/VALIDATION.md) records tested behavior and remaining hardware qualification.
