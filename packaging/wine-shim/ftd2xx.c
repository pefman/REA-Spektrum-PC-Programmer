/*
 * ftd2xx.dll stand-in for Wine.
 *
 * The Windows "Spektrum Programmer" talks to its programming cable
 * through FTDI's proprietary D2XX API.  Wine cannot provide that (it
 * needs ftdibus.sys, the FTDI kernel driver), so this DLL implements
 * the handful of D2XX entry points the application imports and forwards
 * every call to a helper process ("spmlinux.d2xx_bridge") over a local
 * TCP socket.  The helper does the real USB work with libusb and logs
 * every frame, which makes the app a perfect oracle for the AS3X
 * protocol.
 *
 * The application imports FTD2XX.dll *by ordinal*, so the exports below
 * use the ordinals of the DLL that ships with the application
 * (nsis_extract/ftd2xx.dll).
 *
 * Build:  make            (needs mingw-w64-gcc)
 * Use:    WINEDLLOVERRIDES="ftd2xx.dll=n" wine SpmProgrammer.exe
 *         with  python3 -m spmlinux.d2xx_bridge  running
 */
#include <winsock2.h>
#include <windows.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

typedef int FT_STATUS;

#pragma comment(lib, "ws2_32.lib")

#define BRIDGE_HOST "127.0.0.1"
#define BRIDGE_PORT 47531

/* ---------------------------------------------------------------- bridge */

static SOCKET g_sock = INVALID_SOCKET;
static HANDLE g_event = NULL;
static HANDLE g_thread = NULL;
static volatile LONG g_running = 1;

static int bridge_connect(void)
{
    WSADATA wsa;
    struct sockaddr_in addr;
    int one = 1;
    if (g_sock != INVALID_SOCKET)
        return 0;
    if (WSAStartup(MAKEWORD(2, 2), &wsa))
        return -1;
    g_sock = socket(AF_INET, SOCK_STREAM, 0);
    if (g_sock == INVALID_SOCKET)
        return -1;
    setsockopt(g_sock, IPPROTO_TCP, TCP_NODELAY, (const char *)&one, sizeof(one));
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(BRIDGE_PORT);
    addr.sin_addr.s_addr = inet_addr(BRIDGE_HOST);
    if (connect(g_sock, (struct sockaddr *)&addr, sizeof(addr))) {
        closesocket(g_sock);
        g_sock = INVALID_SOCKET;
        return -1;
    }
    return 0;
}

/* request: [u32 opcode][u32 length][payload]  ->  [u32 status][payload] */
static int bridge_call(uint32_t opcode, const void *req, uint32_t reqlen,
                       void *resp, uint32_t resplen, uint32_t *resp_len)
{
    uint8_t hdr[8];
    uint32_t i;
    if (bridge_connect())
        return -1;
    hdr[0] = (uint8_t)(opcode & 0xff);
    hdr[1] = (uint8_t)((opcode >> 8) & 0xff);
    hdr[2] = (uint8_t)((opcode >> 16) & 0xff);
    hdr[3] = (uint8_t)((opcode >> 24) & 0xff);
    hdr[4] = (uint8_t)(reqlen & 0xff);
    hdr[5] = (uint8_t)((reqlen >> 8) & 0xff);
    hdr[6] = (uint8_t)((reqlen >> 16) & 0xff);
    hdr[7] = (uint8_t)((reqlen >> 24) & 0xff);
    if (send(g_sock, (const char *)hdr, 8, 0) != 8)
        goto fail;
    if (reqlen && send(g_sock, (const char *)req, (int)reqlen, 0) != (int)reqlen)
        goto fail;
    if (recv(g_sock, (char *)hdr, 8, MSG_WAITALL) != 8)
        goto fail;
    {
        uint32_t status = (uint32_t)hdr[0] | ((uint32_t)hdr[1] << 8) |
                         ((uint32_t)hdr[2] << 16) | ((uint32_t)hdr[3] << 24);
        uint32_t len = (uint32_t)hdr[4] | ((uint32_t)hdr[5] << 8) |
                       ((uint32_t)hdr[6] << 16) | ((uint32_t)hdr[7] << 24);
        if (resp_len)
            *resp_len = len;
        if (len > resplen)
            len = resplen;
        for (i = 0; i < len; ) {
            int n = recv(g_sock, (char *)resp + i, (int)(len - i), MSG_WAITALL);
            if (n <= 0)
                goto fail;
            i += (uint32_t)n;
        }
        return (int)status;
    }
fail:
    closesocket(g_sock);
    g_sock = INVALID_SOCKET;
    return -1;
}

/* ------------------------------------------------------- reader thread */

/* The application waits on a Windows event before reading, so the
 * bridge has to signal it when data arrives. */
static DWORD WINAPI reader_thread(LPVOID param)
{
    (void)param;
    while (g_running) {
        uint8_t hdr[8];
        uint32_t opcode, len, i;
        if (bridge_connect())
        {
            Sleep(50);
            continue;
        }
        /* ask for pending data */
        hdr[0] = 0xFE; hdr[1] = 0; hdr[2] = 0; hdr[3] = 0;
        hdr[4] = 0; hdr[5] = 0; hdr[6] = 0; hdr[7] = 0;
        if (send(g_sock, (const char *)hdr, 8, 0) != 8) {
            closesocket(g_sock); g_sock = INVALID_SOCKET; continue;
        }
        if (recv(g_sock, (char *)hdr, 8, MSG_WAITALL) != 8) {
            closesocket(g_sock); g_sock = INVALID_SOCKET; continue;
        }
        opcode = (uint32_t)hdr[0] | ((uint32_t)hdr[1] << 8) |
                 ((uint32_t)hdr[2] << 16) | ((uint32_t)hdr[3] << 24);
        len = (uint32_t)hdr[4] | ((uint32_t)hdr[5] << 8) |
              ((uint32_t)hdr[6] << 16) | ((uint32_t)hdr[7] << 24);
        if (opcode == 0xFFFFFFFF) {   /* no data */
            Sleep(20);
            continue;
        }
        for (i = 0; i < len; ) {
            uint8_t sink[4096];
            int n = recv(g_sock, (char *)sink, (int)(len - i), MSG_WAITALL);
            if (n <= 0) break;
            i += (uint32_t)n;
        }
        if (g_event)
            SetEvent(g_event);
    }
    return 0;
}

static void ensure_thread(void)
{
    if (g_thread)
        return;
    g_event = CreateEvent(NULL, FALSE, FALSE, NULL);
    g_thread = CreateThread(NULL, 0, reader_thread, NULL, 0, NULL);
}

/* ------------------------------------------------------------- exports */

#define FT_OK 0
#define FT_DEVICE_NOT_FOUND 3

/* the app passes the index in pDeviceInfo when opening by index */
typedef struct {
    uint32_t flags;
    uint32_t type;
    uint32_t id;
    uint32_t locId;
    char serialNumber[16];
    char description[128];
    void *ftHandle;
} FT_DEVICE_INFO;

__declspec(dllexport) FT_STATUS WINAPI FT_CreateDeviceInfoList(void)
{
    uint32_t n = 0;
    if (bridge_call(0x01, NULL, 0, &n, sizeof(n), NULL) != 0)
        return 0;
    return n;
}

__declspec(dllexport) FT_STATUS WINAPI FT_GetDeviceInfoList(FT_DEVICE_INFO *list, uint32_t count)
{
    uint32_t i;
    for (i = 0; i < count; i++) {
        uint8_t buf[4 + 4 + 4 + 4 + 16 + 128];
        uint32_t len = 0;
        if (bridge_call(0x02, &i, sizeof(i), buf, sizeof(buf), &len) != 0)
            return FT_DEVICE_NOT_FOUND;
        if (len >= sizeof(buf))
            continue;
        memcpy(&list[i], buf, len);
    }
    return FT_OK;
}

__declspec(dllexport) FT_STATUS WINAPI FT_OpenEx(void **handle, uint32_t flags, void *info)
{
    uint32_t idx = 0;
    uint8_t resp[4];
    uint32_t len = 0;
    uint32_t slot = 0xFFFFFFFF;
    if (info) {
        if (flags == 1) { /* open by index: pDeviceInfo points at the index */
            memcpy(&idx, info, sizeof(idx));
        } else {
            /* open by description: the app passes a string */
            const char *desc = (const char *)info;
            uint32_t n = FT_CreateDeviceInfoList();
            uint32_t i;
            FT_DEVICE_INFO *di = (FT_DEVICE_INFO *)calloc(n ? n : 1, sizeof(FT_DEVICE_INFO));
            if (!di)
                return FT_DEVICE_NOT_FOUND;
            FT_GetDeviceInfoList(di, n);
            for (i = 0; i < n; i++) {
                if (desc[0] == '\0' || strcmp(di[i].description, desc) == 0) {
                    idx = i;
                    break;
                }
            }
            free(di);
        }
    }
    if (bridge_call(0x03, &idx, sizeof(idx), resp, sizeof(resp), &len) != 0)
        return FT_DEVICE_NOT_FOUND;
    memcpy(&slot, resp, len < 4 ? len : 4);
    *handle = (void *)(uintptr_t)slot;
    ensure_thread();
    return FT_OK;
}

__declspec(dllexport) FT_STATUS WINAPI FT_Close(void *handle)
{
    uint32_t slot = (uint32_t)(uintptr_t)handle;
    bridge_call(0x04, &slot, sizeof(slot), NULL, 0, NULL);
    return FT_OK;
}

__declspec(dllexport) FT_STATUS WINAPI FT_Write(void *handle, const void *buf, uint32_t len, uint32_t *written)
{
    uint32_t slot = (uint32_t)(uintptr_t)handle;
    uint8_t req[4 + 4096];
    uint32_t out = 0, rlen = 0;
    if (len > 4096) len = 4096;
    memcpy(req, &slot, 4);
    memcpy(req + 4, buf, len);
    if (bridge_call(0x05, req, 4 + len, req, sizeof(req), &rlen) != 0)
        return 1;
    if (rlen >= 4)
        memcpy(&out, req, 4);
    if (written)
        *written = out;
    return FT_OK;
}

__declspec(dllexport) FT_STATUS WINAPI FT_Read(void *handle, void *buf, uint32_t len, uint32_t *read)
{
    uint32_t slot = (uint32_t)(uintptr_t)handle;
    uint8_t req[8];
    uint8_t *resp = (uint8_t *)malloc(len + 4);
    uint32_t rlen = 0, got = 0;
    if (!resp)
        return 1;
    memcpy(req, &slot, 4);
    memcpy(req + 4, &len, 4);
    if (bridge_call(0x06, req, 8, resp, len + 4, &rlen) != 0) {
        free(resp);
        return 1;
    }
    if (rlen >= 4) {
        memcpy(&got, resp, 4);
        if (got > len) got = len;
        memcpy(buf, resp + 4, got);
    }
    free(resp);
    if (read)
        *read = got;
    return FT_OK;
}

__declspec(dllexport) FT_STATUS WINAPI FT_GetQueueStatus(void *handle, uint32_t *status)
{
    uint32_t slot = (uint32_t)(uintptr_t)handle;
    uint32_t rlen = 0, val = 0;
    if (bridge_call(0x07, &slot, sizeof(slot), &val, sizeof(val), &rlen) != 0)
        return 1;
    if (status)
        *status = val;
    return FT_OK;
}

/* simple pass-through for the configuration calls */
static FT_STATUS cfg_call(uint32_t opcode, void *handle, uint32_t a, uint32_t b)
{
    uint32_t req[4], resp[4];
    uint32_t rlen = 0;
    req[0] = (uint32_t)(uintptr_t)handle;
    req[1] = a;
    req[2] = b;
    req[3] = 0;
    return bridge_call(opcode, req, sizeof(req), resp, sizeof(resp), &rlen) == 0 ? FT_OK : 1;
}

__declspec(dllexport) FT_STATUS WINAPI FT_SetBaudRate(void *handle, uint32_t baud)
{ return cfg_call(0x08, handle, baud, 0); }

__declspec(dllexport) FT_STATUS WINAPI FT_SetDataCharacteristics(void *handle, uint32_t bits,
                                                               uint32_t parity, uint32_t stop)
{ return cfg_call(0x09, handle, bits | (parity << 8) | (stop << 11), 0); }

__declspec(dllexport) FT_STATUS WINAPI FT_SetFlowControl(void *handle, uint32_t type,
                                                        uint32_t xon, uint32_t xoff)
{ return cfg_call(0x0A, handle, type, xon | (xoff << 16)); }

__declspec(dllexport) FT_STATUS WINAPI FT_SetLatencyTimer(void *handle, uint32_t ms)
{ return cfg_call(0x0B, handle, ms, 0); }

__declspec(dllexport) FT_STATUS WINAPI FT_SetBitMode(void *handle, uint8_t mode, uint8_t mask)
{ return cfg_call(0x0C, handle, mode, mask); }

/* the app asks for read notifications; the helper thread does the work */
__declspec(dllexport) FT_STATUS WINAPI FT_SetEventNotification(void *handle, uint32_t mask)
{
    ensure_thread();
    return FT_OK;
}

__declspec(dllexport) FT_STATUS WINAPI FT_GetDeviceInfo(void *handle, void *info)
{
    return FT_OK;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)inst; (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        g_running = 1;
        DisableThreadLibraryCalls(inst);
    } else if (reason == DLL_PROCESS_DETACH) {
        g_running = 0;
    }
    return TRUE;
}
