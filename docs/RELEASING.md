# Aurora distribution releases

Fedora Aurora Remix, Aurora ALARM and Aurora NixOS each have KDE, GNOME and minimal profiles. Image assembly, signed delivery and physical qualification are separate entries in `site/installer/profile-builds.json`. A successful build does not promote a profile to Stable or mark hardware tests complete.

## Images and packages

Each distribution repository owns its native AArch64 package builds and `images.yml` workflow. Fedora publishes signed RPM snapshots and an authenticated repository at `https://aurorasilicon.org/fedora-aurora/44/testing/aarch64/`. ALARM publishes signed packages and databases at `https://aurorasilicon.org/alarm-aurora/testing/aarch64/`. NixOS publishes signed NARs through `https://aurorasilicon.org/nixos-aurora/cache/`.

Dispatch the distribution's `publish-payload.yml` with its profile and successful image run ID. The reusable workflow verifies that the image came from a successful main-branch build, authenticates the native runtime, assembles the payload, signs its profile catalog and uploads an immutable GitHub prerelease. Large payloads retain a whole-file digest and use separately pinned download parts. Private device calibration and Neo policy inputs are excluded from public deliveries.

Dispatch `adopt-set.yml` here with the selected repository/tag pairs. It verifies signatures, public model coverage, profile bindings, image provenance and increasing catalog sequences before committing the complete set. It then explicitly dispatches the Pages workflow. Edge carries development images; Stable and RC remain authenticated empty catalogs until qualified releases are deliberately promoted.

## Native developer app

The `native-developer-build.yml` workflow runs manually on this repository's main branch. Its Linux job authenticates all nine current catalogs and prepares signed app-download metadata using `INSTALLER_CATALOG_SIGNING_KEY`. These files remain workflow artifacts; the job does not change the live feeds.

Its macOS ARM job checks out the pinned installer revision, verifies the signed engine/Recovery runtime manifest, builds through XcodeBuildMCP, and packages the app with all nine sealed catalogs. The result includes a ZIP, developer package, build manifest, profile manifest and prospective catalog bundle. Signing credentials are confined to the Linux preparation job and do not enter the app or macOS job.

```sh
gh workflow run native-developer-build.yml \
  --repo aurora-silicon/aurora-distributions \
  -f release-tag=native-dev-20261018
```

Verify the successful run and its `native-build.json` before publishing. Upload the ZIP, developer package, manifests, checksums and signed checksum file to a draft release in `aurora-silicon/aurora-installer`, using the same tag named by the workflow. The tag must point to the manifest's exact native source revision. Verify the uploaded asset digests, then publish it as a prerelease. Only afterward should its prospective catalog metadata be adopted into the live feed; re-check current profile catalogs so a concurrent image publication is never overwritten by an older model set.

For each prospective catalog, require that `app-download-amendment.json` names the exact SHA-256 of the current catalog. Verify the old and new signatures against `keys/catalog.ed25519.pub`, require an increasing sequence, and compare the complete parsed catalogs with only `installer.downloadURL`, `sequence` and `issuedAt` excluded. Only these three fields may differ. Copy the verified catalog, signature and Edge envelope; keep the root catalog and Edge envelope identical to Fedora KDE. Leave Stable, RC, payload releases and image qualification records unchanged.

`site/installer/native-downloads.json` pins the published app ZIP and developer package URLs, sizes and SHA-256 digests. Sign its exact bytes with the catalog key using Ed25519 and place the raw 64-byte signature beside it as `native-downloads.json.sig`. The pinned public key is independent of Apple's app-signing system.

The Pages job runs the focused tests, authenticates every raw catalog, and runs `scripts/fetch-native-downloads.py`. That script authenticates the download manifest before fetching, verifies both release files, and places the ZIP at the compatibility path recorded in the manifest. This path preserves the old app download URL in immutable payload catalogs. The binary is added only to the Pages artifact and is ignored by Git. Future payload catalogs use the actual immutable GitHub release URL. A missing signature, altered asset or unsafe URL/path fails deployment.

After publication, verify the live signed manifest, the current catalogs and the compatibility ZIP against their expected digests. When changing the reusable payload workflow, update the distribution repositories' pinned workflow revision after this repository's coherent release commit has passed deployment. This does not require rebuilding existing images.

Developer app builds are ad hoc signed and are not notarized. The developer package installs the app and binds its privileged helper to that exact app signature. General Developer ID distribution requires the owner's Apple signing credentials and notarization; no production signing identity is represented by these development artifacts. Public catalogs admit their listed M1–M3 models. Neo remains a separate local developer delivery with independently provisioned inputs and explicit device qualification.

## Qualification and storage

Retain source revisions, package manifests, signatures, images, test logs and rollback artifacts. Check free space and estimate download plus extraction plus copy peaks before local artifact operations; retain at least 8 GiB reported free on the development host. Completed superseded data may move off-host only after a lossless byte verification and with exact restore commands. The emergency reserve is not build space.

Before promoting hardware support, complete the installation, owner setup, encryption/unlock, updates and reboot checks on that device. An owner-deferred Recovery step remains pending in the evidence, even when build and VM checks have passed.
