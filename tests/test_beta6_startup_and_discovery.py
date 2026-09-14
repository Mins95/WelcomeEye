"""Beta 6 startup and stale-discovery regressions; no real device is contacted."""
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import call, patch

PACKAGE = "welcomeeye_beta6_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components/welcomeeye_local")
]
sys.modules[PACKAGE] = package
client = importlib.import_module(PACKAGE + ".client")


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, payload):
        self.sent.append(payload)

    def shutdown(self, _how):
        pass

    def close(self):
        self.closed = True


class Beta6DiscoveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        client._DISCOVERY_CACHE.clear()
        client._DISCOVERY_NETWORK_REQUESTS = 0
        client._DISCOVERY_CACHE_HITS = 0
        client._DISCOVERY_CACHE_INVALIDATIONS = 0
        client._DISCOVERY_CONNECTION_RETRIES = 0

    def test_refused_cached_endpoint_is_rediscovered_once_before_login(self):
        host = "192.0.2.10"
        stale = types.SimpleNamespace(uid="TESTUID000", tcp_port=10001)
        fresh = types.SimpleNamespace(uid="TESTUID000", tcp_port=10002)
        client._DISCOVERY_CACHE[host] = stale
        sock = FakeSocket()
        session = client.Session(host, "admin", "code")

        with (
            patch.object(client, "discover", side_effect=[stale, fresh]),
            patch.object(
                client.socket,
                "create_connection",
                side_effect=[ConnectionRefusedError("stale"), sock],
            ) as create_connection,
            patch.object(client, "build_protected_login", return_value=b"login"),
            patch.object(client, "encode_password", return_value="encoded"),
            patch.object(session, "read", return_value=[(502, b"ok")]),
            patch.object(
                client,
                "decode_login_reply",
                return_value=(1, None, {"AppId": 1}, 1234),
            ),
        ):
            session.connect()

        self.assertEqual(
            create_connection.call_args_list,
            [
                call((host, 10001), timeout=5),
                call((host, 10002), timeout=5),
            ],
        )
        self.assertEqual(sock.sent, [b"login"])
        self.assertEqual(session.connection_stage, "authenticated")
        diagnostics = client.discovery_diagnostics()
        self.assertEqual(diagnostics["cache_invalidations"], 1)
        self.assertEqual(diagnostics["connection_refused_rediscoveries"], 1)
        session.close()

    def test_second_refusal_is_not_retried_again(self):
        host = "192.0.2.11"
        stale = types.SimpleNamespace(uid="TESTUID000", tcp_port=11001)
        fresh = types.SimpleNamespace(uid="TESTUID000", tcp_port=11002)
        client._DISCOVERY_CACHE[host] = stale
        session = client.Session(host, "admin", "code")

        with (
            patch.object(client, "discover", side_effect=[stale, fresh]),
            patch.object(
                client.socket,
                "create_connection",
                side_effect=[ConnectionRefusedError("stale"), ConnectionRefusedError("fresh")],
            ) as create_connection,
        ):
            with self.assertRaises(ConnectionRefusedError):
                session.connect()

        self.assertEqual(create_connection.call_count, 2)
        self.assertEqual(client.discovery_diagnostics()["connection_refused_rediscoveries"], 1)
        self.assertEqual(session.connection_error_type, "ConnectionRefusedError")


class Beta6StartupOrderingTests(unittest.TestCase):
    def test_dns_preload_runs_before_hub_and_platform_setup(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "custom_components/welcomeeye_local/__init__.py"
        ).read_text()
        helper = "dns.rdata.load_all_types(disable_dynamic_load=False)"
        preload = "await hass.async_add_executor_job(_preload_dns_types)"
        hub = "hub = WelcomeEyeHub(hass, entry)"
        platforms = "await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)"

        self.assertIn(helper, source)
        self.assertLess(source.index(preload), source.index(hub))
        self.assertLess(source.index(preload), source.index(platforms))


if __name__ == "__main__":
    unittest.main()
