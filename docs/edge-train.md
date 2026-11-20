# Edge for every profile: the Edge train

Status 2026-11-20: implementation and plan. Nothing here is signed, adopted or published.

Dj's direction: every M1, M2 and M3 Mac and the MacBook Neo installs from Edge, on every distribution, and every distribution's Edge moves at the same time. Testing ("nightly") stays the channel for Macs that Edge does not admit.

An **Edge train** is one release: one Linux and Mesa `aurora-edge` pair, built by every distribution, staged, qualified, signed and adopted for all ten profiles at once. A partial set is refused, as for the 65-model Testing cohort.

## The cohort

`edge/cohort.json` holds the policy. It covers 35 Macs from `keys/supported-models-65.json`: 34 M1, M2 and M3 boards plus the Neo. Each family is one chip generation and die class (base, or Pro/Max/Ultra): the same SoC firmware and GPU configuration, so it is qualified once on any of its Macs.

| Family | Chip | Macs |
|---|---|---|
| `t8103` | M1 | j274 (Mac mini), j293 (MacBook Pro 13"), j313 (MacBook Air), j456, j457 (iMac 24") |
| `t600x` | M1 Pro, Max, Ultra | j314s, j314c (MacBook Pro 14"), j316s, j316c (16"), j375c, j375d (Mac Studio) |
| `t8112` | M2 | j413 (MacBook Air 13"), j415 (Air 15"), j473 (Mac mini), j493 (MacBook Pro 13") |
| `t602x` | M2 Pro, Max, Ultra | j414s, j414c (MacBook Pro 14"), j416s, j416c (16"), j474s (Mac mini), j475c, j475d (Mac Studio), j180d (Mac Pro) |
| `t8122` | M3 | j433, j434 (iMac 24"), j504 (MacBook Pro 14"), j613 (MacBook Air 13"), j615 (Air 15") |
| `t603x` | M3 Pro, Max | j514s, j514c, j514m (MacBook Pro 14"), j516s, j516c, j516m (16") |
| `t8140` | A18 Pro | j700 (MacBook Neo) |

- **Excluded:** `apple,j575d`, the Mac Studio with M3 Ultra (T6032). Its Stage 1 and iBoot have never been tried (Testing's `stage1_caution`), and no Aurora machine can produce its rows. It stays on Testing with that caution. Adding it is a one-line cohort change once a T6032 row exists.
- **Rider:** `riders.t6050` is the MacBook Pro 14" M5 Pro (`apple,j714s`) on `fedora-kde` only, with the M5 gates of aurora-distributions#63. A train carries it only when `edge/train.json` lists `"riders": ["t6050"]`.
- **Every profile carries every cohort Mac**, ordered as the producer orders them. There are nine core profiles plus `omarchy-hyprland`.

## How each distribution builds Edge and how Edge installs update

Before this change, only Fedora had an Edge build: the Neo branch workflow `edge/neo-f44-image-*`, for `fedora-kde`, built from a frozen local repository. It wrote `enabled=0` into `aurora.repo`, so **Edge installs received no Aurora updates**. ALARM and NixOS had only `edge-staging/*` package builds of the alpha.1 pair. They had no image and no feed.

Each distribution now has an Edge package feed that moves only with the adopted train (`site/installer/edge-train.json` on aurora-distributions main):

| | Fedora (fedora-aurora PR) | ALARM (alarm-aurora PR) | NixOS (nixos-aurora draft) | Omarchy (omarchy-aurora draft) |
|---|---|---|---|---|
| Edge build | `edge-staging/<pair>` rpms.yml run, then `edge-f44-sign-rpms.yml` (main) | `edge-staging/<pair>` packages.yml run | `channel-edge` commit (see below) | ALARM's build-image with the ALARM Edge overlay |
| Edge snapshot | `publish-edge.yml`: a Testing snapshot with the exact signed Edge kernel and Mesa RPMs (`edge-YYYYMMDD.N`, `edge-sources.json`) | `publish-edge.yml`: the `[aurora-edge]` overlay, kernel and Mesa only (`edge-YYYYMMDD.N`) | cache release of that commit | the ALARM overlay |
| Feed | `aurorasilicon.org/fedora-aurora/44/edge/aarch64` | `[aurora-edge]` `aurorasilicon.org/alarm-aurora/edge/aarch64`, ahead of `[aurora]` Testing | Git ref `channel-edge` | ALARM's `[aurora-edge]` |
| Images | `images.yml channel=edge edge-snapshot=…`: built from the snapshot's signed metadata, follows the Edge feed | `images.yml channel=edge edge-snapshot=…` | `images.yml channel=edge` | `images.yml alarm-edge-snapshot=…` |
| Deploy | `deploy-snapshot.yml channel=edge` | `deploy-snapshot.yml channel=edge` | `deploy-cache.yml` advance edge | ALARM's deploy |

**Why the models differ.**
- **Fedora 44** is a fixed release, so Edge is a complete frozen snapshot, like Stable. A later **refresh** snapshot may carry newer Testing userspace with the very same signed kernel and Mesa RPMs (`channel-site.py edge_gate`). That keeps Firefox security fixes inside the 7-day Edge deadline. A new pair is always a new train.
- **ALARM** is rolling: `[core]`, `[extra]` and `[alarm]` move every day. A frozen Aurora userspace on a moving Arch base is a partial upgrade. A third full copy (about 430 MB per snapshot) would also overflow the 900 MB Pages budget once Stable is promoted. So ALARM Edge is an overlay: `[aurora-edge]` holds only the qualified kernel and Mesa, ahead of `[aurora]` (Testing). Pacman takes a package from the first repository that has it.
- **NixOS** pins the whole system in `flake.lock`. Edge is the ref `channel-edge`, a commit whose kernel and Mesa pins are the train pair. Signing (testing-signing) and cache releases are main-only, so the Edge kernel and Mesa must become attributes on main. See the nixos-aurora draft for the remaining package half.

**The same time.** Every Edge deploy reads `edge-train.json` from aurora-distributions main. It refuses any snapshot that the adopted train does not name, or (Fedora refresh) that does not carry the named snapshot's exact kernel and Mesa RPMs. Right after adoption, dispatch the four deploys together (step 10 below). No distribution's Edge kernel or Mesa can move alone.

**Existing Neo Edge installs** (alpha.1 and alpha.2) have `enabled=0` and stay frozen until moved to the feed once:

```
sudo sed -i -e 's|^baseurl=.*|baseurl=https://aurorasilicon.org/fedora-aurora/$releasever/edge/aarch64|' -e 's|^enabled=.*|enabled=1|' /etc/yum.repos.d/aurora.repo
```

## The pipeline

`scripts/edge_train.py` generalizes `neo_edge.py` to N models × ten profiles. `neo_edge.py`, `prepare-neo-edge-payload.py` and `stage-neo-edge-payload.yml` are unchanged: they keep serving the Neo alpha.2 and #63's M5 alpha.3.

1. **Commit A: `edge/train.json`.** It records:
   - `release` (`alpha.<n>`) and `riders`;
   - `pair`: Linux and Mesa `aurora-edge` revisions and Git trees. The pair is a variable: use whatever pair the latest Neo qualification passed on;
   - `reference`: the build the hardware rows ran, `{kernelImageSHA256, mesaDsoSHA256, mesaSigningRunID}` from the Fedora signer;
   - `distributions`: each distribution's Edge snapshot (Omarchy also names `alarm_snapshot`);
   - `profiles`: each profile's `images.yml` run on its distribution's main.

   Push A to `edge/stage-train-<name>`. `stage-edge-train.yml` stages all ten profiles, artifact only: it uses no secrets and creates no release. For each profile it authenticates the image run and its `edge-image-proof.json` (schema 2: the snapshot, the Edge feed, the packaged pair, the kernel and Mesa digests). It then restricts the install policy and the catalog rows to the cohort and records `STAGING.sha256`.
2. **Commit B** adds `profiles.<p>.stage = {run, sha256}` and merges to main (PR). `train_digest` excludes the staging records, so A and B are the same train.
3. **Publish**, one call per profile: each distribution's `publish-edge-payload.yml` calls `build-payload.yml` with `edge-train-revision` (B on main) and `edge-train-evidence`. `bind-edge-train.py` checks that B is on main and checks the staging run, the digest and the exact file list. It then binds the evidence and runs `verify_edge_delivery`, `verify_binding`, `verify_metadata` and `verify_edge_source_refs`. The result is the immutable `payload-<profile>-<image run>-edge-alpha.<n>` release, signed by the catalog signer.
4. **Adopt once:** `adopt-edge-set.yml` runs `adopt-edge-set.py`.
   - It downloads and verifies all ten deliveries exactly as the single-profile Edge path does: signature, hosted provenance, report digest, packaged sources, aurora-edge ancestry and tree, no relabelled Testing payload.
   - It refuses anything but the train's ten tags.
   - It refuses a sequence that does not exceed every served Edge sequence. Installers keep one Edge floor for all profiles.
   - It refuses removing a Mac that Edge serves today (the Neo; the J714s once alpha.3 shipped it).
   - It assembles and verifies the new tree beside the site, then replaces all ten Edge directories, the Fedora KDE root fallback and `site/installer/edge-train.json`.
5. **Served-state checks.** `verify-channel-catalogs.py` (Pages and every PR) fails if any profile's Edge catalog differs from the adopted train record. `adopt-delivery.py --channel edge` refuses once a train is adopted, and Testing release preparation keeps the record and every Edge file.

Every rule in `docs/channels.md` still holds: a new immutable `-edge-alpha.<n>` release per profile, the detached report digest, packaged-source records, the revision/tree and aurora-edge ancestry, no Testing tag or payload as Edge, and native schema 5 only.

## Composing with Neo alpha.2 and the M5 rider

- **Neo alpha.2** (the respin on mesa-aurora 26.3.0-35: 418bf795 plus 15fe5010, kernel 7.3.0-43 e0ee46a1, a new signer run) and **alpha.3** (#63, `neo_edge.M5`) ship through the single-profile path, unchanged.
- **The first train takes the next free alpha number:** alpha.4 if alpha.3 ships, otherwise alpha.3.
  - Its pair should be the pair the Neo last passed, so the Neo's evidence carries over: the `t8140` record is the Neo lane's `{pair, checks}` plus `"machine": "apple,j700"`.
  - If alpha.3 put the J714s on Fedora KDE, the train must list `riders: ["t6050"]` with the M5 record, or adoption refuses.
- **After the first train**, every Edge update is a train.

## Qualification plan

**Evidence input** (`edge-train-evidence`):

```json
{"families": {"<family>": {"machine": "apple,<board>", "pair": <train.reference>,
                           "checks": {"<gate>": {"status": "passed", "sha256": "sha256:<manifest digest>"}}}},
 "distributions": {"fedora|arch|nixos|omarchy": {"machine": "apple,<board>",
                    "pair": {"kernelImageSHA256": "...", "mesaDsoSHA256": "..."},
                    "checks": {"boot": {...}, "gpuSmoke": {...}, "suspend": {...}, "restoration": {...}}}}}
```

**Per-device evidence (installer#176).** The installer defaults a Mac to Edge only with proof for that model:
- Every bound catalog carries `edgeQualification.devices`: `{"<deviceIdentifier>": {"evidenceDigest": "sha256:…"}}`.
- Each digest covers the exact bytes of the delivery asset `edge-device-<vendor>-<board>.json`, for example `edge-device-apple-j413.json`. That record holds `deviceIdentifier`, `family`, `machine`, `pair` and `checks` (its family row, the shared pair), plus the `linux` and `mesa` sources.
- `verify_binding` re-derives every digest from the family rows. `adopt-edge-set.py` downloads every record and refuses a missing or altered one, or one naming another device.
- `verify-channel-catalogs.py` re-checks the served copies. These sit in each profile's `edge-alpha` directory and, for Fedora KDE, in the root fallback.
- A family qualified only by a sibling (`qualifiedBy`) is listed without `devices` entries. Its Macs default to Nightly; an explicit Edge pick still works.
- `neoEvidence` (the `t8140` row as `{pair, checks}`) stays for the Alpha 22 installer.
- The single-profile Neo and M5 path (`neo_edge.py`, #63) does not emit `devices` yet; that lane should add it before the new installer ships.

**Gates:**
- M1, M2 and M3 families: `vulkanCTS`, `glCTS`, `suspend` and `restoration`.
- `t8140` (Neo): `neo_edge.GATES`.
- The `t6050` rider: #63's M5 gates.
- Each `sha256` is the digest of a sha256sum manifest kept by the machine owner, as for the Neo. The publisher sees only digests.

**Machines** (Aurora Agents inventory and lab records, 2026-11-20):

| Family | Machine | Status |
|---|---|---|
| `t600x` | Ryan's lab M1 Pro (G13S) | available through Ryan |
| `t8112` | Ryan's lab MacBook Air M2 (J413) | available through Ryan |
| `t8122` | Ryan's lab MacBook Air 15" M3 (J615); satchlj's `rubra-j613` (MacBook Air 13" M3) | rubra: one-shot trial boots only, no installs, no speakers, no CTS capability listed; offline since 11-13 |
| `t8140` | the Neo lane's Neo | Neo lane |
| `t8103`, `t602x`, `t603x` | **none** | Dj's M2 Pro (J414s) is light use only, no tests |

The three uncovered families need a machine, or Dj's decision to qualify them by a sibling family on the same GPU generation. That decision is recorded in the signed report: `"qualifiedBy": "t600x"` for `t8103`, `"t8112"` for `t602x`, `"t8122"` for `t603x`.

### Row spec (one row per family, plus one smoke row per distribution)

**Before the row.**
- Use the M1/M2 rig's own lock.
- Check the machine's baseline (the default kernel, failed systemd units) and keep a verified rollback copy.
- Speaker checks only at 5 % through speakerguardd. No speaker use on rubra.
- On Type-C VDM rigs, verify the partner identity first.

**The exact pair.** One-shot boot the reference kernel:
- `vmlinuz` and modules from the Fedora signer run's `kernel-aurora-core` and `kernel-aurora-modules` RPMs;
- check the vmlinuz sha256 against `reference.kernelImageSHA256`.

Use the reference Mesa:
- `mesa-aurora-vulkan-drivers` and `mesa-aurora-dri-drivers`, with signatures checked (`rpmkeys --checksig`), extracted to a tmpfs prefix;
- `VK_ICD_FILENAMES` and `LIBGL_DRIVERS_PATH` point at that prefix;
- check `libvulkan_asahi.so` against `reference.mesaDsoSHA256`.

**Gates and pass bars.**
- **`vulkanCTS`.** The Khronos CTS that matches Mesa's `conformance_version`, full `vk-default` must-pass list, with `deqp-vk --deqp-caselist-file=… --deqp-log-images=disable`. Run it resumably, in chunks of at most 75 minutes.
  - Pass bar: no `Fail`, `Crash`, `InternalError`, `ResourceError` or `Timeout`. `NotSupported`, `QualityWarning` and `CompatibilityWarning` are allowed.
  - **No waivers**: a real failure blocks the family.
  - Record the case count and totals.
- **`glCTS`.** `glcts` KHR-GL46 and GLES 3.2 (EGL surfaceless), with the same bar.
- **`suspend`.**
  - Three cycles of `rtcwake -m freeze -s 30` on M1 to M3 (`mem_sleep` is s2idle). Arm nothing else.
  - After each wake:
    - `vulkaninfo --summary` lists the Borealis device;
    - one compute dispatch and `dEQP-VK.api.smoke.*` pass;
    - the panel comes back;
    - Wi-Fi reassociates;
    - `journalctl -k -b` has no GPU fault, firmware crash or `asahi` timeout line.
  - Pass bar: 3 of 3.
- **`restoration`.** Reboot to the default, then remove the one-shot entry and the tmpfs prefix. The before/after baselines must match: the kernel, the failed-unit list, the bootloader entries, and the ESP Stage 2 sha256.

**Distribution smoke rows** (fedora, arch, nixos, omarchy; any cohort machine, Fedora may reuse a family row):
- Install, or one-shot boot, that distribution's Edge image from the staged payload. Its kernel and DSO digests are that distribution's `edge-image-proof.json` `files`.
- `boot`: the desktop or console comes up.
- `gpuSmoke`: `vulkaninfo` plus `dEQP-VK.api.smoke.*` plus `eglinfo`.
- `suspend`: one 30 s cycle.
- `restoration`: as above.

**Requests to post on the team channel.** One per family and one per distribution:
- the train's `pair`, `reference` and the staged payload digests;
- the gates and bars above;
- the request to return a `{machine, pair, checks}` record.

## Release notes (draft, alpha.N)

> **Aurora Edge Alpha N: every M1, M2 and M3 Mac and the MacBook Neo, on every distribution**
>
> Edge is now the default channel for every MacBook, iMac, Mac mini, Mac Studio and Mac Pro with an M1, M2 or M3 chip, and for the MacBook Neo. Fedora (KDE, GNOME, minimal), Arch Linux ARM (KDE, GNOME, minimal), NixOS (KDE, GNOME, minimal) and Omarchy all ship the same kernel and Mesa, built from the `aurora-edge` branches and tested on each chip family. Every chip family passed the full Vulkan and OpenGL conformance tests, suspend and restoration.
>
> - Kernel: aurora-edge <linux revision>. Mesa: aurora-edge <mesa revision> (Borealis Vulkan driver).
> - Edge installs now receive updates. Fedora follows the Edge repository. Arch Linux ARM takes the Edge kernel and Mesa from `[aurora-edge]` and everything else from Testing. NixOS follows `channel-edge`. All distributions move to a new Edge kernel and Mesa together.
> - Existing Fedora Neo Edge installs: run the one-line command in the release page to start receiving Edge updates.
>
> Known issues:
> - The Mac Studio with M3 Ultra stays on Testing; its first boot has not been tested.
> - <per-family known issues from the rows>

## Step by step

| # | Step | Who |
|---|---|---|
| 1 | Merge the pair into linux and mesa `aurora-edge` (the Neo lane's pair). | PR review |
| 2 | Fedora: `edge-staging/<pair>` rpms.yml, then the `edge-f44-sign-rpms.yml` pins PR and dispatch with `sign=true` (as for the Neo). | fedora-aurora, testing-signing |
| 3 | ALARM: push `edge-staging/<pair>` (tracker pins only) and dispatch packages.yml on it. | alarm-aurora |
| 4 | Edge snapshots: `publish-edge.yml` in fedora-aurora (`signing-run`, `base-release`) and alarm-aurora (`build-run`). NixOS: the `channel-edge` commit and cache release. | testing-signing (package keys) |
| 5 | Images: `images.yml channel=edge` for the nine core profiles; Omarchy with `alarm-edge-snapshot`. | distribution repos |
| 6 | Commit A, `edge/train.json`, pushed to `edge/stage-train-<name>`. Stage late: the catalog sequence is the staging time. | aurora-distributions |
| 7 | Hardware rows (the spec above) and the evidence JSON. | Ryan's lab, rubra, the Neo lane, the M5 lane; owners |
| 8 | Commit B (staging records) PR to main, plus each distribution's `publish-edge-payload.yml` pin to that main commit. | review |
| 9 | Publish ten payloads: `publish-edge-payload.yml` per profile, with `edge-train-revision` and the evidence. | **catalog-signing (Dj's catalog key)** |
| 10 | Adopt: `adopt-edge-set.yml`, then Pages. Then, together: `deploy-snapshot.yml channel=edge` in fedora-aurora and alarm-aurora, and the NixOS edge advance. | **catalog owner (Dj)** |
| 11 | Verify all ten `edge-alpha` URLs (models, `edgeTrain.trainDigest`, payload digests) and one install per distribution from the live channel. | |

**Steps that need Dj:**
- Step 9 signs with the installer catalog key in `catalog-signing`.
- Step 10 is the catalog owner's adoption and Pages publication.
- Steps 2 and 4 use the package keys in each repository's `testing-signing` environment.

**Decisions for Dj:**
- Sibling qualification for `t8103`, `t602x` and `t603x` (`qualifiedBy`), or hold those families until hardware exists.
- j575d (M3 Ultra) on Edge or not.
- The weekly Fedora Edge refresh cadence for Firefox security fixes.

**Installer finding (for the installer lane).** The accepted-catalog floor is per channel, not per profile (`AcceptedCatalogIdentityStore.fileName(for:)`). After preparing one profile on Edge (or Testing, whose ten catalogs share sequence 1795085678), preparing another profile fails. With an equal sequence it fails as `sequenceReuse`, with a lower one as `rollback`. Keying the floor by channel and profile fixes it for every channel.
