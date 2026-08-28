#!/usr/bin/env python3
"""Loopback-only listener regression checks for scripts/server.py."""
import importlib.util
import pathlib
import socket
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "scripts" / "server.py"
SPEC = importlib.util.spec_from_file_location("memory_hub_rest_server", SERVER_PATH)
assert SPEC and SPEC.loader
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class LoopbackServerTest(unittest.TestCase):
    def test_valid_loopback_hosts_resolve_and_bind(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                family, sockaddr = server.resolve_loopback_bind_target(host, 0)
                self.assertIn(family, (socket.AF_INET, socket.AF_INET6))
                self.assertTrue(sockaddr[0] == "127.0.0.1" or sockaddr[0] == "::1")
                httpd = server.create_loopback_server(family, sockaddr)
                try:
                    self.assertGreater(httpd.server_address[1], 0)
                finally:
                    httpd.server_close()

    def test_non_loopback_addresses_are_rejected_before_server_start(self):
        for host in ("0.0.0.0", "192.168.1.10", "8.8.8.8"):
            with self.subTest(host=host):
                result = subprocess.run(
                    [sys.executable, str(SERVER_PATH), "--host", host, "--port", "0"],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("拒绝监听非 loopback 地址", result.stderr)


if __name__ == "__main__":
    unittest.main()
