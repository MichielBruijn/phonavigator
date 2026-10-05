"""Windows: serve the axes on a named pipe for TDxNavLib.dll (windows/navlib).

Windows has no spacenavd; instead our own TDxNavLib.dll is installed where 3D applications
look for 3Dconnexion's navigation library, and reads the axes from this pipe. Values go out
the way spacenavd would send them for a virtual SpaceMouse Compact, so the tray settings and
the dll's axis defaults behave as on Linux.
"""

import ctypes
import struct
import threading
import time
from ctypes import wintypes

from . import Backend, BackendError, spnavrc

PIPE = r"\\.\pipe\phonavigator"
_PIPE_ACCESS_OUTBOUND = 0x2
_PIPE_TYPE_BYTE = 0x0
_PIPE_WAIT = 0x0
_PIPE_REJECT_REMOTE_CLIENTS = 0x8
_PIPE_UNLIMITED_INSTANCES = 255
_ERROR_PIPE_CONNECTED = 535
_INVALID = ctypes.c_void_p(-1).value

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateNamedPipeW.restype = wintypes.HANDLE
_k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
_k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
_k32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]

# raw axis i -> (spacenavd output axis, sign), as for a 3Dconnexion device without spnavrc
_CHAIN = spnavrc._chain(spnavrc.parse(""))


class PipeBackend(Backend):

    def __init__(self):
        self._cond = threading.Condition()
        self._packet = self._pack([0] * 6, 0)
        self._gen = 0
        self._clients = 0
        self.last = [0] * 6
        self._last_time = time.monotonic()
        handle = self._new_pipe()  # fail early, e.g. a second tray
        threading.Thread(target=self._serve, args=(handle,), name="pipe", daemon=True).start()

    @property
    def name(self):
        return f"TDxNavLib pipe, {self._clients} application(s) connected"

    @staticmethod
    def _pack(out, period_ms):
        return struct.pack("<8i", 0, *out, period_ms)

    def _new_pipe(self):
        h = _k32.CreateNamedPipeW(PIPE, _PIPE_ACCESS_OUTBOUND,
                                  _PIPE_TYPE_BYTE | _PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS,
                                  _PIPE_UNLIMITED_INSTANCES, 4096, 0, 0, None)
        if h == _INVALID or h is None:
            raise BackendError(f"Cannot create {PIPE} (error {ctypes.get_last_error()})")
        return h

    def _serve(self, handle):
        while True:
            ok = _k32.ConnectNamedPipe(handle, None) or ctypes.get_last_error() == _ERROR_PIPE_CONNECTED
            if ok:
                threading.Thread(target=self._client, args=(handle,), daemon=True).start()
            else:
                _k32.CloseHandle(handle)
            while True:
                try:
                    handle = self._new_pipe()
                    break
                except BackendError:
                    time.sleep(1)

    def _client(self, handle):
        """One application: always send the newest state; slow readers just skip states."""
        with self._cond:
            self._clients += 1
            gen, packet = self._gen, self._packet
        written = wintypes.DWORD()
        try:
            while _k32.WriteFile(handle, packet, len(packet), ctypes.byref(written), None):
                with self._cond:
                    self._cond.wait_for(lambda: self._gen != gen)
                    gen, packet = self._gen, self._packet
        finally:
            _k32.CloseHandle(handle)
            with self._cond:
                self._clients -= 1

    def write(self, values):
        out = [0] * 6
        for raw, (axis, sign) in zip(values, _CHAIN):
            out[axis] = sign * raw
        if out == self.last:
            return  # the dll keeps moving at the last rate until it gets new values
        now = time.monotonic()
        period = int((now - self._last_time) * 1000)
        self._last_time, self.last = now, out
        with self._cond:
            self._packet = self._pack(out, period)
            self._gen += 1
            self._cond.notify_all()

    def close(self):
        self.write([0] * 6)
