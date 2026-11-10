# Flicker-free boot on Apple Silicon

From power-on to the desktop the screen shows one picture: the logo the
bootloader draws, at one size and place, with the distribution's branding added
at the bottom once the splash starts. Nothing in between clears the screen,
prints text or switches the display mode. This page lists what each boot stage
does to keep that frame, the settings each distribution ships for it, and how
to check a machine.

It applies to every Aurora distribution (Fedora, ALARM, NixOS) and to
downstreams built on the same boot chain. The stages are the same for
everyone; which images appear is configuration (see Branding).

## Branding

Three images, chosen per distribution or downstream:

| Parameter | Where it comes from | Aurora distributions | A downstream with one logo |
|---|---|---|---|
| Boot logo | aura's built-in logo (Stage 1 and Stage 2), and the same two PNGs in the Plymouth theme | Aurora logo | its own logo in both places |
| Bottom mark | `watermark.png` in the theme's `ImageDir` (the distribution's Plymouth spinner theme); no file, no mark | Fedora, Arch or NixOS logo | none |
| Disk-unlock screen | the same theme: boot logo kept, password entry below it | Aurora logo + mark | its own logo only |

- Boot logo: build aura with `make LOGO_DIR=<dir> BUILTIN_LOGO=<name>` after
  `data/makelogo.py <image> --name <name> --output-dir <dir>` (aura's README);
  every build checks the logo is present at 48, 128 and 256 px. Ship the 128
  and 256 px renders of the same image as the theme's `aurora-logo-128.png` and
  `aurora-logo-256.png`, so Plymouth draws exactly what aura drew. The two must
  match pixel for pixel, or the logo visibly changes when the splash starts.
- Bottom mark: the theme draws `watermark.png` from its `ImageDir` when there
  is one. The Aurora packages point `ImageDir` at the distribution's spinner
  theme, so the mark is the installed distribution's own: Fedora's comes from
  `fedora-logos`, ALARM's `plymouth` package installs Arch's
  `archlinux-logo-text-dark`, NixOS uses `boot.plymouth.logo` (the NixOS
  snowflake). One theme, no per-distribution copy. For no mark, point
  `ImageDir` at a directory that holds the spinner, entry, lock and bullet
  images without a `watermark.png`.

## The chain

| Stage | Draws | Must not |
|---|---|---|
| aura (Stage 1 and Stage 2) | the logo, centred, on black | - |
| U-Boot | nothing on a normal boot | clear the screen, draw its logo or banner, print messages |
| shim and GRUB | nothing (hidden menu) | switch to `gfxterm`, print errors |
| Linux, early | nothing | let fbcon take the console, print at the console log level |
| Plymouth on simpledrm | the same logo on the same pixels, a spinner, the distro branding | scale the logo, move it |
| Plymouth on appledrm | the same frame | blank during the DCP takeover |
| Plymouth disk unlock (encrypted root) | the password entry below the same logo | blank before or after the prompt |
| display manager / compositor | the greeter or desktop | change the display mode, colour space or brightness on takeover |

## aura: the framebuffer and the logo

aura draws its built-in logo in the boot framebuffer it receives from iBoot:
256 x 256 px when the framebuffer has the Retina flag (every internal panel),
128 x 128 px otherwise, at `((width - size) / 2, (height - size) / 2)` of the
full framebuffer. `src/fb.c` (`fb_init`, `fb_blit_logo`) holds the rule.

aura hands the framebuffer to the next stage in the device tree:

- `/chosen/framebuffer@<addr>`: a `simple-framebuffer` with `width`, `height`,
  `stride`, `format` (`x2r10g10b10` on 10-bit panels) and `reg`.
- `/reserved-memory/framebuffer@<addr>`: the original reservation. On a notched
  panel where the notch rows are hidden, aura advances the chosen base by
  `notch rows x stride` and keeps the reservation's base; the difference is the
  hidden row count. With `asahi,show-notch` the chosen framebuffer is the whole
  panel.

The logo position therefore depends only on the full panel size, which every
later stage can recompute from the device tree.

## U-Boot: quiet video console

U-Boot must leave the picture alone. Apple builds (`apple_m1_defconfig`) set:

- `CONFIG_NO_FB_CLEAR=y`: the framebuffer is not cleared when the video device
  probes.
- `CONFIG_VIDEO_QUIET_BOOT=y`: the video console holds text back while U-Boot
  boots an operating system. U-Boot's logo is not drawn, its banner and
  messages (including the EFI loader's CTXR window placement lines) go to the
  serial console only, and a clear-screen request from an EFI application is
  not drawn. The video console starts showing text, after clearing the screen,
  as soon as it is needed:
  - a key stops the autoboot;
  - U-Boot falls back to its command line (it prints its version first);
  - an EFI application writes visible text (a boot menu, an error).
- Verbose boots: set the `verbose` environment variable, or a `u-boot,verbose`
  property in `/chosen` of the control device tree, to see everything from the
  start.

`/proc/device-tree/chosen/u-boot,version` in Linux shows which U-Boot booted.

## shim and GRUB

GRUB keeps the screen untouched only when it writes to the firmware console
and prints nothing:

- `GRUB_TERMINAL_OUTPUT="console"`. `gfxterm` sets a graphics mode and clears
  the screen even with a hidden menu. Fedora: `aurora-platform` switches an
  existing `gfxterm` configuration once (`aurora-grub-console`). ALARM:
  `aurora-boot` ships `40-aurora-console.cfg`. NixOS: `boot.loader.grub.font`
  and `splashImage` are null.
- A hidden menu with a short timeout (`GRUB_TIMEOUT_STYLE=hidden`).
- No errors. Any GRUB message ends U-Boot's quiet console, which then clears
  the screen and shows it, as it should. A common one is
  `error: ... grub_search_fs_uuid ... no such device: XXXX-XXXX.` from a
  `search --fs-uuid --set=boot` line that names an ESP UUID the disk no longer
  has. Fedora's grub.cfg is generated once, so `aurora-platform` regenerates it
  when its only stale lines are such searches (`aurora-grub-uuid`). ALARM
  regenerates grub.cfg from a pacman hook and NixOS generates its loader
  configuration, so neither keeps a stale line.

## Linux

Kernel configuration and command line:

- `CONFIG_DRM_SIMPLEDRM=y` takes the boot framebuffer as `card0`
  (`[drm] Initialized simpledrm ... framebuffer`), without clearing it.
- `CONFIG_FRAMEBUFFER_CONSOLE_DEFERRED_TAKEOVER=y`: fbcon binds only when
  something is written to the console (`fbcon: Deferring console take-over`).
  A boot that logs `fbcon: Taking over console` has printed to the screen.
- `quiet` on the command line (console log level 4): only errors reach the
  console. Kernel information such as `ctxr-execmem: reserved exec window` is
  logged at info level and stays in `dmesg`. `rhgb` asks for the graphical
  splash, and `plymouth.ignore-serial-consoles` keeps a serial console (the
  device tree's `stdout-path`) from forcing Plymouth's text view.
- appledrm takes the internal panel over from simpledrm without a mode set.
  A desktop 26.x DCP (M4 Pro) checks the panel while the boot framebuffer
  still owns the screen, then adopts the mode the loader left running
  (`takeover: adopting the live mode (color:.. timing:..)`) instead of setting
  it again, which would blank the panel for about 200 ms. The first commit from
  user space keeps it (`keeping the takeover mode`, debug level). If no swap
  completes on the adopted mode, the swap watchdog sets the mode after all;
  `appledrm.takeover_adopt=0` turns adoption off. An H17P DCP (J700) inherits
  the boot mode in its own mode-set path (`set_digital_out_mode finished`
  returns at once). In both cases the firmware logs no `Modeset requested`
  for the internal panel before the desktop.

Early KMS: the display driver is in the initramfs and the display devices are
coldplugged before the rest, so Plymouth starts on simpledrm early and follows
appledrm when it appears. appledrm registers its DRM device only once every DCP
has bound, the external ones included, and an external DCP binds only after its
Type-C PHY, whose retimer or PD controller sits on an I2C bus. The initramfs
therefore also carries `i2c-pasemi-platform`, `phy-apple-atc`, `phy-ti-cd2e22`
and `tps6598x`, and the hook queues the Type-C PHYs and Apple I2C controllers
with the display. Without them a J700's internal DCP started in the initramfs
(and stopped showing the boot framebuffer) while appledrm registered only on
the real root, about 5 s later: a black panel in between. Fedora: dracut module `91aurora-early-kms`. NixOS:
`aurora-early-kms` initrd unit. ALARM: the `aurora-early-kms` mkinitcpio hook.

## Plymouth

### Theme

One theme, `aurora`, built from the same script in the three distribution
repositories (`packages/plymouth-theme-aurora` in fedora-aurora and
alarm-aurora, `plymouth-theme-aurora/` in nixos-aurora):

- Plymouth runs with `DeviceScale=1`. The theme draws in device pixels, so the
  logo matches aura's pixel for pixel; Plymouth's own 2x scale on Retina panels
  would double it. The packages add the setting to `plymouthd.conf` (NixOS:
  `boot.plymouth.extraConfig`), and an owner's own `DeviceScale` wins.
- Logo size: 256 px on internal Apple panels, 128 px elsewhere. Plymouth cannot
  see the Retina flag, so the script decides by mode: internal panels are at
  least 2304 x 1440 and not 16:9 (except the iMac's 4480 x 2520).
- Logo place: centred in the full panel, minus hidden notch rows. The helper
  `aurora-plymouth` runs before `plymouthd` on every boot, reads the panel
  size, hidden rows and `apple,notch-height` from the live device tree and
  writes `/run/plymouth/aurora.script` (the installed script with three numbers
  prepended). Details and fixtures: `GEOMETRY.md` beside the theme.
- Spinner below the logo; passphrase prompts on the same screen.
- Distribution branding at the bottom: the watermark the distribution ships for
  Plymouth's spinner theme, which the theme uses as its image directory, so
  initramfs generators copy it with the theme. Fedora: `fedora-logos`. ALARM:
  the `plymouth` package installs Arch's `archlinux-logo-text-dark` as the
  spinner watermark. NixOS: `boot.plymouth.logo`. A downstream adds its logo
  the same way, without changing the theme.

| Panel | Size | Hidden rows | Logo (full panel) |
|---|---|---|---|
| MacBook Pro 16" M4 Pro (J616s) | 3456 x 2234 | 74 | (1600, 989), 256 px |
| MacBook Air M2 (J413) | 2560 x 1664 | 64 | (1152, 704), 256 px |
| J700 | 2408 x 1506 | 0 | (1076, 625), 256 px |

### Disk unlock

With an encrypted root, systemd's password agent asks Plymouth, and the theme
replaces the spinner with a lock icon, the prompt and the password field at the
spinner's place, below the logo; bullets show the typed length. The logo,
background and bottom mark do not move or redraw, and nothing changes the
display mode, so there is no blank before the prompt or after the password is
accepted: the spinner comes back and the boot continues on the same frame. The
prompt is drawn on every display Plymouth owns, at each display's scale.
`plymouth.ignore-serial-consoles` matters here: without it a serial console
makes Plymouth show the prompt in its text view.

### Plymouth itself

Carried Plymouth changes (same set in the three repositories):

- start `plymouthd` once udevd runs, not after the whole coldplug;
- fix the race between the simpledrm framebuffer device and its DRM device;
- reset HDR connector state a previous DRM master left before drawing SDR;
- keyboard and terminal lifetime fixes for the script plugin.

When simpledrm goes away, Plymouth drops `card0`, opens appledrm's card and
keeps the splash: `Splash already shown, so pausing display until next frame
update` in `plymouth.debug` output, followed by the next frame on the new
device.

### Initramfs

The initramfs holds its own copy of the theme and `plymouthd.conf`, so it has
to be rebuilt whenever either changes:

- Fedora: dracut module `96aurora-plymouth` installs the helper and its
  `plymouth-start.service` drop-in; the theme package rebuilds the initramfs
  on install and on every update while it is the selected theme.
- ALARM: the `aurora-plymouth` mkinitcpio hook goes right before `plymouth`;
  mkinitcpio's pacman hook rebuilds the images when the package's initcpio
  files change.
- NixOS: `modules/boot-splash.nix` runs the helper in the systemd initrd's
  `plymouth-start` pre-start and adds its store paths to the initrd.

## Display manager and compositor

GDM (and Plasma Login on NixOS) owns the splash until its first frame: Plymouth
is deactivated, the compositor takes the DRM master, and `plymouth quit
--retain-splash` leaves the last frame until the compositor presents. For no
visible change at that point, the compositor's first commit has to keep the
mode Plymouth used:

- the same mode (resolution and refresh), so the driver needs no mode set;
- the same colour state, or one the driver switches without a mode set. On an
  internal panel with EDR headroom (a desktop 26.x DCP) SDR and PQ only change
  how the DCP treats the presented surfaces: a compositor that turns HDR on at
  startup gets `EDR on without a mode set` (appledrm debug output) and the
  panel keeps its frame. SDR content stays at the backlight's white, with the
  highlights above it. A change of `Colorspace` or EOTF on other outputs is
  still a mode set.
- the same brightness. The loader boots the panel at the level in the NVRAM
  variable `backlight-nits` (16.16 fixed-point nits), which macOS keeps; macOS
  then adjusts from that level. A level restored from a previous Linux session
  (`systemd-backlight@backlight:apple-panel-bl`) or by the desktop that differs
  from it fades the panel to that level a few seconds into boot.

## Verifying a machine

1. `cat /proc/cmdline`: `rhgb quiet`, and `plymouth.ignore-serial-consoles`.
2. `journalctl -b -k | grep -E 'simpledrm|fbcon|takeover trial|appledrmfb'`:
   appledrm's `fb0: appledrmfb` within a fraction of a second of the internal
   DCP's `RTKit: Initializing`, before `Switching root` on a J700;
   simpledrm initialised, `Deferring console take-over` only, the takeover
   trial `accepted`.
3. `tr -d '\0' < /proc/device-tree/chosen/u-boot,version`: a U-Boot with the
   quiet video console.
4. `grep -n 'search .*--fs-uuid' /boot/grub2/grub.cfg` (Fedora) and `blkid`:
   every UUID it searches exists.
5. `plymouth-set-default-theme` is `aurora`; `lsinitrd` (Fedora) or
   `lsinitcpio` (ALARM) lists `usr/share/plymouth/themes/aurora/aurora.script`,
   `usr/libexec/aurora-plymouth` and `etc/plymouth/plymouthd.conf` with
   `DeviceScale=1`.
6. One boot with `appledrm.dyndbg=+p`: the display manager's first commit
   logs no second `set_digital_out_mode(...)` after `keeping the takeover mode`.
7. One boot with `plymouth.debug`: `/var/log/plymouth-debug.log` shows the
   simpledrm head (`drm driver: simpledrm`), its removal, the appledrm head
   (`drm driver: apple`) and `pausing display until next frame update`.
8. What the loaders left on screen: boot once with `plymouth.enable=0
   rd.plymouth=0`, then read the region in `/chosen/framebuffer@<addr>`'s `reg`
   from `/dev/mem` (whole rows of `stride` bytes; mapping the region and copying
   rows works where unaligned reads do not). It holds the aura logo on black
   and no text. With the splash enabled, the same read shows the splash's last
   simpledrm frame; on a J616s the logo's visible pixels span
   (1621, 1006)-(1834, 1227), the same pixels as aura's 256 px logo at
   (1600, 989).

A camera at 30 fps or more catches what logs cannot: a blank is a run of
black frames between two pictures, while a brightness change is a smooth ramp.
