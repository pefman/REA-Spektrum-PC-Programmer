# REA — Spektrum PC Programmer (native Linux)

Reverse engineering of **Spektrum Programmer 3.10.6.0** (Horizon Hobby,
Windows) and a native Linux re-implementation of its device-facing parts.

![status](https://img.shields.io/badge/status-alpha-yellow) ![license](https://img.shields.io/badge/license-GPL--3.0-blue)

## What is in here

| Path | Contents |
|---|---|
| [`REPORT.md`](REPORT.md) | the reverse-engineering report: every finding with the binary address it came from |
| [`spmlinux/`](spmlinux/) | the native Linux implementation (Python): protocol layer, `.srm` codec, cable transports, CLI |
| [`packaging/arch/`](packaging/arch/) | Arch Linux PKGBUILDs for the native CLI and for the official app under Wine |
| `pyproject.toml` | standard Python packaging, so `pip install .` also works |

## Quick facts (see REPORT.md for the evidence)

* The Windows app is a **native 32-bit x86 C++/Qt 5** program (not .NET,
  not Electron) shipped inside an NSIS installer.
* It talks to the programming cable through **FTDI's D2XX v1 API**
  (`ftd2xx.dll`, imported by ordinal): 115200 8N1, no flow control, 8 ms
  latency, `FT_SetBitMode(0x88, 0x20)`.
* The cable is an FTDI **0403:7C48** ("USB Interface AS3X Programmer"),
  but the app does **not** check the PID — any FTDI device works.
* Wire format: two frame families, both with a 16-bit additive checksum
  seeded with **0x170**:
  * bootloader frames `48 48 <cmd> <arg> …` padded to 524 bytes + CRC,
  * FSK frames `<lead> 00 <payload> <crc> …` padded to ≥ 64 bytes.
* `.srm` model files are **JSON** (`name`, `deviceProductCode`,
  `parameterVersion`, `rawData`), wrapping a 1856-byte model-memory image.
* The app embeds the **model-memory layout as a bit-field DSL**; it is
  extracted into `spmlinux/model_memory.schema.txt`.

## Native CLI

```bash
pip install -r spmlinux/requirements.txt   # pyusb, pyserial

spm devices                    # list FTDI cables and serial ports
spm srm model.srm --dump       # inspect a .srm model file
spm defaultmodels out/         # convert the models shipped with the app
spm probe                      # talk to a real cable
```

On Arch:

```bash
cd packaging/arch && make build   # builds + installs the `spmlinux` package
```

## What still needs work

* The device handshake (`ID_REQ`), the bulk model-memory transfer
  (`ADV_READ_SEQ`) and the model write path are implemented only at the
  framing level — they need real frames from a device to be finished.
  The cheapest way to get those frames is to run the Windows app under
  Wine against a logging `ftd2xx.dll` stand-in, or to sniff the cable.
* The model-memory schema is extracted but not yet turned into structs.
* No GUI yet; the Windows app has ~40 settings pages.

## License

GPL-3.0-or-later for our own code (see [LICENSE](LICENSE)).  The vendor's
Windows application is **not** redistributed here —
`packaging/arch/spektrum-programmer` builds it locally from the vendor's
own installer for personal use.  The ten default `.srm` model files under
`spmlinux/defaultmodels/` come from the vendor's installer and are kept
here so the test-suite and the Arch package have sample data.
