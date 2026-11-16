# Aurora distribution releases

Aurora Fedora Remix, Aurora Alarm and Aurora NixOS each have KDE, GNOME and minimal profiles. Image assembly, signed delivery and physical qualification are separate entries in `site/installer/profile-builds.json`. A successful build does not promote a profile to Stable or mark hardware tests complete.

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

Public catalogs admit every model in `keys/supported-models.json`: every M1 to M6 Mac with a device tree in Aurora's kernel, and the MacBook Neo. Boards listed under `stage1_caution` stay enabled and qualified; the installer warns their owners, with the recovery steps, before installing. The Neo and every M4 and later Mac need their own board Stage 2 in every payload, so the image's kernel must carry each one's device tree. Public deliveries carry no Neo firmware, calibration or radio policy; the native installer derives them on the Mac being installed. A payload assembled with private Neo policy inputs stays local, and adoption refuses it.

## Qualification and storage

Retain source revisions, package manifests, signatures, images, test logs and rollback artifacts. Check free space and estimate download plus extraction plus copy peaks before local artifact operations; retain at least 8 GiB reported free on the development host. Completed superseded data may move off-host only after a lossless byte verification and with exact restore commands. The emergency reserve is not build space.

Before promoting hardware support, complete the installation, owner setup, encryption/unlock, updates and reboot checks on that device. An owner-deferred Recovery step remains pending in the evidence, even when build and VM checks have passed.

See [validation evidence](VALIDATION.md) for the final NixOS minimal image's encrypted boot, installed-configuration rebuild and rebuilt-generation boot checks. These developer-run VM results do not promote hardware support or establish a source-version upgrade result.

## Expanding the published 58-model deliveries to 65 Macs

The target `keys/supported-models-65.json` manifest adds J714c/J714s, J716c/J716s, J775c/J775d and J873s. The current public manifest stays at 58 until complete cohort adoption atomically replaces it alongside the nine verified catalogs. These are experimental admissions with an owner warning, not claims that this installer or every peripheral has been qualified. Their Restore admission is limited to the pinned 27.2 (26B123) customer identities; the installed firmware still has to satisfy the native inspection policy.

The existing nine Fedora, ALARM and NixOS payloads already carry each new Mac's own board Stage 2 and CTXR members. A `delivery-revision: models65` invocation of `build-payload.yml` therefore makes a metadata-only variant of the immutable original image delivery. The refresh authenticates the original catalog signature and hosted attestation, verifies every payload download part plus the complete ZIP, and reads the fourteen required boot members from that verified ZIP. Each member must contain only its matching T6050 board's DTBs. It neither extracts the OS filesystem images nor rewrites any ZIP member. Installer metadata is external to the ZIP; a ZIP that unexpectedly embeds it is refused. The new catalog retains the original payload URLs, part records, size and digest, while pinning the new metadata artifact at a new immutable `-models65` release URL.

Use the nine original image run IDs already recorded in `site/installer/profile-builds.json`. The distribution repositories must first pin a shared workflow revision containing this path, and that workflow must pin the installer producer containing the matching 65-model manifest. `adopt-set.yml` with `model-cohort: models65` requires exactly one variant for each of the nine existing profiles. Adoption re-authenticates the original delivery, checks unchanged payload pointers and identity, and permits only the seven `['27.2']` Stage 1 admission additions in the metadata. The historical `models58` migration remains separate.

This source change does not publish new catalogs or replace the installer DMG. After the immutable variants and catalog adoption pass, prepare and sign the release catalogs, merge their exact bytes, publish Pages, and build a sealed installer DMG pinned to that catalog merge. The current ten-profile native-only release preparation also requires a complete Omarchy Hyprland delivery and passing VM evidence; a pending tenth profile is not bypassed by the 65-model path. Until that delivery exists, the full release workflow remains blocked even if all nine metadata variants are ready.
