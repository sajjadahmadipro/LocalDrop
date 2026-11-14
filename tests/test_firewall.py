"""Firewall reporting must not claim a port is reachable when it is not.

The rule dump and the active network profile are both faked here, so these cases
run on any machine and in any network category.
"""

import os
import unittest
from unittest import mock

from localdrop import firewall


def rule_dump(profiles="Private", port=8080):
    return (
        "Rule Name:                            LocalDrop\n"
        "----------------------------------------------------------------------\n"
        "Enabled:                              Yes\n"
        "Direction:                            In\n"
        "Profiles:                             %s\n"
        "Grouping:\n"
        "LocalIP:                              Any\n"
        "RemoteIP:                             Any\n"
        "Protocol:                             TCP\n"
        "LocalPort:                            %d\n"
        "RemotePort:                           Any\n"
        "Edge traversal:                       No\n"
        "Action:                               Allow\n"
        "\nOk.\n" % (profiles, port))


def profile_dump(category="Public"):
    return (
        "%s Profile Settings:\n"
        "----------------------------------------------------------------------\n"
        "State                                 ON\n"
        "Firewall Policy                       BlockInbound,AllowOutbound\n"
        "\nOk.\n" % category)


def netsh(rule=rule_dump(), profile=profile_dump(), rule_code=0, profile_code=0,
          blockers="", blockers_code=0):
    def run(command_line):
        if command_line.startswith("powershell"):
            return blockers_code, blockers
        if "currentprofile" in command_line:
            return profile_code, profile
        return rule_code, rule
    return run


class FirewallStatusTest(unittest.TestCase):
    def status(self, port=8080, **kwargs):
        with mock.patch.object(firewall, "supported", return_value=True), \
                mock.patch.object(firewall, "_run", side_effect=netsh(**kwargs)):
            return firewall.status(port)

    def test_no_rule_is_blocked(self):
        state, detail = self.status(rule="No rules match the specified criteria.")
        self.assertEqual(state, "blocked")
        self.assertIn("No rule named LocalDrop", detail)

    def test_private_rule_on_a_private_network_is_allowed(self):
        state, _ = self.status(profile=profile_dump("Private"))
        self.assertEqual(state, "allowed")

    def test_private_rule_on_a_public_network_is_a_mismatch(self):
        state, detail = self.status(profile=profile_dump("Public"))
        self.assertEqual(state, "mismatch")
        self.assertIn("public", detail)
        self.assertIn("Network profile type", detail)

    def test_domain_network_is_also_a_mismatch_for_a_private_rule(self):
        state, _ = self.status(profile=profile_dump("Domain"))
        self.assertEqual(state, "mismatch")

    def test_any_profile_rule_is_allowed_on_a_public_network(self):
        state, _ = self.status(rule=rule_dump("Any"), profile=profile_dump("Public"))
        self.assertEqual(state, "allowed")

    def test_rule_that_lists_public_is_allowed_on_a_public_network(self):
        state, _ = self.status(rule=rule_dump("Domain,Private,Public"),
                               profile=profile_dump("Public"))
        self.assertEqual(state, "allowed")

    def test_rule_for_another_port_is_blocked(self):
        state, detail = self.status(rule=rule_dump(port=9999))
        self.assertEqual(state, "blocked")
        self.assertIn("8080", detail)

    def test_an_unreadable_current_profile_does_not_invent_a_mismatch(self):
        state, _ = self.status(profile_code=1, profile="The request requires elevation.")
        self.assertEqual(state, "allowed")

    def test_an_unreadable_rule_scope_does_not_invent_a_mismatch(self):
        state, _ = self.status(rule=rule_dump().replace("Profiles:", "Scope:"))
        self.assertEqual(state, "allowed")

    def test_elevation_required_to_read_rules_is_unknown_not_allowed(self):
        state, _ = self.status(rule_code=1, rule="The request requires elevation.")
        self.assertEqual(state, "unknown")

    def test_other_platforms_say_so(self):
        with mock.patch.object(firewall, "supported", return_value=False):
            state, detail = firewall.status(8080)
        self.assertEqual(state, "not-windows")
        self.assertIn("Windows", detail)

    def test_a_cancelled_prompt_block_rule_overrides_a_perfect_rule(self):
        state, detail = self.status(rule=rule_dump("Any"),
                                    blockers="TCP Query User{ABC}C:\\python\\python.exe")
        self.assertEqual(state, "program-blocked")
        self.assertIn("BLOCK", detail)
        self.assertIn("firewall-open.bat", detail)

    def test_no_block_rules_leaves_a_good_rule_allowed(self):
        state, _ = self.status(rule=rule_dump("Any"), blockers="")
        self.assertEqual(state, "allowed")

    def test_a_failed_blocker_query_is_not_a_block_claim(self):
        state, _ = self.status(rule=rule_dump("Any"), blockers_code=1,
                               blockers="access denied")
        self.assertEqual(state, "allowed")

    def test_blocker_names_are_parsed_one_per_line(self):
        with mock.patch.object(firewall, "supported", return_value=True), \
                mock.patch.object(firewall, "_run",
                                  side_effect=netsh(blockers="rule one\nrule two\n")):
            self.assertEqual(firewall.program_blockers(), ["rule one", "rule two"])

    def test_other_platforms_have_no_blockers(self):
        with mock.patch.object(firewall, "supported", return_value=False):
            self.assertEqual(firewall.program_blockers(), [])


class ActiveProfileTest(unittest.TestCase):
    def test_reads_the_category_from_the_netsh_header(self):
        for category in ("Public", "Private", "Domain"):
            with mock.patch.object(firewall, "supported", return_value=True), \
                    mock.patch.object(firewall, "_run",
                                      side_effect=netsh(profile=profile_dump(category))):
                self.assertEqual(firewall.active_profile(), category.lower())

    def test_failure_is_an_empty_answer_not_a_guess(self):
        with mock.patch.object(firewall, "supported", return_value=True), \
                mock.patch.object(firewall, "_run", side_effect=netsh(profile_code=1)):
            self.assertEqual(firewall.active_profile(), "")


class AddCommandTest(unittest.TestCase):
    def test_rule_covers_every_profile_and_only_the_local_subnet(self):
        command = firewall.add_command(8080)
        self.assertIn('name="LocalDrop"', command)
        self.assertIn("localport=8080", command)
        self.assertIn("profile=any", command)
        self.assertIn("remoteip=localsubnet", command)

    def test_elevation_runs_exactly_the_command_we_print(self):
        if os.name != "nt":
            self.skipTest("ShellExecuteW is Windows only")
        with mock.patch.object(firewall, "is_admin", return_value=False), \
                mock.patch.object(firewall.ctypes.windll.shell32, "ShellExecuteW",
                                  return_value=42) as shell:
            ok, _ = firewall.elevate_and_add(8080)
        self.assertTrue(ok)
        args = shell.call_args[0]
        self.assertEqual(args[1], "runas")
        self.assertEqual(args[2], "netsh")
        self.assertEqual(args[3], firewall.add_command(8080).split(" ", 1)[1])


class FirewallCliTest(unittest.TestCase):
    def test_mismatch_exits_non_zero_and_says_why(self):
        with mock.patch.object(firewall, "supported", return_value=True), \
                mock.patch.object(firewall, "_run", side_effect=netsh()), \
                mock.patch("sys.stdout") as out:
            code = firewall.main(["status", "--port", "8080"])
        self.assertEqual(code, 1)
        printed = "".join(call.args[0] for call in out.write.call_args_list)
        self.assertIn("mismatch", printed)
        self.assertIn("public", printed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
