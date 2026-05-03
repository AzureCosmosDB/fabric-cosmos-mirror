"""Unit tests for lookup helpers and edge cases.

Run from the repo root:
    python -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

# Make python/ importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python"))

import fabric_client  # noqa: E402
import service_tags  # noqa: E402


def _mock_response(status: int, payload: dict, headers: dict | None = None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = str(payload)
    r.headers = headers or {}
    return r


class LookupTests(unittest.TestCase):
    """Regression: Fabric API may return items with displayName=None."""

    def test_lookup_workspace_skips_none_displayname(self):
        payload = {"value": [
            {"id": "w0", "displayName": None},
            {"id": "w1", "displayName": "MyWS"},
        ]}
        with patch("fabric_client.requests.get", return_value=_mock_response(200, payload)):
            self.assertEqual(fabric_client.lookup_workspace("tok", "MyWS"), "w1")

    def test_lookup_connection_skips_none_displayname(self):
        payload = {"value": [
            {"id": "c0", "displayName": None},
            {"id": "c1"},  # missing key entirely
            {"id": "c2", "displayName": "cdb-pp-c"},
        ]}
        with patch("fabric_client.requests.get", return_value=_mock_response(200, payload)):
            self.assertEqual(fabric_client.lookup_connection("tok", "cdb-pp-c"), "c2")

    def test_lookup_connection_case_insensitive(self):
        payload = {"value": [{"id": "c1", "displayName": "CDB-PP-C"}]}
        with patch("fabric_client.requests.get", return_value=_mock_response(200, payload)):
            self.assertEqual(fabric_client.lookup_connection("tok", "cdb-pp-c"), "c1")

    def test_lookup_connection_not_found_exits(self):
        payload = {"value": [{"id": "c0", "displayName": "other"}]}
        with patch("fabric_client.requests.get", return_value=_mock_response(200, payload)):
            with self.assertRaises(SystemExit):
                fabric_client.lookup_connection("tok", "missing")

    def test_lookup_folder_paginates_and_skips_none(self):
        page1 = {"value": [{"id": "f0", "displayName": None}],
                 "continuationUri": "https://x/next"}
        page2 = {"value": [{"id": "f1", "displayName": "Mirroring"}]}
        responses = iter([_mock_response(200, page1), _mock_response(200, page2)])
        with patch("fabric_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
            self.assertEqual(fabric_client.lookup_folder("tok", "ws", "Mirroring"), "f1")

    def test_lookup_value_key_null(self):
        # Some API errors return {"value": null} — must not crash
        payload = {"value": None}
        with patch("fabric_client.requests.get", return_value=_mock_response(200, payload)):
            with self.assertRaises(SystemExit):
                fabric_client.lookup_workspace("tok", "x")


class HelperTests(unittest.TestCase):
    def test_derive_account_name(self):
        self.assertEqual(
            fabric_client.derive_account_name("https://cdb-private.documents.azure.com:443/"),
            "cdb-private",
        )
        self.assertEqual(fabric_client.derive_account_name("acct"), "acct")
        self.assertEqual(
            fabric_client.derive_account_name("http://my.documents.azure.com"),
            "my",
        )

    def test_normalize_location(self):
        self.assertEqual(fabric_client.normalize_location("West Central US"), "westcentralus")
        self.assertEqual(fabric_client.normalize_location("eastus"), "eastus")


class ServiceTagsTests(unittest.TestCase):
    def test_extract_handles_none_props_and_names(self):
        tags = {"values": [
            {"name": None, "properties": None},
            {"name": "DataFactory.WestUS",
             "properties": {"systemService": "DataFactory", "region": "westus",
                            "addressPrefixes": ["1.2.3.4/32", "::1/128"]}},
            {"name": "PowerQueryOnline",
             "properties": {"systemService": None,
                            "addressPrefixes": ["5.6.7.8/32"]}},
            {"name": "OtherTag", "properties": {}},
        ]}
        ips = service_tags.extract_fabric_ips(tags, "westus")
        self.assertIn("1.2.3.4/32", ips)
        self.assertIn("5.6.7.8/32", ips)
        self.assertNotIn("::1/128", ips)  # IPv6 filtered

    def test_extract_no_datafactory_warns_but_returns(self):
        tags = {"values": [
            {"name": "PowerQueryOnline",
             "properties": {"systemService": "PowerQueryOnline",
                            "addressPrefixes": ["9.9.9.9/32"]}},
        ]}
        ips = service_tags.extract_fabric_ips(tags, "eastus")
        self.assertEqual(ips, ["9.9.9.9/32"])

    def test_extract_empty_payload(self):
        self.assertEqual(service_tags.extract_fabric_ips({}, "westus"), [])
        self.assertEqual(service_tags.extract_fabric_ips({"values": None}, "westus"), [])


class ImportSmokeTests(unittest.TestCase):
    """Ensure every module imports without error (catches typos like the
    earlier IPAddressOrRange regression)."""

    def test_imports(self):
        import auth, fabric_client, main, mirroring, network_acl, rbac, service_tags, vnet  # noqa: F401


if __name__ == "__main__":
    unittest.main(verbosity=2)
