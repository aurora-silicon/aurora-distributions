# Aurora distributions

Distribution catalogs and download metadata for the native Aurora installer. Aurora Fedora Remix, Aurora Alarm and Aurora NixOS each have KDE Plasma, GNOME and minimal profiles. Hardware revisions and package sources live in their individual repositories; this repository authenticates and publishes their delivery metadata.

Catalogs use an independent Ed25519 key. Only its raw public key is committed under `keys/`; signing uses a protected secret outside the source tree. Catalogs are schema 5: every enabled model is bound to an explicit installation profile, payload and metadata digest, and names no engine, because the installer runs only its built-in native engine. Large payloads use independently pinned parts and retain a whole-file digest.

Development builds and device qualification are separate. `site/installer/profile-builds.json` records assembly evidence, including incomplete profiles. Private device calibration and Neo policy inputs are never uploaded here.

The installer is published as prereleases on [aurora-installer releases](https://github.com/aurora-silicon/aurora-installer/releases); each bundles all nine profiles. [Release procedures](docs/RELEASING.md) cover signed delivery and publication; [validation evidence](docs/VALIDATION.md) records tested behavior and remaining hardware qualification.

[How updates reach installed Macs](docs/updates.md) describes the update channels, the automated pipeline from source branches to signed feeds, its threat model and the key procedures. `feeds-health.yml` checks every live feed and catalog every 30 minutes.
