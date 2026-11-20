# M5 Pro (J714s) deep sleep on Edge: release plan

Status 2026-11-20: preparation only. Nothing here is published. The owner's decisions (2026-11-20) are:
- the channel is Edge;
- the T6050 runs with MTE on (no `arm64.nomte`);
- SEP writes are on (the distribution default `apple_sep xart_writes=1 provision_keybag=1`).

Nothing ships for the M5 until its release-gate boot passes.

## What reaches a J714s, and from where

| Piece | Source | Where it is prepared |
|---|---|---|
| Kernel | linux `aurora-edge` after linux#2527 (integration e0ee46a1, M5 deep-sleep merges included), kernel-aurora 7.3.0-43, signer run 44694386531 | Neo Edge refresh lane (alpha.2). Reused unchanged; no second kernel pin. |
| Mesa | mesa `aurora-edge` after mesa#566 (418bf795), mesa-aurora 26.3.0-34, same signer run | Neo Edge refresh lane. Reused unchanged. |
| Device tree | in-tree `t6050-j714s.dtb` from kernel-aurora-core 7.3.0-43 | in the kernel RPM |
| aura (Stage 2) | aura main 82ee5d6f (aura#214: T6050 sleep controller wake sources; 9f501f32: GPU leakage words from the fuses) | fedora/alarm/nixos aura pin drafts |
| MTE on T6050 | `arm64.nomte` only on T8142; a one-shot removal on T6050 | fedora `aurora-platform` 44-45, alarm `aurora-boot` 20261018-20, nixos `modules/aurora.nix` drafts |
| SEP writes on | distribution default, nothing to change; nothing on the M5 path sets `xart_writes=0` or `provision_keybag=0` | (none) |
| Rootfs deep sleep | `aurora-m5-platform-config`: deep sleep only, lid suspends on battery/AC/docked, `50-aurora-m5-rtc-safety` hook | fedora/alarm/nixos package drafts |
| Catalog | the J714s rides the Fedora KDE Edge payload beside the Neo (`neo_edge.M5`) | this repository's draft |

### Drafts (2026-11-20)

| Item | fedora-aurora | alarm-aurora | nixos-aurora |
|---|---|---|---|
| aura main 82ee5d6f | #448 | #376 | #348 |
| MTE on T6050 | #449 | #377 | #349 |
| aurora-m5-platform-config | #450 (contains #449's commit) | #378 (contains #377's commit) | #350 |
| Catalog switch + this plan | aurora-distributions #63 | | |

### Why the J714s rides the Neo's Fedora KDE Edge delivery

- **The 65-model Testing cohort is not the route.** It is a metadata-only refresh of the November 13-15 Testing images, whose kernel (9125b314) has none of the M5 deep-sleep work. It is Testing, not Edge. And it must adopt ten profiles at once, including the separately qualified Omarchy delivery and six other T6050 models that have not been qualified.
- **A second Fedora KDE Edge delivery is not the route either.** Each profile's Edge endpoint is one signed catalog bound to one payload, and adoption replaces it whole, so a J714s-only delivery would remove the Neo from Edge.
- **What one shared payload needs:** `channel_policy` and Alpha 22 already accept several models that share one qualified payload. The changes are the `neo_edge.M5` switch, an image proof listing both models, and the M5's own evidence next to the Neo's.
- **The Neo evidence carries over.** It is bound to the kernel image digest, the Mesa DSO digest and the signer run, and none of those change. The new image does change the Neo's Stage 2 (aura 82ee5d6f), so the Neo needs one boot-and-sleep smoke on that Stage 2, not a new CTS.
- **No installer release is needed.** Alpha 22 fetches Edge live and shows Edge for any Mac its catalog lists. On a J714s, Testing shows as unavailable and Edge is chosen from the channel menu. Making Edge the default for `apple,j714s` + `fedora-kde`, as for the Neo (`ReleaseChannelPreference.swift`), would be a one-line installer change and needs a new installer release. It is optional.

## Steps from "M5 gate passes" to "J714s users get it on Edge"

The Neo lane's alpha.2 (Neo only) goes first and is not held for the M5. The M5 rides alpha.3 on the same kernel and Mesa pair.

1. **Release-gate boot passes** (Aurora Agents seq 1848). It must boot exactly what users get:
   - the signed kernel-aurora-core/modules 7.3.0-43 RPM contents with the in-tree `t6050-j714s.dtb` (not intg28d and not the lab DTB fdc15472);
   - aura 82ee5d6f Stage 2;
   - no `arm64.nomte`;
   - SEP writes on;
   - the `aurora-m5-platform-config` files.

   Record the `releaseGate`, `deepSleep` and `restoration` receipts (sha256 of manifest files, as for the Neo) and the pair: kernel `sha256:701ed7f2…c5bf`, `libvulkan_asahi.so` `sha256:dea6ef52…af5a`, signer run 44694386531.
2. **Merge the distribution drafts after review and green CI.**
   - Order:
     - alarm aura → alarm MTE → alarm package, each with a merge commit;
     - nixos aura → nixos MTE → nixos module;
     - fedora aura → fedora MTE → fedora package. The package PR carries the MTE commit, so merge the MTE PR first.
   - Also merge the fleet row for aura 82ee5d6f first: a Neo plus one M1/M2 Stage 2 boot, because the aura sweep touches every chip.
   - No kernel pin moves on main for this.
3. **Fedora Testing snapshot.**
   - The main RPM builds (aura, aurora-platform, aurora-m5-platform-config) feed `testing-YYYYMMDD.N`.
   - If the F44 publish reports IncompleteSnapshot, dispatch `rpms.yml -f packages="<missing>"` (F44 rebuilds).
   - Record the snapshot tag and its manifest sha256. The Edge image reads the snapshot release assets; it does not need a feed deploy.
4. **Edge image (fedora-aurora).** Create `edge/neo-f44-image-<pair>-m5` from `edge/neo-f44-image-e0ee46a1`, merge main, then change:
   - `.github/scripts/edge-f44-repository.py`: `BASE` and `MANIFEST_SHA` to the step 3 snapshot.
   - `.github/scripts/edge-f44-image-proof.py`:
     - `"models": ["apple,j700", "apple,j714s"]`;
     - require `aurora-m5-platform-config.noarch`, `aura.aarch64` at `1.0.0~b13.1-2` and `aurora-platform-core` at `44-46` or later among the installed packages;
     - check that the image's `boot-j714s.bin` Stage 2 carries the `1.0.0b13-g82ee5d6f` marker.
   - `edge-f44-image.yml`: the branch name. Keep `SIGNING_RUN 44694386531`: no RPM rebuild and no new signing.
   - Push the trigger file, then record the image run, the head SHA and the proof sha256.
5. **Neo smoke on the new Stage 2.** One boot and one deep sleep of the Neo on the alpha.3 image's aura (neohw lease, Neo lane). The Neo CTS evidence is reused unchanged.
6. **J714s fresh install** (checklist below). This needs a decision from Dj; see "Needs Dj".
7. **aurora-distributions commit A for alpha.3.**
   - In `neo_edge.py`, set `M5 = True` and the new `IMAGE_BRANCH`, `IMAGE_HEAD` and `IMAGE_RUN` (signer runs and file digests stay).
   - `prepare-neo-edge-payload.py` → `-edge-alpha.3`.
   - `stage-neo-edge-payload.yml` pins.
   - Tests.
   - Then push `edge/stage-neo-payload-<name>`. Stage late: the catalog sequence is the staging time and must exceed every edge-alpha sequence served.
8. **Commit B** (`build-payload.yml` pins and the release-note text below) and the fedora `publish-neo-edge.yml` pins, as for alpha.2 (runbook S8/S9).
9. **Publish:** dispatch `publish-neo-edge.yml` with `qualification = {"apple,j700": <alpha.2 Neo record>, "apple,j714s": <M5 record>}`.
10. **Adopt:** `adopt-delivery.yml` with `channel=edge`, then Pages. Verify both edge-alpha URLs:
    - the models are `apple,j700` and `apple,j714s`;
    - `m5Evidence` is present;
    - the payload digest is the one both rows carry.
11. **Verify from the channel** on the J714s and the Neo with Alpha 22 (Edge from the channel menu on the J714s).

## Release note (alpha.3)

> **Fedora 44 KDE Edge Alpha 3: MacBook Neo and MacBook Pro (M5 Pro)**
>
> This release adds the MacBook Pro (M5 Pro, J714s) to Edge, with deep sleep. Closing the lid suspends on battery, on AC and docked, and the Mac wakes with its displays, USB and Thunderbolt devices, Wi-Fi and Bluetooth, audio and camera working. Choose **Edge** in the installer's channel menu.
>
> - Same kernel (aurora-edge, kernel-aurora 7.3.0-43) and Mesa (26.3.0-34) as Edge Alpha 2.
> - Memory Tagging (MTE) is on for the M5 Pro and Max. The boot loader reserves the tag storage, and the kernel turns MTE off by itself when it is not reserved.
> - Touch ID and the Secure Enclave can save state (SEP writes on), as on other Macs.
> - Suspend is deep sleep only on the J714s. A safety wake is set an hour ahead when nothing else is armed. If the lid is still closed, the Mac goes back to sleep.
>
> Known issues on the M5 Pro:
> - A sleep entered right after plugging or unplugging a USB-C device on a Dell monitor's port can hang in about 1 in 10 such sleeps; the Mac restarts by itself about 2 minutes later. A fix is in testing (aurora-silicon/linux#2524 among the candidates).
> - A Dell monitor's SuperSpeed USB hub can be missing after about 15 % of boots. It comes back after the first sleep.
> - A stray Wi-Fi or Bluetooth wake right after the Mac goes to sleep is treated as a dark wake: the screen stays off and the Mac goes back to sleep.
> - Edge installs do not receive package updates from the Testing feed.

## J714s fresh-install qualification (RELEASING.md "Qualification and storage")

These steps run on the M5 lane's J714s. Rules for the run:
- the M5 hardware lock only;
- WSI headless; no test windows on the M5 displays;
- speaker checks at 5 % volume through speakerguardd v2 only;
- before any VDM, check the partner identity with `rig/m5-vdm-partner-check.sh`.

The install must come from the alpha.3 payload, byte for byte (the STAGING.sha256 digest of step 7).

**Before the install**
- [ ] Back up what the M5 lane needs to restore the current default (verified copy and restore commands).
- [ ] Check free space for the download, extraction and copy peaks; keep at least 8 GiB free on the development host.
- [ ] Record macOS 27.2 (26B123) on the J714s, the installer version (Alpha 22) and the payload digest.

**Install**
- [ ] Install Fedora KDE with encryption on. Use the Edge channel when the catalog is live, or the local-payload test installer if Dj chooses the pre-adoption route.
- [ ] The installer admits the J714s; Stage 1 is admitted for 27.2. The board Stage 2 is `boot-j714s.bin` with marker `1.0.0b13-g82ee5d6f`.
- [ ] First boot reaches the Plasma Setup owner flow. The owner account is created. The disk password replaces the temporary key: `/var/lib/aurora/firstboot/disk-rekeyed` exists and the temporary keyslot is gone (`cryptsetup luksDump`).

**Boot checks (installed system)**
- [ ] `uname -r` = `7.3.0-43.fc44.aurora.aarch64`; `/proc/cmdline` has no `arm64.nomte`.
- [ ] MTE: `grep -c mte /proc/cpuinfo` > 0, and `/proc/device-tree/reserved-memory/mte-tag-storage@*` exists.
- [ ] SEP writes on: `/sys/module/apple_sep/parameters/xart_writes` = 1 and `provision_keybag` = 1. Touch ID enrolment works; `aurora-sep status` shows no xART warning.
- [ ] Leakage: the Stage 2 log has `FDT: GPU: leakage calibration words …`, and `work/aura-leakage-fill-20261120/verify-j714s-unit-values.py` passes.
- [ ] `aurora-m5-sleep-policy.service` is active (exited), and both drop-ins are under `/run/systemd/{sleep,logind}.conf.d/`.
- [ ] `busctl get-property org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager HandleLidSwitchDocked` = `suspend`.
- [ ] `cat /sys/power/mem_sleep` lists `[deep]`; `suspend.target` and `sleep.target` are not masked.
- [ ] `ausearch -m avc -ts boot` is empty, for the hook's sysfs writes and logind reading `/run/systemd/logind.conf.d`.
- [ ] Displays at native modes (panel, and Dell DP alt mode and the TB5 dock if attached); USB, Thunderbolt and Type-C; Wi-Fi associated; BT powered; the ALSA cards and PipeWire; 6 tas2764 amps bound with Amp Fault Status 0; ALS; camera frames; `vulkaninfo` plus the dEQP-VK smoke, with no GPU fault lines.

**Deep sleep (installed system)**
- [ ] Three sleeps through logind (lid close, or `systemctl suspend`) with no alarm armed beforehand. `journalctl -t aurora-m5-sleep` shows the hook arming the alarm and clearing it after the wake.
- [ ] After each wake, the post-wake checks of the gates (RELEASE-HANDOFF.md "Checked after every wake"), and one `commit stop: PMP request withdrawn: 0` per entry.
- [ ] One sleep under Plasma with the lid closed on battery; record PowerDevil's docked behaviour.

**Updates and reboot**
- [ ] `dnf check-update` shows the Aurora repo disabled (Edge) and no error; a Fedora update transaction completes.
- [ ] A reboot, a cold boot after power-off, and the disk unlock prompt at each start, with the same password.
- [ ] Restoration: put the M5 lane's default back (rollback set), then verify it.

**Records**
- [ ] Write `freshInstall` and `restoration` receipts (sha256 of the sha256sum manifests). The Edge publisher reads only their digests.

## Needs Dj

- **When the J714s fresh install runs.** The Edge catalog is fetched live and bundled catalogs are Testing-only, so the documented local-payload test installer (aurora-installer `macOS/docs/private-local-payload-install.md`) cannot see an unpublished Edge row. There are two choices:
  - Before adoption: sign a test-only Testing catalog row for `apple,j714s` on an unmerged branch for that test installer. This needs the catalog signer.
  - After adoption, from the live channel, as the Neo's alpha.1 was verified. Drop `freshInstall` from `neo_edge.M5_GATES` and keep a Neo-only alpha.4 ready as the rollback.
- **MTE on every T6050 board.** The owner's T6050 policy turns MTE on for every T6050 board, including the K114/K116 and J775/J873 boards that are in Testing or the 65-model list. Only the J714s has run with MTE on.
- **The aura pin.** Pin aura main 82ee5d6f, which is what the gate boots, or wait for a tagged b14.
