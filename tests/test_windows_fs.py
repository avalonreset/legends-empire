"""Native NTFS adapter regression tests; no real vault or provider access."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_empire.windows_fs import os_proxy


@unittest.skipUnless(os.name == "nt", "native Windows adapter")
class WindowsFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="empire-native-fs-")
        self.path = Path(self.temporary.name)
        self.fs = os_proxy
        self.root = self.fs.open(self.path, self.fs.O_DIRECTORY)

    def tearDown(self):
        self.fs.close(self.root)
        self.temporary.cleanup()

    def _write(self, name, content=b"binary\r\n\x00content"):
        fd = self.fs.open(
            name, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self.root)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            self.fs.fsync(stream.fileno())
        return content

    def test_binary_crt_descriptors_and_stable_identity(self):
        content = self._write("note")
        fd = self.fs.open("note", os.O_RDONLY, dir_fd=self.root)
        try:
            self.assertEqual(os.read(fd, 4096), content)
            metadata = self.fs.fstat(fd)
            native = (self.path / "note").stat()
            self.assertEqual((metadata.st_dev, metadata.st_ino),
                             (native.st_dev, native.st_ino))
        finally:
            self.fs.close(fd)

    def test_private_permissions_are_real_and_survive_reopening(self):
        self._write("private")
        fd = self.fs.open("private", os.O_RDWR, dir_fd=self.root)
        try:
            self.fs.fchmod(fd, 0o600)
            self.assertEqual(self.fs.get_security(fd), self.fs.private_security())
        finally:
            self.fs.close(fd)
        reopened = self.fs.open("private", os.O_RDONLY, dir_fd=self.root)
        try:
            self.assertEqual(self.fs.fstat(reopened).st_mode & 0o777, 0o600)
            self.assertEqual(self.fs.get_security(reopened), self.fs.private_security())
        finally:
            self.fs.close(reopened)

    def test_inherited_acl_restored_exactly_without_duplicate_aces(self):
        (self.path / "original").write_bytes(b"existing")
        source = self.fs.open("original", os.O_RDONLY, dir_fd=self.root)
        try:
            security = self.fs.get_security(source)
        finally:
            self.fs.close(source)
        self._write("replacement")
        replacement = self.fs.open("replacement", os.O_RDWR, dir_fd=self.root)
        try:
            self.fs.set_security(replacement, security)
            self.assertEqual(self.fs.get_security(replacement), security)
        finally:
            self.fs.close(replacement)

    def test_named_stream_rejected_without_losing_content(self):
        content = self._write("note")
        stream_path = str(self.path / "note") + ":private"
        with open(stream_path, "wb") as stream:
            stream.write(b"hidden metadata")
        with self.assertRaises(OSError):
            self.fs.stat("note", dir_fd=self.root)
        self._write("replacement", b"replacement")
        with self.assertRaises(OSError):
            self.fs.replace("replacement", "note", src_dir_fd=self.root,
                            dst_dir_fd=self.root)
        self.assertEqual((self.path / "note").read_bytes(), content)
        with open(stream_path, "rb") as stream:
            self.assertEqual(stream.read(), b"hidden metadata")

    def test_readonly_file_refused_before_replacement(self):
        self._write("readonly")
        os.chmod(self.path / "readonly", 0o444)
        try:
            with self.assertRaises(OSError):
                self.fs.stat("readonly", dir_fd=self.root)
        finally:
            os.chmod(self.path / "readonly", 0o600)

    def test_encrypted_attribute_is_rejected_before_content_mutation(self):
        from claude_empire.windows_fs import _BY_HANDLE_FILE_INFORMATION

        self._write("encrypted-attribute")
        descriptor = self.fs.open("encrypted-attribute", os.O_RDONLY, dir_fd=self.root)
        try:
            metadata = _BY_HANDLE_FILE_INFORMATION()
            metadata.attributes = 0x4000  # FILE_ATTRIBUTE_ENCRYPTED
            with self.assertRaises(OSError):
                self.fs._assert_supported_file(self.fs._handle(descriptor), metadata)
        finally:
            self.fs.close(descriptor)
        self.assertEqual((self.path / "encrypted-attribute").read_bytes(),
                         b"binary\r\n\x00content")

    def test_descriptor_relative_rename_delete_and_iteration(self):
        self._write("before")
        self.fs.rename("before", "after", src_dir_fd=self.root, dst_dir_fd=self.root)
        self.assertEqual(self.fs.listdir(self.root), ["after"])
        with self.fs.scandir(self.root) as entries:
            entry = next(entries)
            self.assertEqual(entry.name, "after")
            self.assertTrue(entry.is_file())
            self.assertGreater(entry.inode(), 0)
        self.fs.unlink("after", dir_fd=self.root)
        self.assertEqual(self.fs.listdir(self.root), [])

    def test_pinned_parent_survives_namespace_rename(self):
        self.fs.mkdir("original", dir_fd=self.root)
        child = self.fs.open("original", self.fs.O_DIRECTORY, dir_fd=self.root)
        try:
            self.fs.rename("original", "moved", src_dir_fd=self.root, dst_dir_fd=self.root)
            self.fs.mkdir("original", dir_fd=self.root)
            fd = self.fs.open("note", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                              0o600, dir_fd=child)
            self.fs.close(fd)
            self.assertTrue((self.path / "moved" / "note").exists())
            self.assertFalse((self.path / "original" / "note").exists())
        finally:
            self.fs.close(child)

    def test_parent_traversal_and_alternate_streams_are_rejected(self):
        for name in ("../escape", "..\\escape", "note:stream", "name.", "CON"):
            with self.subTest(name=name), self.assertRaises(OSError):
                self.fs.open(name, os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=self.root)

    def test_hardlink_acl_mutation_is_rejected(self):
        self._write("first")
        os.link(self.path / "first", self.path / "second")
        fd = self.fs.open("first", os.O_RDWR, dir_fd=self.root)
        try:
            before = self.fs.get_security(fd)
            with self.assertRaises(OSError) as caught:
                self.fs.fchmod(fd, 0o600)
            self.assertEqual(caught.exception.errno, errno.EMLINK)
            self.assertEqual(self.fs.get_security(fd), before)
        finally:
            self.fs.close(fd)

    def test_null_and_foreign_security_descriptors_are_rejected(self):
        with self.assertRaises(OSError):
            self.fs.validate_security("O:SYG:SYD:P(A;;FA;;;SY)")
        with self.assertRaises(OSError):
            self.fs.validate_security("O:SYG:SYD:NO_ACCESS_CONTROL")
        self.assertEqual(self.fs.validate_security(self.fs.private_security()),
                         self.fs.private_security())

    def test_builtin_sid_aliases_and_default_owner_are_semantic_identities(self):
        import claude_empire.windows_fs as native

        user = "S-1-5-21-100000001-200000002-300000003-1001"
        administrators, system = "S-1-5-32-544", "S-1-5-18"
        for token_user, token_owner, token_group in (
            (user, user, administrators),
            (user, administrators, administrators),
            (system, system, administrators),
        ):
            with self.subTest(owner=token_owner, group=token_group):
                identities = {1: token_user, 4: token_owner, 5: token_group}
                with patch.object(native, "_token_sid", side_effect=identities.__getitem__):
                    adapter = native.WindowsFS()
                numeric = (
                    f"O:{token_owner}G:{token_group}"
                    f"D:P(A;;FA;;;S-1-5-18)(A;;FA;;;{token_user})"
                )
                canonical = adapter.private_security()
                self.assertIn("G:BA", canonical)
                self.assertEqual(adapter.validate_security(numeric), canonical)
                self.assertEqual(adapter.validate_security(canonical), canonical)
                if token_owner == administrators:
                    self.assertTrue(canonical.startswith("O:BA"))
                    self.assertIn(f"(A;;FA;;;{user})", canonical)
                    # Membership in Administrators does not authorize a
                    # different owner's descriptor under this adapter.
                    with self.assertRaises(OSError):
                        adapter.validate_security(numeric.replace(f"O:{administrators}", f"O:{user}"))
                elif token_owner == system:
                    self.assertTrue(canonical.startswith("O:SY"))
                with self.assertRaises(OSError):
                    adapter.validate_security(numeric.replace(f"G:{token_group}", "G:S-1-5-19"))

    def test_same_process_lock_contention_is_not_recursive(self):
        duplicate = self.fs.dup(self.root)
        self.fs.acquire_lock(self.root)
        try:
            with self.assertRaises(BlockingIOError):
                self.fs.acquire_lock(duplicate)
        finally:
            self.fs.release_lock(self.root)
        try:
            self.fs.acquire_lock(duplicate)
            self.fs.release_lock(duplicate)
        finally:
            self.fs.close(duplicate)

    def test_mutex_identity_is_independent_of_python_stat_width(self):
        source = Path(__file__).resolve().parents[1]
        alternate_version = "(3, 13)" if sys.version_info < (3, 12) else "(3, 11)"
        program = (
            "import sys\n"
            f"sys.path.insert(0, {str(source)!r})\n"
            "from claude_empire.windows_fs import os_proxy as fs\n"
            f"fd=fs.open({str(self.path)!r}, fs.O_DIRECTORY)\n"
            f"sys.version_info={alternate_version}\n"
            "try:\n"
            "    fs.acquire_lock(fd)\n"
            "except BlockingIOError:\n"
            "    sys.exit(0)\n"
            "else:\n"
            "    fs.release_lock(fd)\n"
            "    sys.exit(77)\n"
        )
        self.fs.acquire_lock(self.root)
        try:
            result = subprocess.run(
                [sys.executable, "-c", program], capture_output=True, text=True,
                timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            self.fs.release_lock(self.root)

    def test_retained_directory_cannot_redirect_after_becoming_junction(self):
        import ctypes as c
        from ctypes import wintypes as w

        parent, outside = self.path / "parent", self.path / "outside"
        parent.mkdir()
        outside.mkdir()
        (outside / "marker").write_bytes(b"external")
        descriptor = self.fs.open(parent, self.fs.O_DIRECTORY)
        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        kernel.CreateFileW.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.DeviceIoControl.argtypes = [
            w.HANDLE, w.DWORD, c.c_void_p, w.DWORD, c.c_void_p, w.DWORD,
            c.POINTER(w.DWORD), c.c_void_p]
        kernel.DeviceIoControl.restype = w.BOOL
        try:
            handle = kernel.CreateFileW(str(parent), 0x40000000, 7, None, 3,
                                        0x02200000, None)
            if handle == c.c_void_p(-1).value:
                raise c.WinError(c.get_last_error())
            try:
                target = ("\\??\\" + str(outside)).encode("utf-16-le")
                display = str(outside).encode("utf-16-le")
                paths = target + b"\0\0" + display + b"\0\0"
                data = struct.pack(
                    "<IHHHHHH", 0xA0000003, 8 + len(paths), 0,
                    0, len(target), len(target) + 2, len(display)) + paths
                buffer, count = c.create_string_buffer(data), w.DWORD()
                if not kernel.DeviceIoControl(
                    handle, 0x900A4, buffer, len(data), None, 0, c.byref(count), None
                ):
                    raise c.WinError(c.get_last_error())
            finally:
                kernel.CloseHandle(handle)
            with self.assertRaises(OSError):
                self.fs.open("marker", os.O_RDWR, dir_fd=descriptor)
            self.assertEqual((outside / "marker").read_bytes(), b"external")
        finally:
            self.fs.close(descriptor)
            # Remove only the junction object, never recurse through it.
            if parent.is_dir():
                os.rmdir(parent)

    def test_zero_signal_does_not_terminate_process(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self.fs.kill(process.pid, 0)
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=10)
        with self.assertRaises(ProcessLookupError):
            self.fs.kill(process.pid, 0)


if __name__ == "__main__":
    unittest.main()
