# spmlinux — native Linux port of the Spektrum Programmer (work in progress)

This is the Linux-native re-implementation of the device-facing parts of
Horizon's *Spektrum Programmer* 3.10.6.0 (Windows).  It is **not** a port
of the GUI yet; it is the protocol/format layer that the GUI would sit
on.  See `../REPORT.md` for the reverse-engineering evidence behind every
constant in here.

```
spmlinux/
  srm.py         .srm model file codec (JSON container + hex memory blob)
  protocol.py    FSK / bootloader framing, CRC16(0x170) checksum, parser
  transport.py   FTDI (pyusb/libusb) and CDC-serial transports
  cli.py         command line front end
  extract_schema.py  pulls the model-memory schema out of SpmProgrammer.exe
  model_memory.schema.txt  the extracted schema (binary order)
  defaultmodels/ the 10 models shipped with the Windows app
  tests/         offline self-tests (no hardware needed)
```

## Status

| Piece | State |
|---|---|
| `.srm` read/write | complete, tested against the shipped models |
| Frame build/parse | complete, tested offline (shapes match the binary) |
| Model-memory schema | extracted from the binary |
| FTDI / serial transport | written, **not yet tested against hardware** |
| Device handshake, model memory read/write | open — needs captured frames |
| Model settings editor | open — schema is available |
| Firmware update (`.sax`) | open |

## Usage

```bash
pip install -r spmlinux/requirements.txt    # pyusb, pyserial

python3 -m spmlinux.cli devices            # list FTDI cables and ttys
python3 -m spmlinux.cli srm model.srm --dump
python3 -m spmlinux.cli defaultmodels out/  # convert the shipped defaults
python3 -m spmlinux.cli probe              # talk to a real cable
python3 -m spmlinux.extract_schema nsis_extract/SpmProgrammer.exe out.txt
python3 -m spmlinux.tests.test_offline     # self-test
```

## Requirements

* Python 3.10+
* `libusb-1.0` (for the FTDI cable) and/or `pyserial` (for a CDC/TTL cable)
* the Spektrum programming cable (FTDI 0403:7c48) — or any FTDI device,
  the Windows app does not check the PID.

## What still needs doing

See `../REPORT.md` §7.1 — the short version: capture real frames
(easiest: run the Windows app under Wine with a `ftd2xx.dll` stand-in that
logs the bytes, or sniff the cable with a logic analyser), then implement
`ID_REQ` / `ADV_READ_SEQ` / model-section read-write on top of the framing
that is already here.
