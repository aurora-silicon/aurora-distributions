# M5 Pro (J714s) deep sleep on Edge: release plan

Status 2026-11-20: preparation only. Nothing here is published. The owner's decisions (2026-11-20) are:
- the channel is Edge;
- the T6050 runs with MTE on (no `arm64.nomte`);
- SEP writes are always on. After linux#2591 the driver has no `xart_writes`/`provision_keybag` parameters, and the distributions ship no apple_sep write options. The Edge kernel 7.3.0-43 predates linux#2591: it still has both parameters, and they default to on, so writes are on there too. Re-confirmed 2026-11-20 with no further write row. The evidence is the 11-11 J714s lab boot ([linux#1587](https://github.com/aurora-silicon/linux/pull/1587), merged 554c6d14): with the platform's xART guard (level 1), an enrolment survived a reboot, the right finger matched, a wrong finger was rejected, and there were no xART refusals.

Nothing ships for the M5 until its release-gate boot passes.

## What reaches a J714s, and from where

| Piece | Source | Where it is prepared |
|---|---|---|
| Kernel | linux `aurora-edge` after linux#2527 (integration e0ee46a1, M5 deep-sleep merges included), kernel-aurora 7.3.0-43, signer run 44694386531 | Neo Edge refresh lane (alpha.2). Reused unchanged; no second kernel pin. |
| Mesa | mesa `aurora-edge` after mesa#566 plus 15fe5010 (418bf795 + 15fe5010), mesa-aurora 26.3.0-35, signer run 44832807927 | Neo Edge refresh lane (the -35 respin). Reused unchanged. |
| Device tree | in-tree `t6050-j714s.dtb` from kernel-aurora-core 7.3.0-43 | in the kernel RPM |
| aura (Stage 2) | aura 2f487f26 = main 82ee5d6f (aura#214: T6050 sleep controller wake sources; 9f501f32: GPU leakage words from the fuses) + aura#384 + the aura#392 MCC fix; label `1.0.0b13-g2f487f26` | on main: fedora `aura-1.0.0~b13.1-3`, alarm `1.0.0b13.1-4`, nixos `1.0.0b13.1` |
| MTE on T6050 | `arm64.nomte` only on T8142; a one-shot removal on T6050 | fedora `aurora-platform` 44-45, alarm `aurora-boot` 20261018-20, nixos `modules/aurora.nix` drafts |
| SEP writes on | always on, nothing to configure: 7.3.0-43's parameters default to on, and linux#2591 removes them | (none) |
| Rootfs deep sleep | `aurora-m5-platform-config`: deep sleep only, lid suspends on battery/AC/docked, `50-aurora-m5-rtc-safety` hook | fedora/alarm/nixos package drafts |
| Catalog | the J714s rides the Fedora KDE Edge payload beside the Neo (`neo_edge.M5`) | this repository's draft |

### Drafts (2026-11-20)

| Item | fedora-aurora | alarm-aurora | nixos-aurora |
|---|---|---|---|
| aura main 82ee5d6f | #448 | #376 | #348 |
| MTE on T6050 | #449 | #377 | #349 |
| aurora-m5-platform-config | #450 (contains #449's commit) | #378 (contains #377's commit) | #350 |
| Catalog switch + this plan | aurora-distributions #63 | | |
| Platform profiles without ACPI (linux#2549; see "Gaps found by the release gate") | #451: kernel 7.3.0-44 pin, **not to merge alone** | #380: resolved config line | #351: config copy and provenance, after #380 |

## Gaps found by the release gate (2026-11-20)

### No platform profiles on 7.3.0-43 (known issue)

- **Symptom.** An Edge J714s has no `/sys/class/platform-profile`, while the M5 lab kernel has one. So apple-clpc's power modes are unreachable: Low Power (2808 MHz ceiling and the SMC's 15 W PHPC hold) and Performance (macOS's High Power record to the SMC). The Mac runs at apple-clpc's default, Balanced.
- **Cause.** The distro configs build with `# CONFIG_ACPI is not set`. `ACPI_PLATFORM_PROFILE` was defined only under `if ACPI` and built only inside `drivers/acpi/`. So apple-clpc's `select ACPI_PLATFORM_PROFILE if ACPI` did nothing, and its `IS_REACHABLE()` guard silently skipped `devm_platform_profile_register()`. The lab kernel starts from defconfig, which has `CONFIG_ACPI=y`. A distro config line alone cannot fix this, because Kconfig drops the symbol without ACPI.
- **Fix (drafts).**
  - [linux#2549](https://github.com/aurora-silicon/linux/pull/2549), head `4dfa5b66` = e0ee46a1 plus one commit. It builds the class without ACPI, makes apple-clpc select it unconditionally, and has asahi.config request `CONFIG_ACPI_PLATFORM_PROFILE=y`.
  - Distro drafts: [fedora-aurora#451](https://github.com/aurora-silicon/fedora-aurora/pull/451) (7.3.0-44 pin), [alarm-aurora#380](https://github.com/aurora-silicon/alarm-aurora/pull/380) and [nixos-aurora#351](https://github.com/aurora-silicon/nixos-aurora/pull/351).
  - A Kconfig-only resolve of all three distro configs differs from e0ee46a1 by exactly that one line.
- **Effect elsewhere.** Only T6050 and T6041 (M4 Max) define a low-power profile in apple-clpc. The Neo (T8140) and the T8132, T8142 and T8152 register none and get only an empty class directory. Every other platform-profile driver in the tree needs X86 or ACPI.
- **Fedora also needs a userspace bridge.** The J714s rides Fedora KDE, which uses tuned-ppd. tuned's `acpi` plugin uses only `/sys/firmware/acpi/platform_profile`, which a device tree boot never has. aurora-power-budget (aurora-platform#16, in Fedora's pin) stops capping when an `apple-clpc` class device appears. So on Fedora, 7.3.0-44 alone would turn Power Saver into a no-op on T6050 and T6041. Today, aurora-power-budget's own cap applies there. The alarm and NixOS power-profiles-daemon 0.30 patch already drives the class.
- **For alpha.3.** Ship on 7.3.0-43 and list this as a known issue (release note below). Rebuilding to 7.3.0-44 now would buy nothing on Fedora until that bridge exists. It would also cost the Neo's qualification, which is bound to the 7.3.0-43 kernel image digest and signer run (see "Needs Dj").

### Why the J714s rides the Neo's Fedora KDE Edge delivery

- **The 65-model Testing cohort is not the route.** It is a metadata-only refresh of the November 13-15 Testing images, whose kernel (9125b314) has none of the M5 deep-sleep work. It is Testing, not Edge. And it must adopt ten profiles at once, including the separately qualified Omarchy delivery and six other T6050 models that have not been qualified.
- **A second Fedora KDE Edge delivery is not the route either.** Each profile's Edge endpoint is one signed catalog bound to one payload, and adoption replaces it whole, so a J714s-only delivery would remove the Neo from Edge.
- **What one shared payload needs:** `channel_policy` and Alpha 22 already accept several models that share one qualified payload. The changes are the `neo_edge.M5` switch, an image proof listing both models, and the M5's own evidence next to the Neo's.
- **The Neo evidence carries over.** It is bound to the kernel image digest, the Mesa DSO digest and the signer run, and none of those change. The new image does change the Neo's Stage 2 (aura 82ee5d6f), so the Neo needs one boot-and-sleep smoke on that Stage 2, not a new CTS.
- **No installer release is needed.** Alpha 22 fetches Edge live and shows Edge for any Mac its catalog lists. On a J714s, Testing shows as unavailable and Edge is chosen from the channel menu.
- **The Edge default comes from the catalog.** Installers with aurora-installer#176 open on Edge only for a Mac that the profile's Edge catalog proves: it must carry an `edgeQualification.devices` entry for that Mac. The alpha.3 catalog carries entries for `apple,j700` and `apple,j714s`, so with that installer the J714s opens on Edge; the app has no model rule. Without an entry it would open on Nightly, and an explicit Edge pick would still work.

## Steps from "M5 gate passes" to "J714s users get it on Edge"

The Neo lane's alpha.2 (Neo only) goes first and is not held for the M5. The M5 rides alpha.3 on the same kernel and Mesa pair.

1. **Release-gate boot passes** (Aurora Agents seq 1848). It must boot exactly what users get:
   - the signed kernel-aurora-core/modules 7.3.0-43 RPM contents with the in-tree `t6050-j714s.dtb` (not intg28d and not the lab DTB fdc15472);
   - aura 2f487f26 Stage 2 (rgate4a and rgate35c; the first gate, rgate2f, ran main 82ee5d6f);
   - no `arm64.nomte`;
   - the SEP as the default runs it: writes on, no `apple_sep.*` on the command line (rgate35c). The 11-11 J714s boot (linux#1587) is the write-path evidence;
   - the `aurora-m5-platform-config` files.

   Record the `releaseGate`, `deepSleep` and `restoration` receipts (sha256 of manifest files, as for the Neo) and the pair: kernel `sha256:701ed7f2…c5bf`, `libvulkan_asahi.so` `sha256:767f3e17…8424`, Mesa signer run 44832807927 (the -35 respin; the kernel stays on signer run 44694386531). The record is `receipts-rgate35c` (sha256 b6402244…40e3).
2. **Merge the distribution drafts after review and green CI.**
   - Order:
     - alarm aura → alarm MTE → alarm package, each with a merge commit;
     - nixos aura → nixos MTE → nixos module;
     - fedora aura → fedora MTE → fedora package. The package PR carries the MTE commit, so merge the MTE PR first.
   - The aura pin is 2f487f26, already on the three distros' main after its fleet rows (J773g, J713, J413, J714s; Aurora Agents seq 1921).
   - No kernel pin moves on main for this.
3. **Fedora Testing snapshot.**
   - The main RPM builds (aura, aurora-platform, aurora-m5-platform-config) feed `testing-YYYYMMDD.N`.
   - If the F44 publish reports IncompleteSnapshot, dispatch `rpms.yml -f packages="<missing>"` (F44 rebuilds).
   - Record the snapshot tag and its manifest sha256. The Edge image reads the snapshot release assets; it does not need a feed deploy.
4. **Edge image (fedora-aurora).** Create `edge/neo-f44-image-<pair>-m5` from `edge/neo-f44-image-e0ee46a1`, merge main, then change:
   - `.github/scripts/edge-f44-repository.py`: `BASE` and `MANIFEST_SHA` to the step 3 snapshot.
   - `.github/scripts/edge-f44-image-proof.py`:
     - `"models": ["apple,j700", "apple,j714s"]`;
     - require `aurora-m5-platform-config.noarch`, `aura.aarch64` at `1.0.0~b13.1-3` and `aurora-platform-core` at `44-46` or later among the installed packages;
     - check that the image's `boot-j714s.bin` Stage 2 carries the `1.0.0b13-g2f487f26` marker. Check which aura pin the image actually got. If b14 is tagged and the distro b14 pins (fedora-aurora#461, alarm-aurora#399, nixos-aurora#360, all at 7c3e778e) merge before the image builds, the image carries `1.0.0b14` instead: then require that marker and run one J714s aura row on the b14 release bytes before binding.
   - `edge-f44-image.yml`: the branch name. Keep `SIGNING_RUN 44694386531`: no RPM rebuild and no new signing.
   - Push the trigger file, then record the image run, the head SHA and the proof sha256.
5. **Neo smoke on the new Stage 2.** One boot and one deep sleep of the Neo on the alpha.3 image's aura (neohw lease, Neo lane). The Neo CTS evidence is reused unchanged.
6. **J714s fresh install: after release, from the live Edge channel** (decided 2026-11-20, as the Neo's alpha.1). It is not a gate: the M5 gates are `releaseGate`, `deepSleep` and `restoration`. The opt-in owner row is under "J714s fresh install from the live channel" below.
7. **aurora-distributions commit A for alpha.3.**
   - In `neo_edge.py`, set `M5 = True` and the new `IMAGE_BRANCH`, `IMAGE_HEAD` and `IMAGE_RUN` (signer runs and file digests stay).
   - `prepare-neo-edge-payload.py` → `-edge-alpha.3`.
   - `stage-neo-edge-payload.yml` pins.
   - Tests.
   - Then push `edge/stage-neo-payload-<name>`. Stage late: the catalog sequence is the staging time and must exceed every edge-alpha sequence served.
8. **Commit B** (`build-payload.yml` pins and the release-note text below) and the fedora `publish-neo-edge.yml` pins, as for alpha.2 (runbook S8/S9). The Neo binder step already writes the `edge-device-*.json` records, so its binder `ref:` must be commit A or later; an older `neo_edge.py` has no `device_evidence`, and the step fails.
9. **Publish:** dispatch `publish-neo-edge.yml` with `qualification = {"apple,j700": <alpha.2 Neo record>, "apple,j714s": <M5 record>}`.
10. **Adopt:** `adopt-delivery.yml` with `channel=edge`, then Pages. Verify both edge-alpha URLs:
    - the models are `apple,j700` and `apple,j714s`;
    - `m5Evidence` is present;
    - `edgeQualification.devices` has exactly `apple,j700` and `apple,j714s`, and `edge-device-apple-j700.json` and `edge-device-apple-j714s.json` are served beside each catalog, with the digests the catalog names;
    - the payload digest is the one both rows carry.
11. **Verify from the channel** on the J714s and the Neo with Alpha 22 (Edge from the channel menu on the J714s).

## With the Edge train (aurora-distributions#64)

The per-device records are the train's format, byte for byte. A test (`EdgeTrainDeviceRecordTests`) compares them with `edge_train._device_records` as soon as `scripts/edge_train.py` is on the branch.

- **#63 first.** alpha.3 ships through `adopt-delivery.py` as above. #64 merges cleanly on top. Its `verify-channel-catalogs.py` then re-checks the served J700 and J714s records, and its Testing preparation keeps them. The first train lists `riders: ["t6050"]` with the M5 row, or its adoption refuses to remove the J714s from Edge.
- **#64 first, before any train is adopted.** This branch still merges cleanly, and the single-profile path works unchanged.
- **After a train is adopted.** `adopt-delivery.py --channel edge` refuses. The J714s then rides the train as the `t6050` rider, on `fedora-kde` only:
  - the train's `edge-train-evidence` carries `families.t6050 = {"machine": "apple,j714s", "pair": <train.reference>, "checks": <the M5 checks>}`, which is the M5 record of this plan plus `machine`;
  - the train binder emits the J714s's `devices` entry and `edge-device-apple-j714s.json` itself.
- **Keep the gates in step.** `neo_edge.M5_GATES` and the `t6050` rider's `gates` in `edge/cohort.json` must stay the same set. `freshInstall` is dropped from both (decided 2026-11-20): the `t6050` rider's gates are `releaseGate`, `deepSleep` and `restoration`.

## Release note (alpha.3)

> **Fedora 44 KDE Edge Alpha 3: MacBook Neo and MacBook Pro (M5 Pro)**
>
> This release adds the MacBook Pro (M5 Pro, J714s) to Edge, with deep sleep. Closing the lid suspends on battery, on AC and docked, and the Mac wakes with its displays, USB and Thunderbolt devices, Wi-Fi and Bluetooth, audio and camera working. Choose **Edge** in the installer's channel menu.
>
> - Same kernel (aurora-edge, kernel-aurora 7.3.0-43) and Mesa (26.3.0-35) as Edge Alpha 2.
> - Fresh install on the MacBook Pro M5 Pro is verified from the live Edge channel after release, as for the Neo's alpha.1.
> - Memory Tagging (MTE) is on for the M5 Pro and Max. The boot loader reserves the tag storage, and the kernel turns MTE off by itself when it is not reserved.
> - Touch ID and the Secure Enclave can save state (SEP writes on), as on other Macs.
> - Suspend is deep sleep only on the J714s. A safety wake is set an hour ahead when nothing else is armed. If the lid is still closed, the Mac goes back to sleep.
>
> Known issues on the M5 Pro:
> - A sleep entered right after plugging or unplugging a USB-C device on a Dell monitor's port can hang in about 1 in 10 such sleeps; the Mac restarts by itself about 2 minutes later. A fix is in testing (aurora-silicon/linux#2524 among the candidates).
> - A Dell monitor's SuperSpeed USB hub can be missing after about 15 % of boots. It comes back after the first sleep.
> - A stray Wi-Fi or Bluetooth wake right after the Mac goes to sleep is treated as a dark wake: the screen stays off and the Mac goes back to sleep.
> - Edge installs do not receive package updates from the Testing feed.
> - The power mode setting (Power Saver, Balanced, Performance) does not yet reach the M5 Pro's own power modes: the kernel offers no platform profile. Power Saver still applies Aurora's CPU power budget. A fix is in review (aurora-silicon/linux#2549).

## J714s fresh install from the live channel (after adoption; opt-in owner row)

Decided 2026-11-20: the J714s fresh install is not a release gate. It is verified from the live Edge channel after adoption, as the Neo's alpha.1 was. The M5 lane's rig can't host it: a new install needs NVMe space carved from the macOS APFS container, a recoveryOS (1TR) boot-policy step with the owner's password and a power-button hold, and the interactive Plasma Setup on the Mac's displays. The release gate rows already cover what is specific to the J714s from the signed bytes (Stage 2, kernel, DTB, Mesa, MTE, SEP, deep sleep). The installer flow (partitioning, LUKS, Plasma Setup) is shared with the Neo and Testing.

PR readiness asks J714s owners on the agents channel for one opt-in row after adoption, Isaac's J714s first. The row:

**Setup**
- [ ] The owner's own J714s (MacBook Pro 14", M5 Pro) on macOS 27.2 (26B123), with enough free space for an install; the owner chooses to install. Record the macOS build, the installer version (Alpha 22 or later) and the catalog's payload digest.
- [ ] Install Fedora KDE from the installer's **Edge** channel, encryption on. The installer admits the J714s; Stage 1 is admitted for 27.2, and the board Stage 2 is `boot-j714s.bin` from the payload.
- [ ] First boot reaches the Plasma Setup owner flow; the owner account is created. The disk password replaces the temporary key: `/var/lib/aurora/firstboot/disk-rekeyed` exists and the temporary keyslot is gone (`cryptsetup luksDump`).

**Boot checks (installed system)**
- [ ] `uname -r` = `7.3.0-43.fc44.aurora.aarch64`; `/proc/cmdline` has no `arm64.nomte`.
- [ ] MTE: `grep -c mte /proc/cpuinfo` > 0, and `/proc/device-tree/reserved-memory/mte-tag-storage@*` exists.
- [ ] SEP writes on: no `apple_sep.*` on `/proc/cmdline`; `/sys/bus/platform/devices/*.sep/diag/policy` shows `xart_writes=1 provision_keybag=1`; `diag/xart` reads `enabled`; `aurora-sep status` shows no xART warning; Touch ID can enrol (`fprintd-enroll`).
- [ ] Leakage: the Stage 2 log has `FDT: GPU: leakage calibration words …`.
- [ ] `aurora-m5-sleep-policy.service` is active (exited), with both drop-ins under `/run/systemd/{sleep,logind}.conf.d/`; `HandleLidSwitchDocked` = `suspend`; `/sys/power/mem_sleep` lists `[deep]`; `suspend.target` and `sleep.target` are not masked.
- [ ] `ausearch -m avc -ts boot` is empty.
- [ ] Displays at native modes; USB, Thunderbolt and Type-C; Wi-Fi associated; Bluetooth powered; ALSA cards and PipeWire; the 6 tas2764 amps bound with Amp Fault Status 0 (speaker checks at 5 % volume through speakerguardd v2 only); ALS; camera frames; `vulkaninfo` lists the M5 Pro GPU, with the Edge Mesa (26.3.0-35, `git-15fe5010c1`).

**Deep sleep (installed system)**
- [ ] Three sleeps through logind (lid close, or `systemctl suspend`) with no alarm armed beforehand. `journalctl -t aurora-m5-sleep` shows the hook arming the alarm and clearing it after the wake. Each wake: displays, USB, Wi-Fi, Bluetooth, audio and camera back, and one `commit stop: PMP request withdrawn: 0` per entry.
- [ ] One sleep under Plasma with the lid closed on battery; record PowerDevil's docked behaviour.

**Updates and reboot**
- [ ] `dnf check-update` shows the Aurora repo disabled (Edge) and no error; a Fedora update transaction completes.
- [ ] A reboot, a cold boot after power-off, and the disk unlock prompt at each start, with the same password.

**Report** (a reply on the agents channel, no raw logs): the Mac, macOS build, installer version, payload digest, each item pass/fail with the one-line evidence, and anything that failed with its first error line. The owner keeps or removes the install; nothing on another lane's machine changes.

## Needs Dj

- **Decided 2026-11-20: the J714s fresh install runs after adoption, from the live channel** (as the Neo's alpha.1). `freshInstall` is dropped from `neo_edge.M5_GATES` and from the `t6050` rider of aurora-distributions#64's `edge/cohort.json`. The rig can't host an install (the macOS containers, 1TR, Plasma Setup on its displays, its enrolled dev Stage 1), and a test-only catalog row would need the catalog signer. Keep a Neo-only alpha.4 ready as the rollback; adopting it removes the served `edge-device-apple-j714s.json`.
- **MTE on every T6050 board.** The owner's T6050 policy turns MTE on for every T6050 board, including the K114/K116 and J775/J873 boards that are in Testing or the 65-model list. Only the J714s has run with MTE on.
- **Decided 2026-11-20: alpha.3 ships on 7.3.0-43, without platform profiles.** The missing profiles are a known issue in the release note. The Edge release is not rebuilt or re-pinned.
  - **Why not 7.3.0-44.** A rebuild would have needed a new signer run and image digest. The Neo evidence bound to `sha256:701ed7f2…c5bf` and run 44694386531 would then not carry over, and the M5 release gate would rerun. On Fedora it would also have needed the userspace bridge first, or Power Saver regresses on T6050 and T6041.
  - **Next.** linux#2549 and the Fedora bridge land together in the next regular kernel bump:
    - [fedora-aurora#451](https://github.com/aurora-silicon/fedora-aurora/pull/451): kernel-aurora 7.3.0-44, together with aurora-firstboot 0.1.13-7;
    - that aurora-firstboot pins [aurora-platform#35](https://github.com/aurora-silicon/aurora-platform/pull/35), where aurora-power-budget hands tuned-ppd's profile to apple-clpc's class device;
    - alarm and NixOS take [alarm-aurora#380](https://github.com/aurora-silicon/alarm-aurora/pull/380) and [nixos-aurora#351](https://github.com/aurora-silicon/nixos-aurora/pull/351). Their power-profiles-daemon already drives the class.
- **Decided 2026-11-20: SEP writes on for the T6050, with no extra write row.** The 11-11 J714s lab boot (linux#1587) is the evidence; the release note keeps "SEP writes on".
- **The aura pin (decided 2026-11-20).** alpha.3 ships fedora main's pin, aura 2f487f26 (branch `djdev/mcc-count-width-on-82ee5d6f`: main 82ee5d6f + aura#384 + the aura#392 MCC fix). Its label is `1.0.0b13-g2f487f26`; packages are fedora `aura-1.0.0~b13.1-3`, alarm `1.0.0b13.1-4` and nixos `1.0.0b13.1`. b14 reaches the M5 in the next Edge refresh. Check which aura pin the image actually got. If b14 is tagged and the distro b14 pins (fedora-aurora#461, alarm-aurora#399, nixos-aurora#360, all at 7c3e778e) merge before the image builds, the image carries `1.0.0b14` instead: then require that marker and run one J714s aura row on the b14 release bytes before binding.
