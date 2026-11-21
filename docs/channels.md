# Installer channel identity

The installer offers Stable, Release candidate, Testing (shown as Nightly) and Edge. Edge carries qualified releases built from Linux and Mesa `aurora-edge` sources; Testing carries development/prerelease deliveries. Which channel an install uses (installer#176):

- **A new install defaults to Edge** when the selected profile's Edge catalog admits the Mac and `edgeQualification.devices` proves that model (a per-device evidence digest, see [Edge trains](edge-train.md)). Otherwise it defaults to Nightly (Testing).
- **An existing install keeps its recorded channel**, or Testing when none is recorded.
- **An explicit pick always wins**, in either direction.

The release descriptors' `default_channel` stays Testing (`channel_policy.descriptor` refuses Edge there): the installer chooses Edge per Mac and profile from the signed Edge catalog, never from the descriptor. Source-branch promotion alone does not make a payload eligible.

| ID | Catalog endpoint | Initial state |
| --- | --- | --- |
| `testing` | `channels/testing/catalog.signed.json` | Existing authenticated development deliveries |
| `edge` | `channels/edge-alpha/catalog.signed.json` | Signed empty catalog, or unavailable before migration |
| `stable` | `channels/stable/catalog.signed.json` | Existing authenticated state |
| `rc` | `channels/rc/catalog.signed.json` | Existing authenticated state |

The installer displays the friendly name **Edge**. The URL namespace is deliberately different from the old `channels/edge` endpoint. Already released installers defaulted to that old endpoint and ignore additive catalog fields; placing a new Edge payload there would silently opt them in. The old URL remains a development/Testing compatibility alias and must never carry the new Edge track.

## Signed catalogs and qualification

New catalogs carry a signed top-level `channel` field. A signed empty Edge catalog needs no invented qualification. A nonempty Edge catalog must be native schema 5 and carry `channel: edge` plus `edgeQualification` with:

- `schemaVersion: 1`, `releaseKind: alpha`, `status: passed`, and a positive `imageRunID`.
- `payloadDigest`, binding every model's actual payload digest.
- `evidenceDigest`, the SHA-256 digest of the **exact detached bytes** of `edge-qualification.json`.
- `linux` and `mesa` source records containing `repository`, `branch`, `revision` and `tree`. Repositories are `aurora-silicon/linux` and `aurora-silicon/mesa`, branches are both `aurora-edge`, and revisions/trees are full lowercase Git object IDs. `tree` means the actual Git tree object, not an informal name for a tested commit.

The detached report contains those qualification fields **without** `evidenceDigest`, avoiding self-reference. The payload receipt must independently name `release_channel: edge`, `release_kind: alpha`, the matching `packaged_sources` records and the actual payload size/hash. These records describe the packaged sources, not merely the current branch heads. The existing hosted provenance verification must authenticate the report, receipt and catalog before adoption. The adopter also independently resolves each source revision's Git tree and checks its `aurora-edge` ancestry.

Edge uses a new immutable `payload-<profile>-<image-run>-edge-alpha.<number>` release. Its payload, metadata and parts must belong to that new release. Existing Testing tags, existing Testing payload digests, metadata-only model refreshes, missing/pending qualification, mismatched packaged sources and wrong branches/trees are rejected. Ordinary `publish-channels os-promote --to edge` and `app-publish --to edge` are forbidden. A typed catalog for another channel cannot be copied into a different channel by the ordinary publisher.

The explicit future `adopt-delivery.py --channel edge` path accepts only an already signed, qualified Edge delivery; it cannot construct qualification or promote a Testing set. No workflow is dispatched as part of this support change.

## Edge trains: every profile at once

Edge for every M1, M2 and M3 Mac and the MacBook Neo ships as one **Edge train**: one aurora-edge pair, all ten profiles, one atomic adoption (`adopt-edge-set.py`). `edge/cohort.json` names the Macs, their chip families and hardware gates; `edge/train.json` pins one release. Staging (`stage-edge-train.yml`), signing (`build-payload.yml` with `edge-train-revision`) and adoption refuse a partial set, another train's deliveries, a sequence at or below any served Edge sequence, and a train that would remove a Mac Edge serves. Once a train is adopted, `site/installer/edge-train.json` records it, every served Edge catalog must match it, and the single-profile `adopt-delivery.py --channel edge` path is closed. Each distribution's Edge package feed deploys only what the adopted train names. See [Edge trains](edge-train.md) for the cohort, the per-distribution update models, the qualification row spec and the release plan.

## Development migration and rollback

Legacy unchannelled catalogs are not general-purpose channel catalogs. Testing accepts authenticated legacy development lineage: signed image-build evidence and the immutable approved distribution/profile delivery. The adopter verifies hosted provenance and the original main-branch image workflow. The installer checks the signed profile, image-build evidence ID and canonical development payload URL. Arbitrary unchannelled signed catalogs do not gain Testing eligibility.

Four-channel schema-3 release descriptors default to Testing. Legacy three-channel descriptors are interpreted as development descriptors: their old default Edge becomes Testing, while new Edge remains unavailable. New bundles contain explicit four-channel descriptors. Old clients keep their own bundled legacy descriptor and continue to read the development compatibility URL; the new Edge namespace stays separate.

Testing preserves the highest accepted sequence from both its own state and the old native development-edge state. Equal-sequence/different-digest reuse still fails. Edge uses a separate `edge-alpha-v1` rollback-state namespace and never inherits the old development floor. Adding signed channel identity changes the catalog digest, so migration advances the sequence before signing. No floor is deleted or reset.

`prepare-native-only-profiles.py` keeps the existing payload URLs/digests, emits signed Testing catalogs and a separately signed empty Edge catalog until qualified Edge input exists. Once a qualified Edge catalog exists, a Testing release preparation preserves its exact catalog/signature/report, sequence and installer compatibility; it cannot rewrite that channel. Legacy development aliases continue to reference Testing. Pruning retains objects reachable through those old-client aliases.

The final 65-model **Testing** cohort remains an atomic ten-profile adoption: nine core metadata variants plus the normal VM-qualified Omarchy delivery. Edge adoption does not replace Testing profile records or advance that cohort. Production signing, adoption and DMG publication remain coordinated by the distribution/catalog owner.
