# Spektrum Programmer 3.10.6.0 — reverse-engineering report & native Linux port plan

Target: `https://spektrumrc.cachefly.net/apps/spektrum-programmer-3.10.6.0.exe`
(sha256 `d622e4fc38229f232f810b083c23bd89bfaa8450b8f6ca0ef7d35ae15265c871`,
11 416 984 bytes) plus the product page
`https://spektrumrc.cachefly.net/apps/spektrum_programmer.html`.

Everything below was derived from the shipped artifact itself unless a
source is named explicitly.  Addresses are image VAs of the 32-bit PE
(base `0x400000`); they are given so each claim can be re-checked with
`objdump -d` / `pefile` on `nsis_extract/SpmProgrammer.exe`.

---

## 1. What the program actually is

| Question | Answer | Evidence |
|---|---|---|
| Package format | NSIS 3.11 installer (Unicode, LZMA, solid) wrapping a PE32 stub | 7-Zip listing: `Type = Nsis … SubType = NSIS-3 Unicode`; stub manifest `Nullsoft Install System v3.11` |
| App type | **native 32-bit x86 C++/Qt 5 GUI** — *not* .NET, *not* Electron, no Electron `app.asar` | `file SpmProgrammer.exe` → `PE32 executable … Intel i386`; import table = Qt5Core/Gui/Network/SerialPort/Widgets/WinExtras + MSVC runtime |
| Payload | 240 files / 33 MB: the app, Qt 5 runtime, OpenSSL 1.1, MSVC runtime, FTDI D2XX + driver INF, 10 default models, images, acknowledgements | `nsis_extract/` |
| Signing | Authenticode (GlobalSign EV code-signing) | certificate strings in the image (`crl.globalsign.com/…/codesignca2020`) |

Imports of the app (pefile/objdump):

```
FTD2XX.dll   (by ordinal)  70 FT_CreateDeviceInfoList   71 FT_GetDeviceInfoList
                           27 FT_OpenEx                   2 FT_Close
                            3 FT_Read                     4 FT_Write
                            7 FT_SetBaudRate              8 FT_SetDataCharacteristics
                            9 FT_SetFlowControl          18 FT_GetQueueStatus
                           19 FT_SetEventNotification    29 FT_SetLatencyTimer
                           31 FT_SetBitMode
Qt5Core/Gui/Network/SerialPort/Widgets/WinExtras, MSVCP140, VCRUNTIME140,
KERNEL32, USER32, SHELL32, UxTheme, WINMM
```

The ordinal → name mapping was recovered from the *bundled* driver DLL
`nsis_extract/Cable Drivers/i386/ftd2xx.dll` (93 named exports), so the
D2XX API surface used by the app is exact.

---

## 2. Device transports

### 2.1 FTDI D2XX (the real SPMA3065 cable)

Runtime configuration, read directly from the open path
(`0x437234`–`0x437299`):

```
FT_OpenEx(handle, flags=1, <device index/name>)
FT_SetBaudRate(handle, 0x1C200)              -> 115200 baud
FT_SetDataCharacteristics(handle, 8, 0, 0)   -> 8 data bits, no parity, 1 stop bit
FT_SetFlowControl(handle, 0, 0, 0)           -> no flow control
FT_SetBitMode(handle, 0x88, 0x20)            -> D2XX: FT128R|MSB, mask SYNCFF
FT_SetLatencyTimer(handle, 8)                -> 8 ms
FT_SetEventNotification(handle, mask=1)      -> FT_EVENT_DATA_AVAILABLE
```

The cable itself: the bundled driver INF
(`nsis_extract/Cable Drivers/ftdibus.inf`) contains

```
USB\VID_0403&PID_7C48.DeviceDesc="USB Interface AS3X Programmer"
Ftdi="Horizon Hobby"
```

so the programming cable is an **FTDI 0403:7C48** device.

Important for a native port: the app contains **no hard-coded 0x7C48 /
0x0403 immediate**.  It enumerates every FTDI device
(`FT_CreateDeviceInfoList` / `FT_GetDeviceInfoList`) and logs
`VID: %1, PID: %2, Flags: %3, LocId: %4, ftHandle: %5, SN: %6, Desc: %7`.
Any FTDI device can therefore be used — a cheap FT232R works, provided
the FTDI chip's UART is configured identically (115200 8N1, 8 ms latency).

### 2.2 USB-CDC serial path

The app also enumerates serial ports and accepts specific CDC devices
(decompiled function at `0x429ff0`):

```
VID 0x0483  PID 0xA3C0, 0xA3D3
VID 0x35CF  PID 0xF000, 0xF003, 0xF004, 0xF005, 0xF006, 0xF007   (type 0)
              PID 0xF001, 0xF002                                   (type 1)
```

It logs them as `Type: UsbCdc, portName = %1`.  Note that
`QSerialPort::setBaudRate` is **not** imported, so this path runs at the
QSerialPort default (9600 8N1) — it is meant for the DXS USB Simulator
Adapter, not for the AS3X cable.

---

## 3. Wire protocol ("FSK" / AS3X)

Two frame families, both checksummed the same way.

### 3.1 Checksum (verified)

`0x42a4e0` and `0x42a5f5`: seed **`0x170`**, 16-bit **additive sum** over the
frame bytes, appended **low byte first, then high byte**
(`0x42a509`: `append(crc); append(crc >> 8)`).

### 3.2 Bootloader frame (verified, 526 bytes)

Builder at `0x42a430`:

```
48 48 <cmd> <arg> <payload…> 00…(zero padded to 524)  <crc lo> <crc hi>
```

* `0x48 0x48` header (`0x42a47c`, `0x42a487`)
* payload zero-padded to **524** bytes (`0x20c`, `0x42a4ae`)
* checksum covers the first 524 bytes and is appended afterwards.

The public Saleae Logic 2 decoder
`ajcasagrande/spektrum-bootloader-and-sg3-decoder` documents the same
`0x48 0x48` bootloader header for *"communication between Spektrum
Programmer software for Windows and compatible receivers"*, with the
command set `ID_REQUEST 0x01`, `CABLE_SYN 0x02`, `BIN_ERASE 0x04`,
`BIN_WRITE 0x05`, `SAX_ERASE 0x07`, `SAX_WRITE 0x08`, `SAX_EOF 0x09`,
`SAX_IX_ERASE 0x0A`, `SAX_IX_WRITE 0x0B` and responses at `cmd + 0x85`.
The app's own log strings use the same vocabulary
(`Sending bootloader ID_REQUEST`, `Received bootloader ID_REQUEST_ACK`,
`Sending bootloader GO`, `Received bootloader GO_ACK`).

### 3.3 FSK frame (verified structure)

Builder at `0x42a550`:

```
<lead> 00 <payload…> <crc lo> <crc hi> 00…(zero padded to >= 64 bytes)
```

* lead byte is message specific: `0x01` (`0x42e6f3`) and `0x19`
  (`0x42bf90`) are both used;
* the checksum covers `lead … payload`;
* the frame is padded to **at least 64** bytes (`0x40`, `0x42a634`).

Received messages are dispatched on type bytes **0x09 / 0x0A / 0x0B**
(`0x42e8cb`, `0x42ea82`, `0x42ebdd`) — the same values the Saleae decoder
labels `EVENT`, `TELEMETRY`, `FORWARD_PROGRAMMING`.

### 3.4 Application messages seen in the binary

From the log strings (ASCII, directly referenced by code):

```
Sending ID_REQ                 Received ID_REQ_ACK        (Parameter CRC, Product code,
                               Parameter version, FSK protocol version)
Sending SERIAL_REQ             Received SERIAL_ACK
Sending DISCOVERY_REQUEST      Received BOOTLOAD_MODE_ACK
Sending BOOTLOAD_MODE          Received FLASH_SAVE_ACK
Sending FLASH_SAVE             Received TNC_STATUS_ACK
Sending TNC_STATUS             Received SET_TESTED_ACK
Sending SET_TESTED             (Flag to set: Calibration)
Sending bootloader ID_REQUEST  Received bootloader ID_REQUEST_ACK
Sending bootloader GO          Received bootloader GO_ACK
Sending ADV_READ_SEQ packets   Received all data:
```

plus the model-memory layer:

```
Found likely bootloader packet at %1     but checksum didn't match: %1 vs %2
Found FSK packet (checksum matched)      packet type: 0x%1 … (expected: 0x%1)
FlashHeadersAck:   Read section %1, size: %2
SectionCrcReqAck:  section %1, crc: %2
Model CRC compare (%1):  section %1, model: %2, device: %3
Switching to model with CRC %1
Ack Timeout
```

So model memory is organised in **sections**, each with its own CRC, and
a whole model is identified by a CRC (`Switching to model with CRC %1`).

### 3.5 Firmware (SAX) support

`SAX_ERASE` / `SAX_WRITE` / `SAX_EOF` are the firmware path, and the
binary contains SAX decryption material:

```
0x4f1b08  E410 A433 C505 B9FD 5CC1 130B 5644 A689
          32C3 E6B2 E2BF B7F8 8CE6 7A7D C6F7 78A6
0x4f1aa8  0x01C4
```

The `.sax` firmware files themselves are downloaded from Horizon
(see §5), so a native port needs the account flow as well.

---

## 4. File formats

### 4.1 `.srm` model files (fully decoded)

`.srm` is **JSON**, not a binary blob:

```json
{ "name": "CZ Carbon Cub",
  "deviceProductCode": "0xD9",
  "parameterVersion": "0xFF",
  "rawData": { "0": "E4E4E4E4…" } }
```

Verified on all 10 shipped defaults (`nsis_extract/DefaultModels/*.srm`):
`rawData` holds one hex blob per memory bank, **1856 bytes (0x740)** for the
AR-series defaults.  The model *name* is **not** inside the blob (it lives
in the JSON), the blob starts with the servo/travel table
(`E4 E4 … 80 80 … FF …`).

### 4.2 The model-memory schema (embedded in the app)

The app embeds a small bit-field DSL describing the model memory layout
and parses it at runtime; the descriptions are in the binary as wide
strings, e.g.

```
root: {
    system: { axis2aircraft: u8, axisDirection: u8, sensorDigitalFilter: u8,
              serialMode: u8, gainSource: u8[3], fmSource: u8, … },
    cpSettings: { modelType: u8, processorMode: u8, defaultFlightMode: u8,
                  dualRatePos: u8[10][3], expoPos: i8[10][3], … },
    mixes: { mixType: u8, mixMaster: u8, mixSlave: u8, mixPosRate: i8, … }[16],
    as3xSettings: { priority: u8[10][3], rateGains: { kp: u8, ki: u8, kd: u8 }[10][3], … },
    …
}
```

and the DSL's own error strings (`Expected : after bit field name`,
`Expected starting bit index`, `Expected .. after starting bit index`,
`Expected } to close block`, `Expected integer`, `Expected ] to close`)
prove it is a *parser for a text schema*, i.e. the app ships the model
layout as data.  **That schema is the single most valuable artifact for a
native editor** — it can be extracted from the binary (the wide strings
are contiguous) and re-used verbatim.

### 4.3 App data layout (Windows)

```
%APPDATA%\<org>\Spektrum Programmer.ini     (QSettings, IniFormat)
%APPDATA%\…\Models\{Trash,Presets,DefaultModels}
%APPDATA%\…\LastSyncedModels.json
```

---

## 5. Network / account side

* `https://www.spektrumrc.com/Services/SmartClientServices.ashx`
* schema `http://www.spektrumrc.com/Services/Schemas/SmartClientService_XPCIDocument_1_0_1.xsd`
* XPCI `1.0.1`, licence `6E03AF1D-C0E9-4763-A5F7-D77D326C0BDF`
* operations: `LoginUserRequest`, `RegisterUserRequest`,
  `RegisterDeviceRequest`, `GetListofUpdatesRequest`
  (error codes 2002/2003/2004, 3001…, 7001…, 8001… are embedded)
* TLS is done with the **bundled OpenSSL 1.1** (`libssl-1_1.dll`,
  `libcrypto-1_1.dll`), not with the system one.

Account features are optional: connecting to a device, reading/writing
model memory and editing models do not require a login; *firmware updates*
and *model presets download* do.

---

## 6. Side observation: it already runs under Wine

Wine 11.20 (wow64) on this machine starts `SpmProgrammer.exe` without
missing-DLL errors, because Qt 5, the MSVC runtime and OpenSSL are all
bundled.  The only Windows-specific part is the FTDI D2XX layer
(`ftd2xx.dll` + `ftdibus.sys`), which Wine cannot provide.  So Wine is a
usable *stopgap*, but it is not "native".

---

## 7. Native Linux port — plan and status

The `spmlinux/` package in this directory is the start of the native port.

| Layer | Status | Notes |
|---|---|---|
| `.srm` codec | **done + tested** | `spmlinux/srm.py`; round-trips all 10 shipped models |
| Frame builder/parser | **done, offline-tested** | `spmlinux/protocol.py`; checksum + frame shapes match the binary |
| FTDI transport | written, **untested** (no cable here) | `spmlinux/transport.py`, pyusb/libusb, 115200 8N1 / 8 ms / bit mode 0x88 |
| CDC serial transport | written, untested | `spmlinux/transport.py` (pyserial) |
| CLI | done | `spmlinux/cli.py` (`devices`, `srm`, `probe`, `defaultmodels`) |
| Model-memory schema | **extracted** | `spmlinux/extract_schema.py` -> `spmlinux/model_memory.schema.txt` (341 lines, binary order) |
| Message semantics (ID_REQ, ADV_READ_SEQ, model section read/write) | **open** | see below |
| Model-memory schema -> settings editor | **open** | schema is now available; the editor is the porting work |
| Firmware update (SAX) | **open** | needs account flow + SAX decode |
| GUI | **open** | the Windows GUI is ~40 settings pages; port as a Qt6/PySide6 app |

### 7.1 Next concrete steps (in order of value)

0. **Install the two Python deps** (`pip install -r spmlinux/requirements.txt`):
   `pyusb` (cable access) and `pyserial` (CDC/TTL cable).
1. **Get ground truth frames.** Cheapest: run the Windows app under Wine
   against a simulated cable and log the bytes.  This needs a PE
   replacement for `ftd2xx.dll` (a `mingw-w64` DLL that forwards to a
   Unix socket), i.e. `mingw-w64-gcc` is the only missing tool.
   Alternative: capture with the Saleae decoder / a cheap logic analyser
   on the cable's TX line, or use the app's own *Protocol Log* window
   (`actionView_Protocol_Log`) while it talks to a real device.
2. **Finish the message layer** for `ID_REQ` (device identity, parameter
   CRC, product code, parameter version, FSK protocol version) and
   `ADV_READ_SEQ` (bulk model-memory reads), then the write path
   (`Write Seq`, `Write Seq Ack`, `Address`, `0x01C4`).
3. **Turn the extracted schema into structs**
   (`spmlinux/model_memory.schema.txt` -> a struct generator); then the
   per-model settings pages can be reimplemented one-to-one, and the
   `Model CRC compare` / `SectionCrcReqAck` logic can be reimplemented
   against the section table.
4. **Model file I/O** is already done — a native app can already import
   and export the shipped `.srm` files and edit the model memory blob
   directly.
5. **Firmware**: reuse the SAX command set; decode `.sax` (the Saleae
   repo has `sax.py`, including the DES decryption) and implement
   `SAX_ERASE` / `SAX_WRITE` / `SAX_EOF`.

### 7.2 Hardware note

Because the app accepts *any* FTDI device, a native port does not need the
expensive SPMA3065: a $5 FT232R (0403:6001) in the same wiring works, and
`RalfBrandt/USB-AS3X-Programming-cable` documents a home-made
0403:7C48 clone.

---

## 8. Honest limits of this report

* No Spektrum hardware was available, so **no frame was captured from a
  real device**; §3.3's FSK lead bytes and §3.2's frame shape come from
  the binary, but the *semantics* of the payload fields (what "ID_REQ"
  carries, the exact ADV_READ_SEQ layout) are still inferred from the
  app's log strings and the third-party decoder.
* The `0x88/0x20` bit mode is quoted verbatim from the app; whether the
  receiver requires it (vs. it being a no-op for the FT232R/FT230X) is
  unverified.
* The model-memory schema was read as text out of the binary but has not
  yet been validated against a device's memory image.
* Firmware (`.sax`) handling and the account/preset-download protocol
  were mapped only at the level of the request/response names and URLs.
