"""The generated post-merge hook must stay a thin wrapper.

On 2026-07-31 deploy-web.sh learned to wait for the flock as well as the port
before restarting uvicorn. The post-merge hook carried its own older copy of
that logic, git does not version .git/hooks, so the fix could not propagate and
a bare `git pull` on the box still restarted the racy way. These tests fail if
the hook body ever grows restart logic of its own again.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "scripts" / "install-hooks.sh"
DEPLOY = REPO / "scripts" / "deploy-web.sh"


def _hook_body() -> str:
    """The heredoc the installer writes to .git/hooks/post-merge."""
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(r"<<'HOOK'\n(.*?)\nHOOK\n", text, re.S)
    assert match, "could not find the HOOK heredoc in install-hooks.sh"
    return match.group(1)


def _hook_code() -> str:
    """Hook body without comments — the comments legitimately *name* uvicorn and
    git while explaining why the hook doesn't touch them."""
    return "\n".join(line for line in _hook_body().splitlines()
                     if not line.lstrip().startswith("#"))


def test_hook_delegates_to_the_deploy_script():
    assert "scripts/deploy-web.sh" in _hook_body()
    assert "--no-pull" in _hook_body()


@pytest.mark.parametrize("forbidden", ["kill", "uvicorn", "flock", "ss -ltn"])
def test_hook_carries_no_restart_logic_of_its_own(forbidden):
    """Restart logic belongs in deploy-web.sh, which the hook calls. A second
    copy here is the bug this guards: it cannot be pulled, so it silently rots."""
    assert forbidden not in _hook_code(), (
        f"post-merge hook body contains {forbidden!r} — put it in deploy-web.sh "
        "instead, the hook must stay a wrapper"
    )


def test_hook_does_not_run_git_itself():
    """post-merge runs with GIT_DIR/GIT_INDEX_FILE set by the invoking git, so a
    nested git command would act on the wrong repository."""
    body = _hook_code()
    # Word-boundary, not line-start: the hook this replaced called git inside a
    # command substitution (REPO="$(git rev-parse --show-toplevel)"), which a
    # ^\s*git anchor sails straight past. Case-sensitive so the GIT_* names in
    # the `unset` line below don't match.
    assert not re.search(r"\bgit\b", body), "hook must not invoke git"
    assert "unset GIT_DIR" in body


def test_deploy_script_accepts_no_pull():
    text = DEPLOY.read_text(encoding="utf-8")
    assert "--no-pull)" in text, "deploy-web.sh must support the flag the hook passes"


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
@pytest.mark.parametrize("script", ["hook", "installer", "deploy"])
def test_shell_syntax_is_valid(script, tmp_path):
    if script == "hook":
        target = tmp_path / "post-merge"
        target.write_text(_hook_body(), encoding="utf-8")
    else:
        target = INSTALLER if script == "installer" else DEPLOY
    result = subprocess.run(["bash", "-n", str(target)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
