"""Confined local-NTFS operations for the Windows transaction backend.

This module is an explicit adapter, never a monkeypatch of :mod:`os`. Native
directory handles implement descriptor-relative operations using NtCreateFile
and FILE_RENAME_INFO.RootDirectory. Each path component is opened without
following reparse points. Files use real binary CRT descriptors; directories
use positive adapter-owned descriptors.

Windows does not expose an unprivileged directory fsync. File content is
flushed; directory ``fsync`` is explicitly a no-op. This backend supports
process-crash recovery, not a POSIX directory-durability promise after power
loss. It deliberately supports local NTFS only.
"""

from __future__ import annotations

import contextlib
import errno
import os as _os
import stat as _stat
import sys
import threading
from types import SimpleNamespace

if _os.name != "nt":  # Importable by cross-platform package inspection.
    os_proxy = _os
else:
    import ctypes as C
    import msvcrt
    from ctypes import wintypes as W

    _k = C.WinDLL("kernel32", use_last_error=True)
    _a = C.WinDLL("advapi32", use_last_error=True)
    _n = C.WinDLL("ntdll")
    _HANDLE = W.HANDLE
    _ULONG_PTR = C.c_size_t
    _INVALID = C.c_void_p(-1).value
    _READ_CONTROL = 0x20000
    _WRITE_DAC = 0x40000
    _DELETE = 0x10000
    _SYNCHRONIZE = 0x100000
    _FILE_READ_ATTRIBUTES = 0x80
    _FILE_WRITE_ATTRIBUTES = 0x100
    _FILE_ATTRIBUTE_DIRECTORY = 0x10
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    _FILE_OPEN_REPARSE_POINT = 0x200000
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x20
    _FILE_DIRECTORY_FILE = 1
    _FILE_NON_DIRECTORY_FILE = 0x40
    _FILE_WRITE_THROUGH = 2
    _OBJ_CASE_INSENSITIVE = 0x40
    _SE_FILE_OBJECT = 1
    _SECURITY_INFORMATION = 7  # owner, group, discretionary ACL
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000

    class _UNICODE_STRING(C.Structure):
        _fields_ = [("Length", W.USHORT), ("MaximumLength", W.USHORT),
                    ("Buffer", W.LPWSTR)]

    class _OBJECT_ATTRIBUTES(C.Structure):
        _fields_ = [("Length", W.ULONG), ("RootDirectory", _HANDLE),
                    ("ObjectName", C.POINTER(_UNICODE_STRING)),
                    ("Attributes", W.ULONG), ("SecurityDescriptor", C.c_void_p),
                    ("SecurityQualityOfService", C.c_void_p)]

    class _IO_STATUS_BLOCK(C.Structure):
        _fields_ = [("Status", C.c_void_p), ("Information", _ULONG_PTR)]

    class _FILETIME(C.Structure):
        _fields_ = [("low", W.DWORD), ("high", W.DWORD)]

    class _BY_HANDLE_FILE_INFORMATION(C.Structure):
        _fields_ = [
            ("attributes", W.DWORD), ("creation", _FILETIME),
            ("access", _FILETIME), ("write", _FILETIME),
            ("volume", W.DWORD), ("size_high", W.DWORD),
            ("size_low", W.DWORD), ("links", W.DWORD),
            ("index_high", W.DWORD), ("index_low", W.DWORD),
        ]

    class _FILE_ID_INFO(C.Structure):
        _fields_ = [("volume", C.c_ulonglong), ("identifier", C.c_ubyte * 16)]

    class _SECURITY_ATTRIBUTES(C.Structure):
        _fields_ = [("nLength", W.DWORD), ("lpSecurityDescriptor", C.c_void_p),
                    ("bInheritHandle", W.BOOL)]

    class _TOKEN_USER(C.Structure):
        _fields_ = [("Sid", C.c_void_p), ("Attributes", W.DWORD)]

    class _FILE_RENAME_INFO(C.Structure):
        # The leading BOOLEAN is a DWORD-sized union on current Windows.
        _fields_ = [("Flags", W.DWORD), ("RootDirectory", _HANDLE),
                    ("FileNameLength", W.DWORD), ("FileName", W.WCHAR * 1)]

    def _proto(lib, name, restype, *args):
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = restype, args
        return fn

    _NtCreateFile = _proto(
        _n, "NtCreateFile", W.LONG, C.POINTER(_HANDLE), W.DWORD,
        C.POINTER(_OBJECT_ATTRIBUTES), C.POINTER(_IO_STATUS_BLOCK),
        C.c_void_p, W.ULONG, W.ULONG, W.ULONG, W.ULONG, C.c_void_p, W.ULONG,
    )
    _nt_error = _proto(_n, "RtlNtStatusToDosError", W.ULONG, W.LONG)
    _NtSetInformationFile = _proto(
        _n, "NtSetInformationFile", W.LONG, _HANDLE,
        C.POINTER(_IO_STATUS_BLOCK), C.c_void_p, W.ULONG, C.c_int)
    _NtSetSecurityObject = _proto(
        _n, "NtSetSecurityObject", W.LONG, _HANDLE, W.DWORD, C.c_void_p)
    _CloseHandle = _proto(_k, "CloseHandle", W.BOOL, _HANDLE)
    _GetFileInfo = _proto(_k, "GetFileInformationByHandle", W.BOOL, _HANDLE,
                         C.POINTER(_BY_HANDLE_FILE_INFORMATION))
    _GetFileInfoEx = _proto(_k, "GetFileInformationByHandleEx", W.BOOL,
                           _HANDLE, C.c_int, C.c_void_p, W.DWORD)
    _SetFileInfo = _proto(_k, "SetFileInformationByHandle", W.BOOL,
                         _HANDLE, C.c_int, C.c_void_p, W.DWORD)
    _DuplicateHandle = _proto(_k, "DuplicateHandle", W.BOOL, _HANDLE, _HANDLE,
                             _HANDLE, C.POINTER(_HANDLE), W.DWORD, W.BOOL, W.DWORD)
    _GetCurrentProcess = _proto(_k, "GetCurrentProcess", _HANDLE)
    _GetDriveType = _proto(_k, "GetDriveTypeW", W.UINT, W.LPCWSTR)
    _GetVolumeInfo = _proto(
        _k, "GetVolumeInformationByHandleW", W.BOOL, _HANDLE, W.LPWSTR, W.DWORD,
        C.POINTER(W.DWORD), C.POINTER(W.DWORD), C.POINTER(W.DWORD),
        W.LPWSTR, W.DWORD,
    )
    _GetSecurityInfo = _proto(
        _a, "GetSecurityInfo", W.DWORD, _HANDLE, C.c_int, W.DWORD,
        C.POINTER(C.c_void_p), C.POINTER(C.c_void_p), C.POINTER(C.c_void_p),
        C.POINTER(C.c_void_p), C.POINTER(C.c_void_p),
    )
    _SetSecurityInfo = _proto(
        _a, "SetSecurityInfo", W.DWORD, _HANDLE, C.c_int, W.DWORD,
        C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p,
    )
    _SDToString = _proto(
        _a, "ConvertSecurityDescriptorToStringSecurityDescriptorW", W.BOOL,
        C.c_void_p, W.DWORD, W.DWORD, C.POINTER(W.LPWSTR), C.POINTER(W.ULONG),
    )
    _StringToSD = _proto(
        _a, "ConvertStringSecurityDescriptorToSecurityDescriptorW", W.BOOL,
        W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.POINTER(W.ULONG),
    )
    _GetSDDacl = _proto(
        _a, "GetSecurityDescriptorDacl", W.BOOL, C.c_void_p, C.POINTER(W.BOOL),
        C.POINTER(C.c_void_p), C.POINTER(W.BOOL),
    )
    _GetSDControl = _proto(
        _a, "GetSecurityDescriptorControl", W.BOOL, C.c_void_p,
        C.POINTER(W.USHORT), C.POINTER(W.DWORD),
    )
    _GetSDOwner = _proto(
        _a, "GetSecurityDescriptorOwner", W.BOOL, C.c_void_p,
        C.POINTER(C.c_void_p), C.POINTER(W.BOOL),
    )
    _GetSDGroup = _proto(
        _a, "GetSecurityDescriptorGroup", W.BOOL, C.c_void_p,
        C.POINTER(C.c_void_p), C.POINTER(W.BOOL),
    )
    _GetAce = _proto(_a, "GetAce", W.BOOL, C.c_void_p, W.DWORD,
                    C.POINTER(C.c_void_p))
    _InitializeAcl = _proto(_a, "InitializeAcl", W.BOOL, C.c_void_p,
                           W.DWORD, W.DWORD)
    _AddAce = _proto(_a, "AddAce", W.BOOL, C.c_void_p, W.DWORD,
                    W.DWORD, C.c_void_p, W.DWORD)
    _OpenProcessToken = _proto(
        _a, "OpenProcessToken", W.BOOL, _HANDLE, W.DWORD, C.POINTER(_HANDLE),
    )
    _GetTokenInformation = _proto(
        _a, "GetTokenInformation", W.BOOL, _HANDLE, C.c_int,
        C.c_void_p, W.DWORD, C.POINTER(W.DWORD),
    )
    _SidToString = _proto(
        _a, "ConvertSidToStringSidW", W.BOOL, C.c_void_p, C.POINTER(W.LPWSTR),
    )
    _LocalFree = _proto(_k, "LocalFree", C.c_void_p, C.c_void_p)
    _CreateMutex = _proto(
        _k, "CreateMutexW", _HANDLE, C.POINTER(_SECURITY_ATTRIBUTES),
        W.BOOL, W.LPCWSTR,
    )
    _WaitForSingleObject = _proto(
        _k, "WaitForSingleObject", W.DWORD, _HANDLE, W.DWORD,
    )
    _ReleaseMutex = _proto(_k, "ReleaseMutex", W.BOOL, _HANDLE)
    _OpenProcess = _proto(_k, "OpenProcess", _HANDLE, W.DWORD, W.BOOL, W.DWORD)
    _GetExitCodeProcess = _proto(
        _k, "GetExitCodeProcess", W.BOOL, _HANDLE, C.POINTER(W.DWORD))

    def _winerror(code=None, path=None):
        error = C.WinError(C.get_last_error() if code is None else code)
        if path is not None:
            error.filename = _os.fspath(path)
        return error

    def _checked(ok):
        if not ok:
            raise _winerror()

    @contextlib.contextmanager
    def _security_descriptor(sddl):
        if not isinstance(sddl, str) or len(sddl) > 65536 or "\x00" in sddl:
            raise OSError(errno.EINVAL, "invalid or oversized security descriptor")
        pointer = C.c_void_p()
        _checked(_StringToSD(sddl, 1, C.byref(pointer), None))
        try:
            yield pointer
        finally:
            _LocalFree(pointer)

    def _canonical_sddl(text):
        # For a protected regular-file DACL, AI is bookkeeping: P disables
        # inheritance and the file has no children. Windows' two security
        # setters disagree on retaining AI. Preserve every ACE and all other
        # control bits; never strip AI from an unprotected DACL.
        start = text.find("D:")
        if start >= 0:
            end = text.find("(", start)
            if end < 0:
                end = len(text)
            flags = text[start + 2:end]
            if "P" in flags:
                text = text[:start + 2] + flags.replace("AI", "") + text[end:]
        return text

    def _sid_string(sid):
        """Numeric SID identity, independent of SDDL aliases such as BA/SY."""
        if not sid:
            raise OSError(errno.EINVAL, "security descriptor lacks owner or group")
        text = W.LPWSTR()
        _checked(_SidToString(sid, C.byref(text)))
        try:
            return text.value
        finally:
            _LocalFree(C.cast(text, C.c_void_p))

    def _descriptor_identity(descriptor):
        owner, group, defaulted = C.c_void_p(), C.c_void_p(), W.BOOL()
        _checked(_GetSDOwner(descriptor, C.byref(owner), C.byref(defaulted)))
        _checked(_GetSDGroup(descriptor, C.byref(group), C.byref(defaulted)))
        return _sid_string(owner), _sid_string(group)

    def _descriptor_sddl(descriptor, information=_SECURITY_INFORMATION):
        text = W.LPWSTR()
        _checked(_SDToString(descriptor, 1, information, C.byref(text), None))
        try:
            return _canonical_sddl(text.value)
        finally:
            _LocalFree(C.cast(text, C.c_void_p))

    def _sddl_for_handle(handle, information=_SECURITY_INFORMATION):
        descriptor = C.c_void_p()
        result = _GetSecurityInfo(handle, _SE_FILE_OBJECT, information,
                                  None, None, None, None, C.byref(descriptor))
        if result:
            raise _winerror(result)
        string = W.LPWSTR()
        try:
            _checked(_SDToString(descriptor, 1, information, C.byref(string), None))
            try:
                text = string.value
                if text is None or len(text) > 65536:
                    raise OSError(errno.EOVERFLOW, "oversized security descriptor")
                metadata = _BY_HANDLE_FILE_INFORMATION()
                _checked(_GetFileInfo(handle, C.byref(metadata)))
                return (text if metadata.attributes & _FILE_ATTRIBUTE_DIRECTORY
                        else _canonical_sddl(text))
            finally:
                _LocalFree(C.cast(string, C.c_void_p))
        finally:
            _LocalFree(descriptor)

    def _token_sid(kind):
        token = _HANDLE()
        _checked(_OpenProcessToken(_GetCurrentProcess(), 8, C.byref(token)))
        try:
            size = W.DWORD()
            _GetTokenInformation(token, kind, None, 0, C.byref(size))
            buffer = C.create_string_buffer(size.value)
            _checked(_GetTokenInformation(token, kind, buffer, len(buffer), C.byref(size)))
            user = C.cast(buffer, C.POINTER(_TOKEN_USER)).contents
            text = W.LPWSTR()
            _checked(_SidToString(user.Sid, C.byref(text)))
            try:
                return text.value
            finally:
                _LocalFree(C.cast(text, C.c_void_p))
        finally:
            _CloseHandle(token)

    def _validate_component(name):
        if (not name or name in {".", ".."} or "\x00" in name
                or any(char in name for char in '\\/:<>|?*"')
                or name.endswith((" ", "."))):
            raise OSError(errno.EINVAL, "unsafe native path component", name)
        base = name.split(".", 1)[0].upper()
        if base in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or (
            len(base) == 4 and base[:3] in {"COM", "LPT"}
            and base[3] in "123456789¹²³"
        ):
            raise OSError(errno.EINVAL, "reserved Windows path component", name)
        return name

    def _native_open(name, root=None, *, access=None, disposition=1,
                     directory=None, security=None, write_through=False):
        if access is None:
            access = _FILE_READ_ATTRIBUTES | _READ_CONTROL | _SYNCHRONIZE
        string_buffer = C.create_unicode_buffer(name)
        byte_length = len(name.encode("utf-16-le"))
        if byte_length > 65532:
            raise OSError(errno.ENAMETOOLONG, "native path is too long", name)
        name_string = _UNICODE_STRING(byte_length, byte_length + 2,
                                      C.cast(string_buffer, W.LPWSTR))
        attributes = _OBJECT_ATTRIBUTES(
            C.sizeof(_OBJECT_ATTRIBUTES), root, C.pointer(name_string),
            _OBJ_CASE_INSENSITIVE, security, None,
        )
        options = _FILE_OPEN_REPARSE_POINT | _FILE_SYNCHRONOUS_IO_NONALERT
        if directory is True:
            options |= _FILE_DIRECTORY_FILE
        elif directory is False:
            options |= _FILE_NON_DIRECTORY_FILE
        if write_through:
            options |= _FILE_WRITE_THROUGH
        handle, iosb = _HANDLE(), _IO_STATUS_BLOCK()
        status = _NtCreateFile(
            C.byref(handle), access, C.byref(attributes), C.byref(iosb),
            None, 0x80, 7, disposition, options, None, 0,
        )
        if status < 0:
            raise _winerror(_nt_error(status), name)
        try:
            info = _BY_HANDLE_FILE_INFORMATION()
            _checked(_GetFileInfo(handle, C.byref(info)))
            if info.attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise OSError(errno.ELOOP, "reparse points are not supported", name)
            if not info.volume or not (info.index_high or info.index_low):
                raise OSError(errno.ENOTSUP, "filesystem lacks stable identity", name)
            return handle.value
        except BaseException:
            _CloseHandle(handle)
            raise

    class _Entry:
        def __init__(self, adapter, parent_fd, name):
            self._adapter, self._parent_fd, self.name = adapter, parent_fd, name
            self.path = name

        def stat(self, *, follow_symlinks=True):
            return self._adapter.stat(
                self.name, dir_fd=self._parent_fd, follow_symlinks=follow_symlinks)

        def is_dir(self, *, follow_symlinks=True):
            return _stat.S_ISDIR(self.stat(follow_symlinks=follow_symlinks).st_mode)

        def is_file(self, *, follow_symlinks=True):
            return _stat.S_ISREG(self.stat(follow_symlinks=follow_symlinks).st_mode)

        def is_symlink(self):
            # Reparse leaves are rejected rather than followed.
            try:
                self.stat(follow_symlinks=False)
                return False
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    return True
                raise

        def inode(self):
            return self.stat(follow_symlinks=False).st_ino

        def __fspath__(self):
            return self.path

    class _Scandir:
        def __init__(self, adapter, fd, names):
            self._adapter, self._fd = adapter, fd
            self._iterator = iter(names)

        def __iter__(self):
            return self

        def __next__(self):
            if self._fd is None:
                raise StopIteration
            try:
                return _Entry(self._adapter, self._fd, next(self._iterator))
            except StopIteration:
                self.close()
                raise

        def close(self):
            if self._fd is not None:
                self._adapter.close(self._fd)
                self._fd = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

        def __del__(self):
            self.close()

    class WindowsFS:
        """Small os-compatible surface scoped to confined transaction code."""

        native_confined = True
        O_DIRECTORY = 0x10000000
        O_NOFOLLOW = 0x20000000
        O_CLOEXEC = 0x40000000
        O_NONBLOCK = 0x08000000

        def __init__(self):
            self._directories = {}
            self._next_fd = 1 << 30
            self._guard = threading.RLock()
            self._mutexes = {}
            self._held_identities = set()
            self._sid = _token_sid(1)
            # TokenOwner is the default owner assigned to newly created
            # objects. Elevated tokens may use Administrators here while
            # TokenUser remains the individual account. Preserve the actual
            # creator ownership; the private DACL still grants TokenUser.
            self._owner_sid = _token_sid(4)
            self._group_sid = _token_sid(5)
            identity = f"O:{self._owner_sid}G:{self._group_sid}"
            private_dacl = f"D:P(A;;FA;;;SY)(A;;FA;;;{self._sid})"
            # Windows renders well-known token identities with aliases. Use
            # the same canonical serialization for defaults, live readback,
            # approval hashes and journal recovery.
            with _security_descriptor(identity + private_dacl) as descriptor:
                self._identity_sddl = _descriptor_sddl(descriptor, 3)
                self._private_sddl = _descriptor_sddl(descriptor)
            self._private_dacl = self._private_sddl[self._private_sddl.index("D:"):]
            self.supports_dir_fd = {
                self.open, self.stat, self.mkdir, self.rename, self.replace,
                self.unlink, self.rmdir,
            }
            self.supports_fd = {self.stat, self.listdir, self.scandir}
            self.supports_follow_symlinks = {self.stat}

        def __getattr__(self, name):
            return getattr(_os, name)

        def private_security(self):
            return self._private_sddl

        def validate_security(self, sddl):
            """Validate a restorable descriptor without mutating a file."""
            if not isinstance(sddl, str) or len(sddl.encode("utf-8")) > 65536:
                raise OSError(errno.EINVAL, "invalid or oversized security descriptor")
            with _security_descriptor(sddl) as descriptor:
                present, defaulted, dacl = W.BOOL(), W.BOOL(), C.c_void_p()
                _checked(_GetSDDacl(descriptor, C.byref(present), C.byref(dacl),
                                    C.byref(defaulted)))
                if not present or not dacl:
                    raise OSError(errno.EACCES, "null or absent DACL is unsupported")
                if _descriptor_identity(descriptor) != (self._owner_sid, self._group_sid):
                    raise OSError(
                        errno.ENOTSUP,
                        "native transactions require token-default ownership and primary group")
                return _descriptor_sddl(descriptor)

        def kill(self, pid, signal):
            # os.kill(pid, 0) TERMINATES a process on Windows. Emulate only the
            # POSIX liveness probe; never send a signal in this branch.
            if signal != 0:
                return _os.kill(pid, signal)
            if not isinstance(pid, int) or pid <= 0:
                raise ProcessLookupError(errno.ESRCH, "invalid process identifier")
            handle = _OpenProcess(0x1000, False, pid)
            if not handle:
                code = C.get_last_error()
                if code == 87:
                    raise ProcessLookupError(errno.ESRCH, "process does not exist")
                raise _winerror(code)
            try:
                code = W.DWORD()
                _checked(_GetExitCodeProcess(handle, C.byref(code)))
                if code.value != 259:
                    raise ProcessLookupError(errno.ESRCH, "process has exited")
            finally:
                _CloseHandle(handle)

        def _handle(self, fd):
            with self._guard:
                if fd in self._directories:
                    return self._directories[fd]
            return msvcrt.get_osfhandle(fd)

        def _directory(self, fd):
            with self._guard:
                try:
                    handle = self._directories[fd]
                except KeyError:
                    raise OSError(errno.EBADF, "not an adapter directory descriptor")
            information = _BY_HANDLE_FILE_INFORMATION()
            _checked(_GetFileInfo(handle, C.byref(information)))
            if information.attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise OSError(errno.ELOOP, "retained directory became a reparse point")
            return handle

        def _register(self, handle):
            with self._guard:
                descriptor = self._next_fd
                self._next_fd += 1
                self._directories[descriptor] = handle
                return descriptor

        def _anchor(self, anchor):
            # Reject remote volumes and aliases before any runtime mutation.
            if len(anchor) != 3 or anchor[1:] != ":\\" or _GetDriveType(anchor) != 3:
                raise OSError(errno.ENOTSUP, "native transactions require a local fixed NTFS volume")
            handle = _native_open("\\??\\" + anchor, directory=True,
                                  access=1 | _FILE_READ_ATTRIBUTES | _READ_CONTROL | _SYNCHRONIZE)
            try:
                filesystem = C.create_unicode_buffer(32)
                serial, maximum, flags = W.DWORD(), W.DWORD(), W.DWORD()
                _checked(_GetVolumeInfo(
                    handle, None, 0, C.byref(serial), C.byref(maximum),
                    C.byref(flags), filesystem, len(filesystem)))
                if filesystem.value != "NTFS" or not flags.value & 8:
                    raise OSError(errno.ENOTSUP, "native transactions require NTFS persistent ACLs")
                return handle
            except BaseException:
                _CloseHandle(handle)
                raise

        @contextlib.contextmanager
        def _parent(self, path, dir_fd=None):
            raw = _os.fsdecode(path)
            if "\x00" in raw or raw.startswith(("\\\\", "//", "\\??\\")):
                raise OSError(errno.EINVAL, "UNC, device and NUL paths are unsupported")
            raw = raw.replace("/", "\\")
            drive, rest = _os.path.splitdrive(raw)
            handles = []
            if drive:
                if dir_fd is not None or not rest.startswith("\\"):
                    raise OSError(errno.EINVAL, "ambiguous drive-relative path")
                root = self._anchor(drive.upper() + "\\")
                handles.append(root)
                parts = rest.strip("\\").split("\\") if rest.strip("\\") else []
            elif dir_fd is not None:
                if raw.startswith("\\"):
                    raise OSError(errno.EINVAL, "absolute name with directory descriptor")
                root = self._directory(dir_fd)
                parts = raw.split("\\")
            else:
                absolute = _os.path.abspath(raw)
                # Do not normalize explicit parent components into an escape.
                if ".." in raw.split("\\"):
                    raise OSError(errno.EINVAL, "parent traversal is unsupported")
                with self._parent(absolute) as value:
                    yield value
                return
            try:
                if not parts or parts == ["."]:
                    yield root, None
                    return
                for component in parts:
                    _validate_component(component)
                for component in parts[:-1]:
                    root = _native_open(
                        component, root, directory=True,
                        access=1 | _FILE_READ_ATTRIBUTES | _READ_CONTROL | _SYNCHRONIZE,
                    )
                    handles.append(root)
                yield root, parts[-1]
            finally:
                for handle in reversed(handles):
                    _CloseHandle(handle)

        def _duplicate_handle(self, handle):
            duplicate = _HANDLE()
            process = _GetCurrentProcess()
            _checked(_DuplicateHandle(
                process, handle, process, C.byref(duplicate), 0, False, 2))
            return duplicate.value

        def open(self, path, flags, mode=0o777, *, dir_fd=None):
            directory = bool(flags & self.O_DIRECTORY)
            writing = bool(flags & (_os.O_WRONLY | _os.O_RDWR))
            creating = bool(flags & _os.O_CREAT)
            access = _FILE_READ_ATTRIBUTES | _READ_CONTROL | _SYNCHRONIZE
            if directory:
                access |= 1  # FILE_LIST_DIRECTORY
            else:
                if not flags & _os.O_WRONLY:
                    access |= 1  # FILE_READ_DATA
                if writing:
                    access |= 2 | 4 | _WRITE_DAC  # data, append, DACL
            disposition = 2 if creating and flags & _os.O_EXCL else (3 if creating else 1)
            security = self.private_security() if creating and mode & 0o077 == 0 else None
            with self._parent(path, dir_fd) as (parent, name):
                if name is None:
                    if not directory or creating or writing:
                        raise OSError(errno.EISDIR, "operation requires a file name")
                    return self._register(self._duplicate_handle(parent))
                with (_security_descriptor(security) if security else contextlib.nullcontext(None)) as sd:
                    handle = _native_open(
                        name, parent, access=access, disposition=disposition,
                        directory=directory, security=sd,
                        write_through=writing,
                    )
            if directory:
                return self._register(handle)
            try:
                crt_flags = _os.O_BINARY | (
                    _os.O_RDWR if flags & _os.O_RDWR else
                    _os.O_WRONLY if flags & _os.O_WRONLY else _os.O_RDONLY
                )
                if flags & _os.O_APPEND:
                    crt_flags |= _os.O_APPEND
                fd = msvcrt.open_osfhandle(handle, crt_flags)
            except BaseException:
                _CloseHandle(handle)
                raise
            try:
                _os.set_inheritable(fd, False)
                if flags & _os.O_TRUNC:
                    _os.ftruncate(fd, 0)
                return fd
            except BaseException:
                _os.close(fd)
                raise

        def close(self, fd):
            with self._guard:
                handle = self._directories.pop(fd, None)
            if handle is None:
                _os.close(fd)
            else:
                self.release_lock(fd)
                _checked(_CloseHandle(handle))

        def dup(self, fd):
            with self._guard:
                handle = self._directories.get(fd)
            if handle is None:
                return _os.dup(fd)
            return self._register(self._duplicate_handle(handle))

        def get_security(self, fd):
            handle = self._handle(fd)
            self._assert_supported_file(handle)
            return _sddl_for_handle(handle)

        def _assert_supported_file(self, handle, information=None):
            """Refuse storage metadata a byte-only replacement cannot preserve."""
            info = information or _BY_HANDLE_FILE_INFORMATION()
            if information is None:
                _checked(_GetFileInfo(handle, C.byref(info)))
            if info.attributes & _FILE_ATTRIBUTE_DIRECTORY:
                return
            if info.attributes & (0x4000 | 1):  # EFS encryption / read-only
                raise OSError(
                    errno.ENOTSUP,
                    "native transactions do not replace encrypted or read-only files")
            # FileStreamInfo enumerates through the retained handle. Bound the
            # query; refuse an oversized stream list rather than allocating an
            # attacker-controlled amount or silently ignoring hidden streams.
            buffer = C.create_string_buffer(65536)
            if not _GetFileInfoEx(handle, 7, buffer, len(buffer)):
                code = C.get_last_error()
                if code in {122, 234}:
                    raise OSError(errno.ENOTSUP, "NTFS stream metadata is oversized")
                raise _winerror(code)
            offset = 0
            while True:
                next_offset = W.DWORD.from_buffer(buffer, offset).value
                name_bytes = W.DWORD.from_buffer(buffer, offset + 4).value
                if name_bytes % 2 or name_bytes > len(buffer) - offset - 24:
                    raise OSError(errno.EIO, "invalid NTFS stream metadata")
                name = C.wstring_at(C.addressof(buffer) + offset + 24, name_bytes // 2)
                if name != "::$DATA":
                    raise OSError(
                        errno.ENOTSUP, "native transactions do not replace named NTFS streams")
                if not next_offset:
                    return
                if next_offset < 24 or offset + next_offset >= len(buffer):
                    raise OSError(errno.EIO, "invalid NTFS stream offset")
                offset += next_offset

        def set_security(self, fd, sddl):
            handle = self._handle(fd)
            info = _BY_HANDLE_FILE_INFORMATION()
            _checked(_GetFileInfo(handle, C.byref(info)))
            if info.links != 1:
                raise OSError(errno.EMLINK, "ACL changes on hardlinked files are unsupported")
            sddl = self.validate_security(sddl)
            with _security_descriptor(sddl) as descriptor:
                # Never pretend to restore foreign ownership through a DACL.
                if "O:" in sddl or "G:" in sddl:
                    requested = W.LPWSTR()
                    _checked(_SDToString(descriptor, 1, 3, C.byref(requested), None))
                    try:
                        if requested.value != _sddl_for_handle(handle, 3):
                            raise OSError(errno.ENOTSUP, "restoring different Windows ownership is unsupported")
                    finally:
                        _LocalFree(C.cast(requested, C.c_void_p))
                present, defaulted, dacl = W.BOOL(), W.BOOL(), C.c_void_p()
                _checked(_GetSDDacl(descriptor, C.byref(present), C.byref(dacl),
                                    C.byref(defaulted)))
                if not present or not dacl:
                    raise OSError(errno.EACCES, "null or absent DACL is unsupported")
                control, revision = W.USHORT(), W.DWORD()
                _checked(_GetSDControl(descriptor, C.byref(control), C.byref(revision)))
                protection = (_PROTECTED_DACL_SECURITY_INFORMATION if control.value & 0x1000
                              else _UNPROTECTED_DACL_SECURITY_INFORMATION)
                if control.value & 0x1000 or not control.value & 0x400:
                    # A protected DACL is applied verbatim. Without AI, native
                    # semantics also retain the exact non-auto-inherited ACL.
                    status = _NtSetSecurityObject(handle, 4 | protection, descriptor)
                    if status < 0:
                        raise _winerror(_nt_error(status))
                else:
                    # SetSecurityInfo computes automatic inheritance. Supplying
                    # the snapshot's inherited ACEs would duplicate them. Keep
                    # only its explicit ACEs; the temporary file shares the
                    # original's directory, and exact readback below proves the
                    # inherited ACL was reconstructed without policy drift.
                    revision = C.c_ubyte.from_address(dacl.value).value
                    size = W.WORD.from_address(dacl.value + 2).value
                    count = W.WORD.from_address(dacl.value + 4).value
                    explicit = C.create_string_buffer(size)
                    _checked(_InitializeAcl(explicit, size, revision))
                    for index in range(count):
                        ace = C.c_void_p()
                        _checked(_GetAce(dacl, index, C.byref(ace)))
                        flags = C.c_ubyte.from_address(ace.value + 1).value
                        ace_size = W.WORD.from_address(ace.value + 2).value
                        if not flags & 0x10:
                            _checked(_AddAce(explicit, revision, 0xFFFFFFFF, ace, ace_size))
                    result = _SetSecurityInfo(
                        handle, _SE_FILE_OBJECT, 4 | protection,
                        None, None, explicit, None)
                    if result:
                        raise _winerror(result)
                if _sddl_for_handle(handle) != sddl:
                    raise OSError(errno.ENOTSUP, "Windows could not restore the exact captured security descriptor")

        def get_security_at(self, parent_fd, name):
            descriptor = self.open(name, _os.O_RDONLY, dir_fd=parent_fd)
            try:
                return self.get_security(descriptor)
            finally:
                self.close(descriptor)

        def fchmod(self, fd, mode):
            if mode & 0o077 == 0:
                self.set_security(fd, self.private_security())
            # Public mode bits cannot represent Windows ACLs. Existing ACLs are
            # restored separately by the transaction's security snapshot.

        def chmod(self, path, mode, *, dir_fd=None, follow_symlinks=True):
            with self._parent(path, dir_fd) as (parent, name):
                if name is None:
                    raise OSError(errno.ENOTSUP, "changing volume-root ACL is unsupported")
                handle = _native_open(
                    name, parent, access=_READ_CONTROL | _WRITE_DAC
                    | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE)
                descriptor = self._register(handle)
                try:
                    self.fchmod(descriptor, mode)
                finally:
                    self.close(descriptor)

        def _metadata(self, handle):
            info = _BY_HANDLE_FILE_INFORMATION()
            _checked(_GetFileInfo(handle, C.byref(info)))
            if info.attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise OSError(errno.ELOOP, "reparse points are not supported")
            self._assert_supported_file(handle, info)
            directory = bool(info.attributes & _FILE_ATTRIBUTE_DIRECTORY)
            private = _sddl_for_handle(handle, 4) == self._private_dacl
            mode = (_stat.S_IFDIR | (0o700 if private else 0o755)) if directory else (
                _stat.S_IFREG | (0o600 if private else 0o644))
            def time_ns(value):
                return (((value.high << 32) | value.low) - 116444736000000000) * 100
            volume, identity = self._file_identity(handle)
            # Match pathlib/os.stat for the active interpreter. CPython3.12
            # widened Windows st_dev and st_ino to FILE_ID_INFO values.
            if sys.version_info < (3, 12):
                volume, identity = info.volume, (info.index_high << 32) | info.index_low
            return SimpleNamespace(
                st_mode=mode, st_ino=identity,
                st_dev=volume, st_nlink=info.links, st_uid=0, st_gid=0,
                st_size=(info.size_high << 32) | info.size_low,
                st_atime_ns=time_ns(info.access), st_atime=time_ns(info.access) / 1e9,
                st_mtime_ns=time_ns(info.write), st_mtime=time_ns(info.write) / 1e9,
                st_ctime_ns=time_ns(info.creation), st_ctime=time_ns(info.creation) / 1e9,
                st_file_attributes=info.attributes, st_reparse_tag=0,
            )

        def fstat(self, fd):
            return self._metadata(self._handle(fd))

        def _file_identity(self, handle):
            information = _FILE_ID_INFO()
            _checked(_GetFileInfoEx(handle, 18, C.byref(information), C.sizeof(information)))
            identity = int.from_bytes(bytes(information.identifier), "little")
            if not information.volume or not identity:
                raise OSError(errno.ENOTSUP, "filesystem lacks stable identity")
            return information.volume, identity

        def file_identity(self, fd):
            """Interpreter-independent physical identity, including mutex keys."""
            return self._file_identity(self._handle(fd))

        def stat(self, path, *, dir_fd=None, follow_symlinks=True):
            if isinstance(path, int):
                return self.fstat(path)
            with self._parent(path, dir_fd) as (parent, name):
                if name is None:
                    return self._metadata(parent)
                handle = _native_open(name, parent)
                try:
                    return self._metadata(handle)
                finally:
                    _CloseHandle(handle)

        def lstat(self, path, *, dir_fd=None):
            return self.stat(path, dir_fd=dir_fd, follow_symlinks=False)

        def mkdir(self, path, mode=0o777, *, dir_fd=None):
            security = self.private_security() if mode & 0o077 == 0 else None
            with self._parent(path, dir_fd) as (parent, name):
                if name is None:
                    raise FileExistsError(errno.EEXIST, "directory already exists")
                with (_security_descriptor(security) if security else contextlib.nullcontext(None)) as sd:
                    handle = _native_open(name, parent, directory=True, disposition=2,
                                          security=sd)
                    _CloseHandle(handle)

        def fsync(self, fd):
            with self._guard:
                if fd in self._directories:
                    return  # Documented Windows metadata durability limitation.
            return _os.fsync(fd)

        def _names(self, handle):
            buffer = C.create_string_buffer(65536)
            first = True
            while True:
                if not _GetFileInfoEx(handle, 11 if first else 10, buffer, len(buffer)):
                    code = C.get_last_error()
                    if code == 18:  # ERROR_NO_MORE_FILES
                        return
                    raise _winerror(code)
                first = False
                offset = 0
                while True:
                    next_offset = W.DWORD.from_buffer(buffer, offset).value
                    name_bytes = W.DWORD.from_buffer(buffer, offset + 60).value
                    if name_bytes > len(buffer) - offset - 104 or name_bytes % 2:
                        raise OSError(errno.EIO, "invalid native directory enumeration")
                    name = C.wstring_at(C.addressof(buffer) + offset + 104, name_bytes // 2)
                    if name not in {".", ".."}:
                        yield name
                    if not next_offset:
                        break
                    if next_offset < 104 or offset + next_offset >= len(buffer):
                        raise OSError(errno.EIO, "invalid native directory offset")
                    offset += next_offset

        def listdir(self, path="."):
            owned = not isinstance(path, int)
            fd = self.open(path, self.O_DIRECTORY) if owned else path
            try:
                return list(self._names(self._directory(fd)))
            finally:
                if owned:
                    self.close(fd)

        def scandir(self, path="."):
            fd = self.dup(path) if isinstance(path, int) else self.open(path, self.O_DIRECTORY)
            try:
                return _Scandir(self, fd, self._names(self._directory(fd)))
            except BaseException:
                self.close(fd)
                raise

        def rename(self, src, dst, *, src_dir_fd=None, dst_dir_fd=None):
            with self._parent(src, src_dir_fd) as (source_parent, source):
                with self._parent(dst, dst_dir_fd) as (target_parent, target):
                    if source is None or target is None:
                        raise OSError(errno.EINVAL, "renaming volume root is unsupported")
                    handle = _native_open(
                        source, source_parent, access=_DELETE | _SYNCHRONIZE
                        | _READ_CONTROL | _FILE_READ_ATTRIBUTES)
                    try:
                        # Windows will replace a reparse object, not follow it,
                        # but fail closed rather than overwriting any such leaf.
                        try:
                            target_handle = _native_open(target, target_parent)
                        except FileNotFoundError:
                            pass
                        else:
                            try:
                                self._assert_supported_file(target_handle)
                            finally:
                                _CloseHandle(target_handle)
                        encoded = target.encode("utf-16-le")
                        size = max(_FILE_RENAME_INFO.FileName.offset + len(encoded),
                                   C.sizeof(_FILE_RENAME_INFO))
                        buffer = C.create_string_buffer(size)
                        data = C.cast(buffer, C.POINTER(_FILE_RENAME_INFO)).contents
                        data.Flags = 1  # ReplaceIfExists, matching POSIX rename.
                        data.RootDirectory = target_parent
                        data.FileNameLength = len(encoded)
                        C.memmove(C.addressof(buffer) + _FILE_RENAME_INFO.FileName.offset,
                                  encoded, len(encoded))
                        iosb = _IO_STATUS_BLOCK()
                        status = _NtSetInformationFile(
                            handle, C.byref(iosb), buffer, size, 10)
                        if status < 0:
                            raise _winerror(_nt_error(status))
                    finally:
                        _CloseHandle(handle)

        replace = rename

        def _delete(self, path, dir_fd, directory):
            with self._parent(path, dir_fd) as (parent, name):
                if name is None:
                    raise OSError(errno.EINVAL, "deleting volume root is unsupported")
                handle = _native_open(name, parent, directory=directory,
                                      access=_DELETE | _READ_CONTROL
                                      | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE)
                try:
                    # FILE_DISPOSITION_INFO; deletion follows the object handle.
                    disposition = W.BOOL(True)
                    _checked(_SetFileInfo(handle, 4, C.byref(disposition), C.sizeof(disposition)))
                finally:
                    _CloseHandle(handle)

        def unlink(self, path, *, dir_fd=None):
            return self._delete(path, dir_fd, False)

        remove = unlink

        def rmdir(self, path, *, dir_fd=None):
            return self._delete(path, dir_fd, True)

        def acquire_lock(self, fd):
            identity = self.file_identity(fd)
            with self._guard:
                if identity in self._held_identities:
                    raise BlockingIOError(errno.EAGAIN, "vault is already locked")
                with _security_descriptor(self.private_security()) as descriptor:
                    attributes = _SECURITY_ATTRIBUTES(
                        C.sizeof(_SECURITY_ATTRIBUTES), descriptor, False)
                    name = f"Global\\LegendsEmpireVault-{identity[0]:x}-{identity[1]:x}"
                    mutex = _CreateMutex(C.byref(attributes), False, name)
                    if not mutex:
                        raise _winerror()
                result = _WaitForSingleObject(mutex, 0)
                if result not in {0, 0x80}:  # acquired / previous owner died
                    _CloseHandle(mutex)
                    if result == 258:
                        raise BlockingIOError(errno.EAGAIN, "vault is already locked")
                    raise _winerror()
                self._mutexes[fd] = (mutex, identity)
                self._held_identities.add(identity)

        def release_lock(self, fd):
            with self._guard:
                value = self._mutexes.pop(fd, None)
                if value is not None:
                    handle, identity = value
                    try:
                        _checked(_ReleaseMutex(handle))
                    finally:
                        _CloseHandle(handle)
                        self._held_identities.discard(identity)

    os_proxy = WindowsFS()
