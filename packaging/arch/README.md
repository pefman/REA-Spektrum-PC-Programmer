# Arch Linux packaging

Two packages:

| Package | What it is | State |
|---|---|---|
| `spmlinux` | the **native** Linux port (CLI): AS3X/FSK protocol, `.srm` model files, cable transport | alpha - protocol layer not yet verified against hardware |
| `spektrum-programmer` | Horizon's official **Windows** app running under Wine, with the FTDI driver bits removed | works as a stop gap; the USB cable needs a D2XX stand-in |

## Build & install

```bash
cd packaging/arch

# the Wine package needs the Windows payload; extract it once:
#   7z x ../../spektrum-programmer-3.10.6.0.exe -o../../nsis_extract
make build     # native package  (makepkg -si)
make wine      # Wine package    (makepkg -si)
```

`make dist` rebuilds the source tarballs the PKGBUILDs consume; `make all`
does both packages.

The native package pulls in `python-pyusb`, `python-pyserial` and `libusb`
from the Arch repos; the Wine package pulls in `wine` (which is already
wow64 capable, so the 32-bit app runs).

## Using the native package

```bash
spm devices                    # list FTDI cables and serial ports
spm srm model.srm --dump       # inspect a .srm model file
spm defaultmodels ~/models     # convert the models shipped with the app
spm probe                      # talk to a real cable
```

## Using the Wine package

```bash
spektrum-programmer            # first run creates a wine prefix
```

The launcher forces `QT_OPENGL=software`; without it the 32-bit Qt5 build
crashes with `0xc0000409` during start-up on current Wine.

For the programming cable you need a D2XX stand-in for Wine
(`wineftd2xx`) plus FTDI's Linux D2XX library — see the package README.

## Licensing note

The `spektrum-programmer` package embeds Horizon Hobby's proprietary
Windows application.  It is built locally from the vendor's own
installer and must not be redistributed; the `spmlinux` package contains
only our own re-implementation.
