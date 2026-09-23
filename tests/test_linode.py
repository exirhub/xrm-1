#!/usr/bin/env python3
"""Isolated bootstrap checks: no root, network, apt, or real installations."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LinodeBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def run_shell(self, body, expected=0, source=None):
        source = source or ROOT / "linode.sh"
        result = subprocess.run(
            ["bash", "-c", 'source "$1"\n' + body, "test", str(source)],
            cwd=self.work, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_source_has_no_install_side_effects(self):
        self.run_shell("declare -F linode_main >/dev/null")

    def test_retry_succeeds_after_transient_failures(self):
        self.run_shell("""
count=0
sleep() { :; }
flaky() { ((count+=1)); [[ "$count" -eq 3 ]]; }
linode_retry flaky || exit 1
[[ "$count" -eq 3 ]]
""")

    def test_retry_is_bounded_and_preserves_failure(self):
        self.run_shell("""
count=0
sleep() { :; }
broken() { ((count+=1)); return 1; }
linode_retry broken && exit 10
[[ "$count" -eq 6 ]]
""")

    def test_fresh_target_allowed(self):
        self.run_shell('linode_check_target state x-ui.db binary')

    def test_existing_database_is_preserved(self):
        (self.work / "x-ui.db").write_text("original")
        self.run_shell('linode_check_target state x-ui.db binary', expected=1)
        self.assertEqual((self.work / "x-ui.db").read_text(), "original")

    def test_partial_install_is_rejected(self):
        (self.work / "binary").touch()
        self.run_shell('linode_check_target state x-ui.db binary', expected=1)

    def test_completed_install_is_not_reinstalled(self):
        (self.work / "state").mkdir()
        (self.work / "state/complete").touch()
        self.run_shell("""
systemctl() { [[ "$*" == "is-active --quiet x-ui" ]]; }
linode_check_target state x-ui.db binary
""", expected=10)

    def test_completed_but_broken_service_is_not_success(self):
        (self.work / "state").mkdir()
        (self.work / "state/complete").touch()
        self.run_shell("""
systemctl() { return 1; }
linode_check_target state x-ui.db binary
""", expected=1)

    def test_download_works_without_new_curl_flags(self):
        self.run_shell("""
curl() {
    local out=""
    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            --retry-all-errors) return 2 ;;
            --output) shift; out="$1" ;;
        esac
        shift
    done
    printf '#!/bin/bash\\ntrue\\n' > "$out"
}
linode_download https://example.invalid script || exit 1
[[ -s script ]]
""")

    def test_download_rejects_empty_success_response(self):
        self.run_shell("""
printf old > script
curl() { return 0; }
linode_download https://example.invalid script
""", expected=1)

    def test_download_retries_partial_failure_from_scratch(self):
        self.run_shell("""
count=0
sleep() { :; }
curl() {
    local out=""
    while [[ "$#" -gt 0 ]]; do
        case "$1" in --output) shift; out="$1" ;; esac
        shift
    done
    [[ ! -s "$out" ]] || return 99
    ((count+=1))
    if [[ "$count" -eq 1 ]]; then
        printf partial > "$out"
        return 18
    fi
    printf complete > "$out"
}
linode_retry linode_download https://example.invalid script || exit 1
[[ "$count" -eq 2 && "$(cat script)" == complete ]]
""")

    def test_installer_failure_is_not_masked_by_later_success(self):
        (self.work / "script").write_text("#!/bin/bash\nfalse\ntouch should-not-exist\n")
        self.run_shell("linode_execute script", expected=1)
        self.assertFalse((self.work / "should-not-exist").exists())

    def test_installer_receives_closed_stdin(self):
        (self.work / "script").write_text("#!/bin/bash\nif read -r input; then exit 9; fi\n")
        self.run_shell("linode_execute script")

    def test_non_script_response_rejected(self):
        (self.work / "script").write_text("touch should-not-exist\n")
        self.run_shell("linode_execute script", expected=1)
        self.assertFalse((self.work / "should-not-exist").exists())

    def test_invalid_syntax_rejected_before_execution(self):
        (self.work / "script").write_text("#!/bin/bash\ntouch should-not-exist\nif\n")
        self.run_shell("linode_execute script", expected=1)
        self.assertFalse((self.work / "should-not-exist").exists())

    def shared_functions(self):
        # Load only declarations, never the provisioner's top-level commands.
        installer = (ROOT / "install.sh").read_text()
        functions, main = installer.split("\nconfigure_persistent_dns\n", 1)
        self.assertIn("cd /root", main)
        source = self.work / "shared-functions.sh"
        source.write_text(functions)
        return source

    def test_shared_download_retries_dns_or_curl_failure(self):
        self.run_shell("""
ensure_github_dns() { return 0; }
sleep() { :; }
count=0
curl() {
    local out=""
    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            --retry-all-errors) return 2 ;;
            --output) shift; out="$1" ;;
        esac
        shift
    done
    ((count+=1))
    [[ "$count" -gt 1 ]] || return 6
    printf content > "$out"
}
download_file https://example.invalid db || exit 1
[[ "$count" -eq 2 && "$(cat db)" == content ]]
""", source=self.shared_functions())

    def test_shared_download_never_accepts_stale_file(self):
        self.run_shell("""
ensure_github_dns() { return 0; }
sleep() { :; }
count=0
curl() { ((count+=1)); return 0; }
printf stale > db
download_file https://example.invalid db && exit 10
[[ "$count" -eq 10 && ! -s db ]]
""", source=self.shared_functions())

    def test_no_cloud_init_self_wait_or_unsafe_curl_pipe(self):
        content = (ROOT / "linode.sh").read_text()
        self.assertNotIn("cloud-init status --wait", content)
        self.assertNotIn("curl |", content)
        self.assertIn("DPkg::Lock::Timeout=120", content)
        self.assertIn("XUI_NONINTERACTIVE=1", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
