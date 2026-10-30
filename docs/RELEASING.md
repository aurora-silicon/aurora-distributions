# Aurora distribution releases

Fedora Aurora Remix, Aurora ALARM and Aurora NixOS each have KDE, GNOME and minimal profiles. Image assembly, signed delivery and physical qualification are separate entries in `site/installer/profile-builds.json`. A successful build does not promote a profile to Stable or mark hardware tests complete.

## Images and packages

Each distribution repository owns its native AArch64 package builds and `images.yml` workflow. Fedora publishes signed RPM snapshots and an authenticated repository at `https://aurorasilicon.org/fedora-aurora/44/testing/aarch64/`. ALARM publishes signed packages and databases at `https://aurorasilicon.org/alarm-aurora/testing/aarch64/`. NixOS publishes signed NARs through `https://aurorasilicon.org/nixos-aurora/cache/`.

Dispatch the distribution's `publish-payload.yml` with its profile and successful image run ID. The reusable workflow verifies that the image came from a successful main-branch build, authenticates the native Recovery template, assembles the payload, signs its schema-5 profile catalog and uploads an immutable GitHub prerelease. No engine archive is built, pinned or uploaded. Large payloads retain a whole-file digest and use separately pinned download parts. Private device calibration and Neo policy inputs are excluded from public deliveries.

Dispatch `adopt-set.yml` here with the selected repository/tag pairs. It verifies signatures, public model coverage, profile bindings, image provenance and increasing catalog sequences before committing the complete set. It then explicitly dispatches the Pages workflow. Edge carries development images; Stable and RC remain authenticated empty catalogs until qualified releases are deliberately promoted. `native-only-catalogs.yml` re-signs those empty catalogs as schema 5 with each release.

## Installer app

The installer app is built in aurora-installer by `native-installer-dmg.yml`, after `native-only-catalogs.yml` here has signed the release's catalogs and they are committed. [Release procedure](updates.md#release-procedure) step 6 has the order. The app bundles the nine profile catalogs, runs only its built-in native engine and accepts only schema-5 catalogs.

The 2.1.x developer builds and their `native-developer-build.yml` workflow are retired. That workflow verified and bundled the pinned engine runtime, which no longer exists, and could not run against schema-5 catalogs. Their [2.1.1 release](https://github.com/aurora-silicon/aurora-installer/releases/tag/native-dev-20261018.2) stays published for reference only.

`site/installer/native-downloads.json` pins the 2.1.1 app ZIP and developer package URLs, sizes and SHA-256 digests, so the app download URL in older immutable payload catalogs keeps working. Sign its exact bytes with the catalog key using Ed25519 and place the raw 64-byte signature beside it as `native-downloads.json.sig`. The pinned public key is independent of Apple's app-signing system.

The Pages job runs the focused tests, authenticates every raw catalog, and runs `scripts/fetch-native-downloads.py`. That script authenticates the download manifest before fetching, verifies both release files, and places the ZIP at the compatibility path recorded in the manifest. This path preserves the old app download URL in immutable payload catalogs. The binary is added only to the Pages artifact and is ignored by Git. Future payload catalogs use the actual immutable GitHub release URL. A missing signature, altered asset or unsafe URL/path fails deployment.

After publication, verify the live signed manifest, the current catalogs and the compatibility ZIP against their expected digests. When changing the reusable payload workflow, update the distribution repositories' pinned workflow revision after this repository's coherent release commit has passed deployment. This does not require rebuilding existing images.

Public catalogs admit every model in `keys/supported-models.json`: the M1–M3 Macs, the 16-inch M4 Pro MacBook Pro (J616s) and the MacBook Neo. Boards listed under `stage1_unqualified` stay enabled but are left out of the common Stage 1 qualification, so the installer stops before any disk change on them. The Neo and the J616s need their own board Stage 2 in every payload. Public deliveries carry no Neo firmware, calibration or radio policy; the native installer derives them on the Mac being installed. A payload assembled with private Neo policy inputs stays local, and adoption refuses it.

## Qualification and storage

Retain source revisions, package manifests, signatures, images, test logs and rollback artifacts. Check free space and estimate download plus extraction plus copy peaks before local artifact operations; retain at least 8 GiB reported free on the development host. Completed superseded data may move off-host only after a lossless byte verification and with exact restore commands. The emergency reserve is not build space.

Before promoting hardware support, complete the installation, owner setup, encryption/unlock, updates and reboot checks on that device. An owner-deferred Recovery step remains pending in the evidence, even when build and VM checks have passed.

See [validation evidence](VALIDATION.md) for the final NixOS minimal image's encrypted boot, installed-configuration rebuild and rebuilt-generation boot checks. These developer-run VM results do not promote hardware support or establish a source-version upgrade result.
