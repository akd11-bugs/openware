import os
import socket
import subprocess
import sys

import pytest

from agentshield.txn.autosearch import SandboxUnavailable, sandbox_command

mac = pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS only")


def run(code, root, write_dirs):
    cmd = sandbox_command([sys.executable, "-c", code], root, write_dirs)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "docs/results").mkdir(parents=True)
    (tmp_path / "notes.txt").write_text("readable")
    (tmp_path / ".env").write_text("SECRET=1")
    return tmp_path


@mac
def test_normal_code_still_runs(proj):
    r = run("print(21 * 2)", proj, [proj / "docs/results"])
    assert r.returncode == 0 and r.stdout.strip() == "42"


@mac
def test_the_network_is_blocked(proj):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        r = run(f"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=2)", proj, [proj / "docs/results"])
    finally:
        srv.close()
    assert r.returncode != 0 and "PermissionError" in r.stderr


@mac
def test_writes_are_allowed_only_in_the_named_folders(proj):
    ok = run(f"open({str(proj / 'docs/results/out.jsonl')!r}, 'w').write('x')", proj, [proj / "docs/results"])
    assert ok.returncode == 0 and (proj / "docs/results/out.jsonl").read_text() == "x"
    for target in (proj / "evil.py", proj / "docs/evil.py", proj / "notes.txt"):
        bad = run(f"open({str(target)!r}, 'w').write('x')", proj, [proj / "docs/results"])
        assert bad.returncode != 0 and "PermissionError" in bad.stderr, target
    assert not (proj / "evil.py").exists() and (proj / "notes.txt").read_text() == "readable"


@mac
def test_env_files_cannot_be_read_but_other_files_can(proj):
    assert run(f"print(open({str(proj / 'notes.txt')!r}).read())", proj, []).stdout.strip() == "readable"
    bad = run(f"open({str(proj / '.env')!r}).read()", proj, [])
    assert bad.returncode != 0 and "PermissionError" in bad.stderr


@mac
def test_the_kaggle_credentials_folder_cannot_be_read(proj):
    home = os.path.expanduser("~/.kaggle")
    if not os.path.isdir(home):
        pytest.skip("no ~/.kaggle on this machine")
    bad = run(f"import os; os.listdir({home!r})", proj, [])
    assert bad.returncode != 0 and "PermissionError" in bad.stderr


def test_without_a_sandbox_it_refuses_unless_explicitly_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("AGENTSHIELD_ALLOW_UNSANDBOXED", raising=False)
    with pytest.raises(SandboxUnavailable):
        sandbox_command(["python", "x.py"], tmp_path, [])
    monkeypatch.setenv("AGENTSHIELD_ALLOW_UNSANDBOXED", "1")
    assert sandbox_command(["python", "x.py"], tmp_path, []) == ["python", "x.py"]


def test_paths_that_could_break_out_of_the_profile_are_rejected(tmp_path):
    for bad in ('/tmp/a"b', "/tmp/a\\b", "/tmp/a\nb"):
        with pytest.raises(ValueError):
            sandbox_command(["python"], tmp_path, [bad], allow_unsandboxed=False) if sys.platform == "darwin" else (_ for _ in ()).throw(ValueError)
