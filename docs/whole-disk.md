# Using the whole disk (removing macOS)

Aurora normally lives beside macOS. If you don't want macOS at all, for example on a 256 GB Mac where macOS takes most of the disk, you can delete it and give its space to Aurora.

**Read this whole page first. This can't be undone.** It deletes macOS, every macOS app and every file you kept in macOS. The only way back is to erase the Mac completely and restore it (see "Getting macOS back").

**Status: preview.** The tools are tested on disk images and read-only on a real Mac. The deletion has not been run on a real Mac yet. Wait for this line to change unless you're comfortable restoring your Mac yourself.

## What changes

- **macOS is gone.** No macOS apps, no Boot Camp-style switching, no macOS Recovery for macOS. Aurora is the only system on the Mac.
- **Apple's updates stop.** macOS used to update the Mac's system firmware. Without macOS it stays at its current version. So **update macOS to the newest version before you remove it**. Aurora's own startup firmware keeps being updated by Aurora Installer's startup-firmware path, which doesn't need macOS.
- **Your recovery account becomes the Mac's owner.** Every change to Aurora's startup security later (a new boot loader, new startup firmware, security settings) asks for that account's password in Aurora's Recovery. Nobody else can approve those changes. Write the password down somewhere safe.
- **Recovery still works.** Holding the power button still opens Aurora's own Recovery. Apple's System Recovery (double-press and hold) is kept too.

What is kept: Apple's system partitions (the first and the last on the disk), Aurora's startup partition and its Recovery, Aurora's EFI and Linux partitions. Only the macOS partition is deleted.

## Before you start

1. **Back up** everything you want from macOS. It will be deleted.
2. **Update macOS** to the newest version (System Settings → General → Software Update).
3. **Turn off Find My** (System Settings → your name → iCloud → Find My Mac), or remove the Mac at icloud.com/find. Otherwise restoring the Mac later needs that Apple Account. The removal refuses while Find My is on.
4. **Aurora must have its own recovery account.** It's created in Aurora's Recovery at the end of installation (Aurora Installer → Utilities → Create Recovery Account…) and authorized in Startup Disk ("Authorize Users"). The removal refuses without it, because without macOS nobody else could ever approve a startup change.
5. **Aurora must be the startup disk** (Apple menu → System Settings → General → Startup Disk in macOS, or Startup Disk in Recovery).

Check from Aurora what would happen (this changes nothing):

```sh
sudo aurora-whole-disk plan
```

It lists exactly what would be deleted and what stays, and says what is missing if you can't go ahead yet.

## Remove macOS

1. Shut down. Press and hold the power button until "Loading startup options" appears. Choose **Options**, then **Continue**.
2. Open **Utilities → Terminal**.
3. See the plan again (still nothing changes); `aurora-whole-disk plan` printed the exact path for your Mac, for example:

   ```sh
   '/Volumes/Aurora/aura' remove-macos --dry-run
   ```

4. Remove macOS:

   ```sh
   '/Volumes/Aurora/aura' remove-macos
   ```

   It shows the plan and what changes, then asks you to type `remove macos and all its data` and your recovery account's password. It checks the disk once more right before deleting, deletes only the macOS partition and checks everything else is unchanged.
5. Restart into Aurora. On its first start Aurora adds the freed space to your system (you can also run `sudo aurora-whole-disk claim`). Check with `df -h /`.

## Fresh install with the whole disk

Install Aurora as usual and choose the largest size the installer offers. Finish setup in Aurora's Recovery, including the recovery account and its authorization in Startup Disk. Then follow "Remove macOS" above.

## Getting macOS back

You need to erase the Mac in DFU mode and restore it. This deletes Aurora too; you reinstall it afterwards.

- **With another Mac:** follow Apple's [How to revive or restore Mac firmware](https://support.apple.com/en-us/108900) (Finder or Apple Configurator, "Restore").
- **With a Linux computer:** put the Mac in DFU mode and restore it with a current build of [idevicerestore](https://github.com/libimobiledevice/idevicerestore) (`idevicerestore -e -R <restore image>`). Ask in the Aurora community for the exact steps for your Mac.

## Limits

- Aurora adds the space as a second device of its btrfs root file system. If your root is encrypted, or isn't btrfs, Aurora leaves the space free instead.
- Only one macOS can be removed this way. Erase extra macOS installations with Aurora Installer first.
- Installations made by older installers have no Aurora Recovery yet; `aurora-whole-disk plan` says so. Update with Aurora Installer first.

Technical design: [aurora-installer macOS/docs/whole-disk.md](https://github.com/aurora-silicon/aurora-installer/blob/main/macOS/docs/whole-disk.md).
