#!/usr/bin/env python3
"""AOSP17 mondrian Vibrator AIDL V2 source-only regression gate.

Uses actual source code and real host C++/GNU Make, not a simulated HAL.
Android Soong, Xiaomi kernel/haptics, SELinux, VINTF/FCM and physical tests
remain required before the service can be packaged for PE-Reborn mondrian.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OFFLOAD = ROOT / "aidl/VibratorOffload.cpp"
PATTERNS = ROOT / "effect/VibratorPatterns.cpp"

GLINK_HARNESS = r'''
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <string>
#include <sys/socket.h>
#include <unistd.h>
#define ALOGE(...) ((void)0)
class OffloadGlinkConnection {
public:
    int GlinkOpen(std::string& dev);
    int GlinkClose();
    int GlinkPoll();
    int GlinkRead(uint8_t *data, size_t size);
    int GlinkWrite(uint8_t *buf, size_t buflen);
    void setTestFd(int value) { fd = value; }
private:
    std::string dev_name;
    int fd = -1;
};
'''
GLINK_MAIN = r'''
int main() {
    std::signal(SIGPIPE, SIG_IGN);
    OffloadGlinkConnection conn;
    uint8_t buf[4] = {};
    assert(conn.GlinkPoll() == -EBADF);
    assert(conn.GlinkRead(buf, 4) == -EBADF);
    assert(conn.GlinkWrite(buf, 4) == -EBADF);
    std::string missing = "/dev/pe-reborn-vibrator-nonexistent";
    assert(conn.GlinkOpen(missing) == -ENOENT);
    assert(conn.GlinkClose() == 0);

    int fds[2];
    assert(::socketpair(AF_UNIX, SOCK_STREAM, 0, fds) == 0);
    conn.setTestFd(fds[0]);
    assert(conn.GlinkRead(buf, 0) == -EINVAL);
    assert(conn.GlinkRead(nullptr, 1) == -EINVAL);
    assert(conn.GlinkWrite(nullptr, 1) == -EINVAL);
    assert(conn.GlinkWrite(buf, 0) == -EINVAL);

    uint8_t incoming[4] = {1, 2, 3, 4};
    assert(::write(fds[1], incoming, 4) == 4);
    assert(conn.GlinkRead(buf, 4) == 0);
    assert(::memcmp(buf, incoming, 4) == 0);

    uint8_t outgoing[4] = {9, 8, 7, 6};
    assert(conn.GlinkWrite(outgoing, 4) == 0);
    uint8_t back[4] = {};
    assert(::read(fds[1], back, 4) == 4);
    assert(::memcmp(back, outgoing, 4) == 0);

    assert(::shutdown(fds[1], SHUT_WR) == 0);
    assert(conn.GlinkRead(buf, 1) == -EPIPE);
    assert(::close(fds[1]) == 0);
    assert(conn.GlinkWrite(outgoing, 4) == -EPIPE);
    assert(conn.GlinkClose() == 0);
    assert(conn.GlinkClose() == 0);
    return 0;
}
'''
PATTERN_MAIN = r'''
#include "VibratorPatterns.h"
#include <cassert>
#include <cstdint>
int main() {
    uint8_t* config = nullptr;
    uint32_t config_size = 0;
    assert(get_pattern_config(&config, &config_size) == 0);
    assert(config != nullptr && config_size != 0);
    for (int i = 0; i < 25; ++i) {
        uint8_t* data = nullptr;
        uint32_t length = 0;
        assert(get_pattern_data(&data, &length) == 0);
        assert(data != nullptr && length > 0);
        free_pattern_mem(data);  // ASan checks new[] vs delete[].
    }
    free_pattern_mem(nullptr);
    return 0;
}
'''


class VibratorSourceTests(unittest.TestCase):
    def compile_and_run(self, sources, flags):
        with tempfile.TemporaryDirectory(prefix="pe-a17-vibrator-") as tmp:
            folder = Path(tmp)
            main = folder / "test.cpp"
            exe = folder / "test"
            main.write_text(sources[0], encoding="utf-8")
            files = [str(main)] + [str(x) for x in sources[1:]]
            command = [
                "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-fno-omit-frame-pointer", "-I", str(ROOT / "effect"),
                *flags, *files, "-o", str(exe),
            ]
            build = subprocess.run(command, capture_output=True, text=True,
                                   check=False)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True,
                                 check=False,
                                 env={**os.environ,
                                      "ASAN_OPTIONS": "alloc_dealloc_mismatch=1:detect_leaks=1"})
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_real_glink_methods_host_compilation_and_errno_contract(self):
        text = OFFLOAD.read_text(encoding="utf-8")
        start = text.index("#define GLINK_MAX_CONN_RETRIES")
        end = text.index("}  // namespace vibrator", start)
        body = text[start:end]
        self.assertIn("return -errno;", body)
        self.assertIn("return -EPIPE;", body)
        self.assertIn("if (rc == 0)", body)
        self.assertNotIn("return errno;", body)
        self.compile_and_run((GLINK_HARNESS + body + GLINK_MAIN,), [])

    def test_pattern_allocation_matches_delete_array_under_asan(self):
        source = PATTERNS.read_text(encoding="utf-8")
        self.assertIn("new (std::nothrow) uint8_t[LEN_TOTAL_PATTERNS]", source)
        self.assertIn("delete[] ptr;", source)
        self.compile_and_run((PATTERN_MAIN, PATTERNS), [
            "-fsanitize=address", "-fsanitize=undefined",
        ])

    def test_uevent_and_channel_lifetime_cleanup(self):
        source = OFFLOAD.read_text(encoding="utf-8")
        header = (ROOT / "aidl/include/Vibrator.h").read_text(encoding="utf-8")
        self.assertIn("int fd = -1;", header)
        recv = source.split("void PatternOffload::SSREventListener", 1)[1].split(
            "void PatternOffload::SendPatterns", 1)[0]
        self.assertIn("char *msg_ptr = msg;", recv)
        self.assertGreater(recv.index("char *msg_ptr = msg;"),
                           recv.index("uevent_kernel_multicast_recv"))
        self.assertIn("::close(device_fd);", recv)
        send = source.split("void PatternOffload::SendPatterns", 1)[1].split(
            "int PatternOffload::sendData", 1)[0]
        self.assertIn("free_pattern_mem(pattern);", send)
        self.assertIn("GlinkCh.GlinkClose();", send)
        self.assertEqual(send.count("return;"), 1)  # only if open failed
        self.assertLess(send.index("free_pattern_mem(pattern);"),
                        send.index("GlinkCh.GlinkClose();"))

    def test_old_v2_contract_not_falsely_upgraded(self):
        xml = ET.parse(ROOT / "aidl/vendor.qti.hardware.vibrator.service.xml")
        hal = xml.getroot().find("hal")
        self.assertEqual(hal.get("format"), "aidl")
        self.assertEqual(hal.findtext("name"), "android.hardware.vibrator")
        self.assertEqual(hal.findtext("version"), "2")
        self.assertEqual(hal.findtext("fqname"), "IVibrator/default")
        soong = (ROOT / "aidl/Android.bp").read_text(encoding="utf-8")
        self.assertIn("android.hardware.vibrator-V2-ndk", soong)
        self.assertNotIn("android.hardware.vibrator-V4-ndk", soong)

    def evaluate_product(self, device, version):
        with tempfile.TemporaryDirectory(prefix="pe-vib-make-") as tmp:
            mk = Path(tmp) / "test.mk"
            mk.write_text(
                "include " + str(ROOT / "vibrator-vendor-product.mk") + "\n"
                ".PHONY: all\nall:\n"
                "\t@echo VIB_PKGS=$(PRODUCT_PACKAGES)\n"
                "\t@echo VIB_COPY=$(PRODUCT_COPY_FILES)\n"
                "\t@echo VIB_SERVICE=$(QTI_VIBRATOR_HAL_SERVICE)\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["make", "--no-print-directory", "-f", str(mk), "all",
                 "TARGET_DEVICE=" + device, "PLATFORM_VERSION=" + version],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_mondrian_service_not_automatically_installed(self):
        for version in ("14", "17"):
            with self.subTest(version=version):
                result = self.evaluate_product("mondrian", version)
                self.assertIn("VIB_PKGS=\n", result)
                self.assertIn("VIB_COPY=\n", result)
                self.assertIn("VIB_SERVICE=\n", result)

    def test_other_historical_devices_retain_vendor_selection(self):
        result = self.evaluate_product("historical_target", "14")
        self.assertIn("vendor.qti.hardware.vibrator.service", result)
        self.assertIn("excluded-input-devices.xml", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
