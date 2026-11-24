"""Offline checks of the SDK models used for account PATCH and SQL RBAC."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from azure.mgmt.cosmosdb.models import DatabaseAccountGetResults, SqlRoleDefinitionGetResults

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import network_acl
import rbac
import service_tags
import vnet


class CosmosSdkTests(unittest.TestCase):
    def test_network_updates_preserve_existing_settings(self):
        client = Mock()
        rule = {"ipAddressOrRange": "192.0.2.1"}
        client.database_accounts.get.return_value = DatabaseAccountGetResults({
            "location": "eastus",
            "properties": {
                "publicNetworkAccess": "Disabled", "ipRules": [rule],
                "capabilities": [{"name": "existing"}],
                "networkAclBypass": "AzureServices", "networkAclBypassResourceIds": ["old"],
            },
        })
        state = vnet.capture_account_state(client, "rg", "acct")
        self.assertEqual(state.location, "eastus")
        tags = {"values": [{"name": "DataFactory.EastUS", "properties": {
            "systemService": "DataFactory", "region": "eastus", "addressPrefixes": ["192.0.2.2"],
        }}]}
        with patch.object(service_tags, "fetch_service_tags", return_value=tags):
            service_tags.configure_ip_firewall(None, client, "sub", "rg", "acct", "eastus", state)
        network_acl.configure_network_acl_bypass(client, "rg", "acct", "ws", "tenant", state)
        vnet.restore_network_settings(client, "rg", "acct", state)

        firewall, capability, bypass, restore = [
            call.args[2].as_dict()["properties"]
            for call in client.database_accounts.begin_update.call_args_list
        ]
        self.assertEqual(firewall["publicNetworkAccess"], "Enabled")
        self.assertCountEqual(firewall["ipRules"], [rule, {"ipAddressOrRange": "192.0.2.2"}])
        self.assertEqual(capability, {"capabilities": [
            {"name": "existing"}, {"name": "EnableFabricNetworkAclBypass"},
        ]})
        self.assertEqual(bypass["networkAclBypass"], "AzureServices")
        self.assertCountEqual(bypass["networkAclBypassResourceIds"], [
            "old", "/tenants/tenant/subscriptions/00000000-0000-0000-0000-000000000000"
            "/resourceGroups/Fabric/providers/Microsoft.Fabric/workspaces/ws",
        ])
        self.assertEqual(restore, {"publicNetworkAccess": "Disabled", "ipRules": [rule]})

    def test_role_definition_and_assignment_payloads(self):
        client = Mock()
        sql = client.sql_resources
        sql.list_sql_role_definitions.return_value = []
        role = SqlRoleDefinitionGetResults({"id": "custom", "properties": {
            "roleName": "FabricMirroringRole",
        }})
        create = sql.begin_create_update_sql_role_definition
        create.return_value.result.return_value = role
        rbac.setup_rbac(client, "rg", "acct", "/scope", "principal")
        self.assertEqual(create.call_args.args[3].as_dict(), {"properties": {
            "roleName": "FabricMirroringRole", "type": "CustomRole",
            "assignableScopes": ["/scope"], "permissions": [{"dataActions": [
                "Microsoft.DocumentDB/databaseAccounts/readMetadata",
                "Microsoft.DocumentDB/databaseAccounts/readAnalytics",
            ]}],
        }})
        assignments = sql.begin_create_update_sql_role_assignment.call_args_list
        self.assertEqual([call.args[3].as_dict() for call in assignments], [
            {"properties": {"roleDefinitionId": role_id, "principalId": "principal", "scope": "/scope"}}
            for role_id in ("custom", f"/scope/sqlRoleDefinitions/{rbac.BUILTIN_DATA_CONTRIBUTOR_ID}")
        ])
        sql.list_sql_role_definitions.return_value = [role]
        self.assertEqual(rbac._create_fabric_mirroring_role(client, "rg", "acct", "/scope"), "custom")
        create.assert_called_once()
