# Getting macOS back

`aurora-restore` puts macOS back on your Mac from Aurora. You choose a version, Aurora makes room for it, and Apple's own installer installs it from Recovery. Aurora can stay installed next to it, or macOS can take Aurora's space.

**Status: preview.** The tool has been tested on disk images and against stand-ins for Apple's tools, but not yet on a real Mac. Wait for this line to change unless you're comfortable restoring your Mac yourself (see the last section).

## Why Recovery does the installing

Your Mac starts only a macOS that Apple's installer set up. The installer creates macOS's volumes, has Apple prepare the startup files for your Mac, and asks the Mac's owner to approve the new startup disk. Linux can't do any of that, so Aurora prepares and Apple's installer installs. Aurora never changes your Mac's startup security, its firmware or Apple's System Recovery.

## Before you start

- **A network.** The installer comes from Apple (12-18 GB, depending on the version).
- **An owner.** Someone has to approve the new macOS: an administrator of a macOS still on the Mac, or, if macOS was removed, Aurora's recovery account. If neither exists, `aurora-restore` says so and only a full restore from a second computer is left (last section).
- **Partitions.** With `--replace`, only this installation's partitions are deleted: its EFI partition, its root file system and the partitions its `/etc/fstab` mounts. If another Linux partition on the disk is mounted somewhere else, `plan` stops and names it.
- **Room.** About twice the installer's size plus 35 GB (66 GiB for macOS 27.2.1). `aurora-restore plan` prints the exact figure. Back up anything important in Aurora first.

## Steps

1. Install the tool: `sudo dnf install aurora-restore` (Fedora), `sudo pacman -S aurora-restore` (Arch), or add `aurora-restore` to `environment.systemPackages` (NixOS).
2. See what Apple offers for your Mac:

       aurora-restore list

   It lists the newest release of each macOS version that supports your Mac.
3. See exactly what would happen, with nothing changed yet:

       sudo aurora-restore plan 27.2.1

   By default macOS goes **next to** Aurora: Aurora's root file system shrinks to make room (your files are kept). With `--replace`, Aurora's Linux partitions are deleted in Recovery and macOS gets their space. That deletes everything in Aurora.
4. Prepare it:

       sudo aurora-restore apply 27.2.1

   Type the sentence it asks for. It prints one line to type in Recovery.
5. Shut down. Press and hold the power button until "Loading startup options" appears, choose **Options**, and log in if asked. Join a network from the Wi-Fi menu. Open **Utilities > Terminal** and type the line from step 4.
6. Recovery checks that the disk is exactly as planned. It asks for the sentence again, creates the macOS disk, downloads the installer from Apple and starts it. Choose the new disk ("macOS 27.2.1") and approve with the owner when asked. When it's done, the Mac starts macOS.

If it stops part-way (a lost network, a closed Terminal, a power cut), run it again; it carries on from where it stopped, also in the middle of `--replace`'s deletes. The message it stops with names the line to use: the same line as before or, once the macOS disk exists, `sh "/Volumes/macOS 27.2.1/.aurora-restore/restore.sh"`. To undo step 4 before step 6, run `sudo aurora-restore cancel --regrow` in Aurora.

After `--replace`, Aurora's small startup disk (its boot stub and Aurora's Recovery) is left behind, because the installer runs from its Recovery. It can't start anything without Linux. Once the new macOS works, delete it in Disk Utility (View > Show All Devices).

To switch between macOS and Aurora afterwards, use the startup options (hold the power button) or Startup Disk in macOS. To remove Aurora later, use Aurora Installer in macOS.

## Without the tool

Recovery can also install macOS by itself, but you can't choose the version:

- **Aurora's Recovery** (hold the power button, then Options): "Reinstall macOS" installs the newest release of the macOS version Aurora's startup disk uses.
- **System Recovery** (shut down, press and release the power button, then press and hold it): installs a macOS at least as new as itself.

It still needs room: delete Aurora's partitions in Disk Utility or make space with `aurora-restore apply` first. `aurora-restore plan` tells you when one of these covers the version you chose.

## Limits

- Only versions Apple still offers and signs: the newest release of each macOS version that supports your Mac. `--older` tries an older release; Apple usually refuses it.
- A macOS older than Aurora's Recovery may be refused by Apple's installer. `plan` marks this case; it hasn't been tried on a real Mac yet.
- The default mode needs a plain (not encrypted) btrfs root right before Apple's System Recovery partition, and room left for what Aurora already uses plus 10 GiB. Otherwise use `--replace` or make room yourself.

## The last resort: a second computer

Over USB-C in DFU mode, a second computer can **revive** the Mac, which reinstalls its firmware and Recovery (System Recovery included) and keeps your data. Use this when Recovery itself is broken. It can also **restore** the Mac, which erases everything, Aurora included, and installs the newest macOS and firmware.

- **With another Mac:** Apple's [How to revive or restore Mac firmware](https://support.apple.com/en-us/108900) (Finder or Apple Configurator, "Revive" or "Restore").
- **With a Linux computer:** a current build of [idevicerestore](https://github.com/libimobiledevice/idevicerestore). Distribution packages from before November 2026 are too old for macOS 27. `aurora-restore dfu-help` prints the steps.
