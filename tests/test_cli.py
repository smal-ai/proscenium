import fcntl
import json
import os
import stat
import subprocess
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from proscenium import cli, control
from proscenium import operator as operator_config
from proscenium.__main__ import main as module_main
from proscenium.app import create_app
from proscenium.configuration import APP_ROOT, Settings
from proscenium.control import ControlProtocolError
from proscenium.operator import diagnostics, launch, service
from proscenium.services import Services


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail(f"Unexpected subprocess or network probe: {args!r}")

    monkeypatch.setattr(cli, "run", forbidden)
    monkeypatch.setattr(httpx, "stream", forbidden)


@pytest.fixture
def config(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "unused-data"))
    path = xdg / "proscenium/config.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "data_directory": str(tmp_path / "data"),
                "frontend_directory": str(tmp_path / "frontend"),
                "free_space_reserve_bytes": 0,
            }
        )
    )
    path.chmod(0o600)
    return path


def _edit(config, **changes):
    data = json.loads(config.read_text())
    config.write_text(json.dumps({**data, **changes}))


def _service(provider_id, endpoint, capabilities):
    return dict(
        provider_id=provider_id,
        endpoint=endpoint,
        capabilities=capabilities,
        model_id=None,
        available=None,
    )


@pytest.fixture
def live(config):
    settings = Settings.load(config)
    app = create_app(settings, manage_features=False, enable_control_socket=True)
    with TestClient(app, base_url=settings.origin) as browser:
        yield browser


@pytest.mark.parametrize("output", [[], ["--url-only"], ["--json"]])
def test_pair_outputs_and_valid_token(config, live, output, capsys, caplog):
    assert cli.main(["pair", "--config", str(config), *output]) == 0
    capture = capsys.readouterr()
    assert not capture.err
    if output == ["--json"]:
        payload = json.loads(capture.out)
        assert set(payload) == {"pairing_url", "expires_in_seconds"}
        assert payload["expires_in_seconds"] == 900
        url = payload["pairing_url"]
    elif output == ["--url-only"]:
        url = capture.out.rstrip("\n")
        assert url.startswith("http://")
    else:
        label = "Owner pairing (private, 15 minutes): "
        assert capture.out.startswith(label)
        url = capture.out.removeprefix(label).rstrip("\n")
    parts = urlsplit(url)
    origin = Settings.load(config).origin
    assert parts._replace(fragment="").geturl() == origin + "/"
    code = parse_qs(parts.fragment)["pair"][0]
    operation = str(uuid4())
    response = live.post(
        "/api/v1/owner/session",
        json={
            "schema_version": "1.0",
            "operation_id": operation,
            "client_nonce": str(uuid4()),
            "pairing_code": code,
        },
        headers={"Origin": origin, "Idempotency-Key": operation},
    )
    assert response.status_code == 201
    assert code not in caplog.text


def test_pair_offline_is_actionable_and_creates_nothing(config, capsys):
    directory = Settings.load(config).data_directory
    assert cli.main(["pair"]) == 1
    capture = capsys.readouterr()
    assert not capture.out
    assert str(directory / "control.sock") in capture.err
    assert "proscenium service start" in capture.err and "proscenium serve" in capture.err
    assert not directory.exists()


@pytest.mark.parametrize(
    "reply", [{"ok": False, "error": "secret"}, {"ok": True, "pairing_url": "secret"}, None]
)
def test_pair_protocol_failure_hides_private_reply(config, reply, monkeypatch, capsys):
    control = Mock(
        return_value=reply, side_effect=ControlProtocolError("secret") if reply is None else None
    )
    monkeypatch.setattr(cli, "control_request", control)
    assert cli.main(["pair"]) == 2
    capture = capsys.readouterr()
    assert not capture.out and capture.err and "secret" not in capture.err
    control.assert_called_once_with(
        Settings.load(config).data_directory / "control.sock", {"action": "pair"}
    )


@pytest.mark.parametrize(
    "address, expiry",
    [
        ("http://127.0.0.1:99999/", 900),
        ("http://127.0.0.1:abc/", 900),
        ("http://127.0.0.1:0/", 900),
        ("http://localhost:8765/", None),
        ("http://localhost:8765/", "900"),
        ("http://localhost:8765/", 900.0),
        ("http://localhost:8765/", True),
        ("http://localhost:8765/", 0),
        ("http://localhost:8765/", -1),
        ("http://user@localhost/", 900),
        ("http://localhost/?private=token", 900),
        ("http://example.invalid/", 900),
    ],
)
def test_pair_rejects_bad_port_expiry_and_url_without_leak(
    config, monkeypatch, capsys, address, expiry
):
    token = "private_pairing_token_" + "x" * 32
    url = address + "#pair=" + token
    reply = {"ok": True, "pairing_url": url}
    if expiry is not None:
        reply["expires_in_seconds"] = expiry
    monkeypatch.setattr(cli, "control_request", Mock(return_value=reply))
    assert cli.main(["pair", "--json"]) == 2
    capture = capsys.readouterr()
    assert not capture.out and "invalid pairing response" in capture.err
    assert token not in capture.err and url not in capture.err


def test_pair_validates_token_length_and_uses_server_expiry(config, monkeypatch, capsys):
    reply = {
        "ok": True,
        "pairing_url": "http://[::1]:8765#pair=too-short",
        "expires_in_seconds": 123,
    }
    monkeypatch.setattr(cli, "control_request", Mock(return_value=reply))
    assert cli.main(["pair", "--url-only"]) == 2
    capture = capsys.readouterr()
    assert not capture.out and "too-short" not in capture.err
    reply["pairing_url"] = "http://[::1]:8765#pair=" + "x" * 32
    assert cli.main(["pair", "--json"]) == 0
    capture = capsys.readouterr()
    assert not capture.err and json.loads(capture.out) == {
        key: value for key, value in reply.items() if key != "ok"
    }


@pytest.mark.parametrize("command", ["pair", "serve", "status"])
@pytest.mark.parametrize("kind", ["symlink", "missing", "directory"])
def test_explicit_config_requires_regular_file(config, tmp_path, capsys, command, kind):
    path = tmp_path / "refused.json"
    if kind == "symlink":
        path.symlink_to(config)
    elif kind == "directory":
        path.mkdir()
    assert cli.main([command, "--config", str(path)]) == 2
    capture = capsys.readouterr()
    assert not capture.out and "regular JSON file" in capture.err
    assert not Settings.load(config).data_directory.exists()


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("symlink", ["ancestor", "leaf"])
def test_pair_refuses_symlinks_in_explicit_and_default_config_paths(
    config, tmp_path, monkeypatch, capsys, explicit, symlink
):
    if symlink == "ancestor":
        alias = tmp_path / "alias-xdg"
        alias.symlink_to(config.parents[1], target_is_directory=True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(alias))
        requested = alias / "proscenium/config.json"
    else:
        copy = config.with_name("copy.json")
        config.rename(copy)
        config.symlink_to(copy)
        requested = config
    control = Mock()
    monkeypatch.setattr(cli, "control_request", control)
    assert cli.main(["pair", *(["--config", str(requested)] if explicit else [])]) == 2
    capture = capsys.readouterr()
    assert not capture.out and "without symlinks" in capture.err
    control.assert_not_called()


def test_implicit_config_default_only_for_absent_leaf_with_safe_parents(
    config, monkeypatch, capsys
):
    config.unlink()
    control = Mock(side_effect=cli.ControlUnavailableError)
    monkeypatch.setattr(cli, "control_request", control)
    assert cli.main(["pair"]) == 1
    assert "unused-data" in capsys.readouterr().err
    control.assert_called_once_with(Settings().data_directory / "control.sock", {"action": "pair"})
    control.reset_mock()
    config.parent.chmod(0o777)
    assert cli.main(["pair"]) == 2
    assert "Configuration" in capsys.readouterr().err
    control.assert_not_called()
    config.parent.rmdir()
    assert cli.main(["pair"]) == 2
    assert "parent directory does not exist" in capsys.readouterr().err
    control.assert_not_called()


def test_config_parses_only_the_open_fd(config, tmp_path, monkeypatch):
    read_fd = operator_config._open_safe_file_fd

    def replace_after_open(path):
        opened = read_fd(path)
        path.rename(path.with_name("original.json"))
        replacement = tmp_path / "replacement.json"
        replacement.write_text("not the validated configuration")
        path.symlink_to(replacement)
        return opened

    monkeypatch.setattr(operator_config, "_open_safe_file_fd", replace_after_open)
    settings, mode = operator_config.load_config(config)
    assert settings.data_directory == tmp_path / "data"
    assert stat.S_IMODE(mode) == 0o600 and config.is_symlink()


def test_config_read_is_bounded(config, capsys):
    config.write_bytes(b" " * (1024 * 1024 + 1))
    assert cli.main(["pair"]) == 2
    capture = capsys.readouterr()
    assert not capture.out and "1 MiB" in capture.err


@pytest.mark.parametrize("entry, prefix", [(module_main, []), (cli.main, ["serve"])])
@pytest.mark.parametrize("option", ["--p", "--pa"])
def test_serve_rejects_abbreviated_pair(entry, prefix, option, monkeypatch, capsys):
    serve = Mock()
    monkeypatch.setattr(cli if prefix else sys.modules[module_main.__module__], "serve", serve)
    with pytest.raises(SystemExit, match="^2$"):
        entry([*prefix, option])
    assert "unrecognised arguments" in capsys.readouterr().err.replace(
        "unrecognized", "unrecognised"
    )
    serve.assert_not_called()


@pytest.mark.parametrize("entry", [module_main, cli.main])
@pytest.mark.parametrize("pair", [False, True])
def test_serve_legacy_and_console_share_launch(config, tmp_path, monkeypatch, capsys, entry, pair):
    order = []
    settings = Settings.load(config)
    fake = Mock()
    fake.auth.issue_pairing.return_value = "fake-token"
    fake.close.side_effect = lambda: order.append("close")
    monkeypatch.setattr(
        launch, "validate_control_directory", lambda path, *, create: order.append((path, create))
    )
    create = Mock(side_effect=lambda _settings: (order.append("create"), fake)[1])
    monkeypatch.setattr(Services, "create", create)
    create_app = Mock(return_value=object())
    monkeypatch.setattr("proscenium.app.create_app", create_app)
    run = Mock(side_effect=lambda *_args, **_kwargs: order.append("run"))
    monkeypatch.setattr("uvicorn.run", run)
    frontend = tmp_path / "alternate-frontend"
    origin = "http://localhost:9999"
    args = ["--config", str(config), "--origin", origin, "--frontend-dir", str(frontend)]
    if pair:
        args.append("--pair")
    assert entry((["serve"] if entry is cli.main else []) + args) == (
        0 if entry is cli.main else None
    )
    assert order == [(settings.data_directory, True), "create", "run", "close"]
    effective = create.call_args.args[0]
    assert effective.origin == origin and effective.frontend_directory == frontend
    create_app.assert_called_once_with(effective, services=fake, manage_features=True)
    run.assert_called_once_with(create_app.return_value, host="localhost", port=9999, workers=1)
    capture = capsys.readouterr()
    assert not capture.err
    if pair:
        fake.auth.issue_pairing.assert_called_once_with()
        assert capture.out == f"Owner pairing (private, 15 minutes): {origin}/#pair=fake-token\n"
    else:
        fake.auth.issue_pairing.assert_not_called()
        assert not capture.out


def test_serve_closes_when_server_fails(config, monkeypatch):
    fake = Mock()
    monkeypatch.setattr(Services, "create", Mock(return_value=fake))
    monkeypatch.setattr("proscenium.app.create_app", Mock())
    monkeypatch.setattr("uvicorn.run", Mock(side_effect=RuntimeError("startup failed")))
    with pytest.raises(RuntimeError, match="startup failed"):
        cli.main(["serve", "--config", str(config)])
    fake.close.assert_called_once_with()


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").symlink_to(sys.executable)
    (root / "deployment").mkdir()
    (root / "deployment/proscenium.service.in").write_bytes(
        (APP_ROOT / "deployment/proscenium.service.in").read_bytes()
    )
    return root


@pytest.fixture
def runner(monkeypatch):
    fake = Mock(side_effect=_manager)
    monkeypatch.setattr(cli, "run", fake)
    return fake


def _show(unit):
    return [
        "systemctl",
        "--user",
        "show",
        "-p",
        "LoadState,FragmentPath",
        "-p",
        "DropInPaths,NeedDaemonReload,Id",
        unit,
    ]


def _show_also(unit):
    return ["systemctl", "--user", "show", "-p", "Also", unit]


def _manager(command, *, fragment=None, action_code=0, **_):
    if command[:3] == ["systemctl", "--user", "show"]:
        unit = command[-1]
        if command == _show_also(unit):
            return subprocess.CompletedProcess(command, 0, stdout="Also=\n")
        assert command == _show(unit)
        local = cli.unit_directory() / unit
        path = fragment if fragment is not None else local if local.exists() else None
        state = "loaded" if path is not None else "not-found"
        return subprocess.CompletedProcess(
            command, 0, stdout=f"LoadState={state}\nFragmentPath={path or ''}\nId={unit}\n"
        )
    return subprocess.CompletedProcess(command, action_code)


def _local_unit(name="proscenium.service"):
    local = cli.unit_directory() / name
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(
        "[Unit]\nDescription=Proscenium test\n[Service]\n; ignore me\nExecStart=/usr/bin/python3 -m proscenium\n"
    )
    return local


def _install(checkout, *args):
    return cli.main(["service", "install", "--checkout", str(checkout), *args])


def _calls(runner):
    return [call.args[0] for call in runner.call_args_list]


@pytest.fixture
def private_group(monkeypatch):
    uid, gid = os.getuid(), os.getgid()
    owner = SimpleNamespace(pw_uid=uid, pw_gid=gid, pw_name="operator")
    group = SimpleNamespace(gr_name=owner.pw_name, gr_mem=[])
    users = Mock(getpwuid=Mock(return_value=owner), getpwall=Mock(return_value=[owner]))
    groups = Mock(getgrgid=Mock(return_value=group))
    monkeypatch.setattr(control, "pwd", users)
    monkeypatch.setattr(control, "grp", groups)
    return group, users, groups


def _set_group_mode(directory, mode):
    os.chown(directory, -1, os.getgid())
    directory.chmod(mode)


def test_exclusive_private_group_accepts_checkout_config_and_user_units(
    config,
    checkout,
    runner,
    tmp_path,
    private_group,
    capsys,
):
    play = tmp_path / "play"
    play.mkdir()
    checkout = checkout.rename(play / checkout.name)
    directory = cli.unit_directory()
    directory.mkdir(parents=True)
    for writable in (play, config.parent, directory.parent, directory):
        _set_group_mode(writable, 0o775)

    settings, mode = operator_config.load_config(config)
    assert settings.data_directory == tmp_path / "data" and stat.S_IMODE(mode) == 0o600
    assert _install(checkout) == 0
    assert cli.main(["service", "status"]) == 0
    unit = directory / "proscenium.service"
    assert f"WorkingDirectory={checkout}\n" in unit.read_text()
    assert _calls(runner) == [
        _show(unit.name),
        ["systemctl", "--user", "daemon-reload"],
        _show(unit.name),
        ["systemctl", "--user", "status", unit.name],
    ]
    capsys.readouterr()
    unrelated = directory / "lmstudio.service"
    unrelated.write_text("[Service]\nExecStart=/usr/bin/lmstudio\n")
    runner.reset_mock()
    assert cli.main(["service", "status", "--unit", "lmstudio"]) == 2
    assert "not a verified Proscenium unit" in capsys.readouterr().err
    assert _calls(runner) == [_show(unrelated.name)]
    private_group[1].getpwuid.assert_called_with(os.getuid())
    private_group[1].getpwall.assert_called()
    private_group[2].getgrgid.assert_called_with(os.getgid())
    with pytest.raises(PermissionError):
        control.validate_control_directory(play)


@pytest.mark.parametrize("kind", ["exclusive", "supplementary", "primary", "world-writable"])
@pytest.mark.parametrize("site", ["config", "checkout", "unit", "external"])
def test_operator_group_write_safety(
    config, checkout, runner, tmp_path, private_group, capsys, kind, site
):
    group, users, _ = private_group
    if kind == "supplementary":
        group.gr_mem = ["other"]
    elif kind == "primary":
        users.getpwall.return_value.append(
            SimpleNamespace(pw_gid=os.getgid(), pw_uid=os.getuid() + 1)
        )
    mode = 0o777 if kind == "world-writable" else 0o775
    allow = kind == "exclusive"
    if site == "config":
        _set_group_mode(config.parent, mode)
        if allow:
            operator_config.load_config(config)
        else:
            with pytest.raises(
                operator_config.OperatorError, match="Configuration must be an accessible"
            ):
                operator_config.load_config(config)
        runner.assert_not_called()
        return
    if site == "checkout":
        parent = tmp_path / "play"
        parent.mkdir()
        checkout = checkout.rename(parent / checkout.name)
        _set_group_mode(parent, mode)
        code = _install(checkout)
        error = "--checkout must be a trusted existing directory"
    elif site == "unit":
        unit = _local_unit()
        _set_group_mode(unit.parent, mode)
        code = cli.main(["service", "status"])
        error = "unsafe or inaccessible systemd user unit directory"
    else:
        fragment = tmp_path / "other-systemd/studio.service"
        fragment.parent.mkdir()
        fragment.write_text("[Service]\nExecStart=/usr/bin/python3 -m proscenium\n")
        _set_group_mode(fragment.parent, mode)
        runner.side_effect = lambda command, **kwargs: _manager(
            command, fragment=fragment, **kwargs
        )
        code = cli.main(["service", "status", "--unit", "studio"])
        error = "loaded fragment is not a safely inspectable regular unit"
    assert code == (0 if allow else 2)
    if not allow:
        assert error in capsys.readouterr().err
        assert _calls(runner) == ([_show("studio.service")] if site == "external" else [])


@pytest.mark.parametrize("failure", [KeyError(), OSError()])
def test_private_group_lookup_failure_is_not_trusted(private_group, failure):
    _, users, _ = private_group
    users.getpwall.side_effect = failure
    assert not control._is_exclusive_user_group(os.getuid(), os.getgid())


def test_service_install_reload_enable_start(
    config, checkout, runner, tmp_path, capsys, monkeypatch
):
    units = tmp_path / "xdg/systemd/user"
    monkeypatch.setattr(cli, "unit_directory", lambda: units)
    alternate_config = tmp_path / "explicit.json"
    alternate_config.write_bytes(config.read_bytes())
    with_checkout = checkout.relative_to(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert (
        _install(
            with_checkout,
            "--config",
            "explicit.json",
            "--enable",
            "--start",
            "--unit",
            "studio.service",
        )
        == 0
    )
    unit = units / "studio.service"
    assert stat.S_IMODE(units.stat().st_mode) == 0o700
    assert stat.S_IMODE(unit.stat().st_mode) == 0o600
    text = unit.read_text()
    assert f"WorkingDirectory={checkout}\n" in text
    assert (
        f"ExecStart={checkout}/.venv/bin/python -m proscenium --config {alternate_config}" in text
    )
    assert f"--frontend-dir {checkout}/apps/frontend/dist" in text
    assert all(token not in text for token in ("--pair", "@CHECKOUT@", "@CONFIG_FILE@"))
    assert _calls(runner) == [
        _show("studio.service"),
        ["systemctl", "--user", "daemon-reload"],
        _show("studio.service"),
        ["systemctl", "--user", "enable", "studio.service"],
        _show("studio.service"),
        ["systemctl", "--user", "start", "studio.service"],
    ]
    capture = capsys.readouterr()
    assert "make build-frontend" in capture.err
    assert (
        str(unit) in capture.out
        and "proscenium pair" in capture.out
        and "proscenium service status" in capture.out
    )
    assert set(units.iterdir()) == {unit}


def test_service_install_requires_force_for_existing_unit(
    config, checkout, runner, capsys, monkeypatch
):
    unit = cli.unit_directory() / "proscenium.service"
    assert _install(checkout) == 0
    initial = unit.read_text()
    previous = initial + "\n# Existing verified unit\n"
    unit.write_text(previous)
    runner.reset_mock()
    assert _install(checkout) == 2
    assert "--force" in capsys.readouterr().err
    assert unit.read_text() == previous
    runner.assert_not_called()
    confirm = Mock(side_effect=AssertionError("verified Proscenium unit must not prompt"))
    monkeypatch.setattr(cli, "confirm_external", confirm)
    assert _install(checkout, "--force") == 0
    assert unit.read_text() == initial
    assert "not a verified Proscenium unit" not in capsys.readouterr().err
    confirm.assert_not_called()
    assert _calls(runner) == [_show(unit.name), ["systemctl", "--user", "daemon-reload"]]


def test_service_refuses_unconfirmed_overwrite_of_unrelated_local_unit(
    config, checkout, runner, capsys
):
    unit = _local_unit("lmstudio.service")
    previous = "[Service]\nExecStart=/usr/bin/lmstudio\n"
    unit.write_text(previous)
    assert _install(checkout, "--unit", "lmstudio", "--force") == 2
    assert "not a verified Proscenium unit" in capsys.readouterr().err
    assert unit.read_text() == previous
    assert _calls(runner) == [_show(unit.name)]


def test_service_install_refuses_shadowing_external_unit(
    config, checkout, runner, tmp_path, capsys
):
    external = tmp_path / "other-systemd/comfyui-local.service"
    external.parent.mkdir()
    external.write_text("[Service]\nExecStart=/usr/bin/comfyui\n")
    runner.side_effect = lambda command, **kwargs: _manager(command, fragment=external, **kwargs)
    assert _install(checkout, "--unit", "comfyui-local") == 2
    error = capsys.readouterr().err
    assert "not a verified Proscenium unit" in error and "Re-run with --force" in error
    assert _calls(runner) == [_show(external.name)]
    assert list(cli.unit_directory().iterdir()) == []
    assert "ExecStart=/usr/bin/comfyui" in external.read_text()


def test_service_no_force_does_not_clobber_a_racing_install(
    config, checkout, runner, monkeypatch, capsys
):
    directory = cli.unit_directory()
    unit = directory / "proscenium.service"
    link = os.link

    def race(source, destination, **kwargs):
        assert destination == unit.name
        temporary = directory / source
        assert temporary.is_file() and stat.S_IMODE(temporary.stat().st_mode) == 0o600
        assert "-m proscenium --config" in temporary.read_text()
        unit.write_bytes(b"racing unit, leave untouched")
        return link(source, destination, **kwargs)

    monkeypatch.setattr(service.os, "link", race)
    assert _install(checkout) == 2
    assert "already exists" in capsys.readouterr().err
    assert unit.read_bytes() == b"racing unit, leave untouched"
    assert list(directory.iterdir()) == [unit]
    assert _calls(runner) == [_show(unit.name)]


@pytest.mark.parametrize(
    "target",
    ["unit", "user directory", "config", "checkout", "checkout ancestor", "config ancestor"],
)
def test_service_install_refuses_symlinks(config, checkout, runner, tmp_path, target):
    real = tmp_path / "real"
    units = cli.unit_directory()
    args = []
    if target == "unit":
        units.mkdir(parents=True)
        real.write_text("untouched")
        (units / "proscenium.service").symlink_to(real)
    elif target == "user directory":
        units.parent.mkdir()
        real.mkdir()
        units.symlink_to(real)
    elif target == "config":
        real.symlink_to(config)
        args = ["--config", str(real)]
    elif target == "checkout":
        real.symlink_to(checkout)
        checkout = real
    elif target == "checkout ancestor":
        real.symlink_to(checkout.parent)
        checkout = real / checkout.name
    else:
        real.symlink_to(config.parent)
        args = ["--config", str(real / config.name)]
    assert _install(checkout, "--force", *args) == 2
    runner.assert_not_called()
    if target == "unit":
        assert real.read_text() == "untouched"
    elif target == "user directory":
        assert not list(real.iterdir())


def test_service_install_refuses_sticky_tmp_as_checkout_leaf(config, runner, capsys):
    assert _install(Path("/tmp")) == 2
    assert "--checkout must be a trusted existing directory" in capsys.readouterr().err
    runner.assert_not_called()


@pytest.mark.parametrize("part", [".venv", ".venv/bin"])
@pytest.mark.parametrize("kind", ["symlink", "world-write"])
def test_service_install_refuses_unsafe_virtualenv_directories(
    config, checkout, runner, capsys, part, kind
):
    directory = checkout / part
    if kind == "world-write":
        directory.chmod(0o1777)
    else:
        original = directory.rename(directory.with_name(directory.name + "-original"))
        directory.symlink_to(original, target_is_directory=True)
    assert _install(checkout) == 2
    assert "trusted .venv and .venv/bin directories without symlinks" in capsys.readouterr().err
    runner.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    [
        "uv-style",
        "owner-executable",
        "private-group",
        "world-write",
        "shared-group",
        "not-executable",
        pytest.param(
            "other-executable",
            marks=pytest.mark.skipif(os.geteuid() == 0, reason="root can execute via other bits"),
        ),
        "directory",
        "missing",
    ],
)
def test_service_install_validates_uv_interpreter_target(
    config, checkout, runner, tmp_path, private_group, capsys, kind
):
    python = checkout / ".venv/bin/python"
    python.unlink()
    installation = tmp_path / "uv/python/cpython-3.12/bin"
    installation.mkdir(parents=True)
    target = installation / "python3.12"
    if kind == "directory":
        target.mkdir()
    elif kind != "missing":
        target.write_bytes(b"not executed by the test")
        target.chmod(
            {
                "world-write": 0o777,
                "not-executable": 0o644,
                "other-executable": 0o601,
                "owner-executable": 0o700,
            }.get(kind, 0o755)
        )
        if kind in {"private-group", "shared-group"}:
            os.chown(target, -1, os.getgid())
            target.chmod(0o775)
        if kind == "shared-group":
            private_group[0].gr_mem.append("other")
    python.symlink_to(target)
    if kind in {"uv-style", "private-group", "owner-executable"}:
        assert _install(checkout) == 0
        assert (cli.unit_directory() / "proscenium.service").is_file()
    else:
        assert _install(checkout) == 2
        assert "trusted executable regular file" in capsys.readouterr().err
        runner.assert_not_called()


@pytest.mark.parametrize("location", ["executable parent", "intermediate symlink parent"])
@pytest.mark.parametrize("mode", ["private-group", "shared-group", "world-writable", "sticky"])
def test_checkout_validates_every_interpreter_directory(
    config,
    checkout,
    runner,
    tmp_path,
    private_group,
    capsys,
    location,
    mode,
):
    parent = tmp_path / "uv-installation/bin"
    parent.mkdir(parents=True)
    executable = parent / "python3.12"
    executable.write_text("not executed by the test")
    executable.chmod(0o755)
    python = checkout / ".venv/bin/python"
    python.unlink()
    if location == "executable parent":
        directory = parent
        target = executable
    else:
        directory = tmp_path / "aliases"
        directory.mkdir()
        (directory / "current").symlink_to("../uv-installation", target_is_directory=True)
        target = directory / "current/bin/python3.12"
    _set_group_mode(directory, {"world-writable": 0o777, "sticky": 0o1777}.get(mode, 0o775))
    if mode == "shared-group":
        private_group[0].gr_mem.append("other")
    python.symlink_to(target)

    if mode == "private-group":
        assert _install(checkout) == 0
        assert (cli.unit_directory() / "proscenium.service").is_file()
    else:
        assert _install(checkout) == 2
        assert "through trusted directories" in capsys.readouterr().err
        runner.assert_not_called()


@pytest.mark.parametrize("links", [40, 41])
def test_checkout_bounds_all_interpreter_symlink_hops(config, checkout, runner, links):
    python = checkout / ".venv/bin/python"
    python.unlink()
    target = python.with_name("python3.12")
    target.write_text("not executed by the test")
    target.chmod(0o755)
    for index in range(links - 1):
        alias = target.with_name(f"alias-{index}")
        alias.symlink_to(target.name)
        target = alias
    python.symlink_to(target.name)
    assert _install(checkout) == (0 if links == 40 else 2)


def test_checkout_accepts_trusted_usr_merge_interpreter_path(config, checkout, runner):
    python = checkout / ".venv/bin/python"
    python.unlink()
    python.symlink_to("/bin/sh")
    assert _install(checkout) == 0


@pytest.mark.parametrize("name", ["../evil", "bad name", "a/b", "", "a..b", ".service", "a" * 65])
def test_service_rejects_invalid_unit(config, checkout, runner, name):
    with pytest.raises(SystemExit, match="^2$"):
        _install(checkout, "--unit", name)
    runner.assert_not_called()


@pytest.mark.parametrize("option", ["--checkout", "--config"])
@pytest.mark.parametrize("bad", ["%n", "\n", "\r", "\0", " ", '"', "'", "\\", "$PATH"])
def test_service_install_refuses_unsafe_paths(
    config, checkout, runner, tmp_path, capsys, option, bad
):
    assert _install(checkout, option, str(tmp_path / f"bad{bad}path")) == 2
    assert "unsafe characters" in capsys.readouterr().err
    runner.assert_not_called()


def test_service_install_refuses_bad_json(config, checkout, runner, capsys):
    config.write_text("not JSON")
    assert _install(checkout) == 2
    assert "validate the configuration" in capsys.readouterr().err
    runner.assert_not_called()
    assert not cli.unit_directory().exists()


def test_service_install_does_not_read_custom_checkout_template(config, checkout, runner):
    template = checkout / "deployment/proscenium.service.in"
    template.write_text(
        "[Service]\nExecStartPre=/usr/bin/comfyui\nExecStart=/usr/bin/python -m proscenium --pair\n"
    )
    assert _install(checkout) == 0
    unit = (cli.unit_directory() / "proscenium.service").read_text()
    assert unit == (APP_ROOT / "deployment/proscenium.service.in").read_text().replace(
        "@CHECKOUT@", str(checkout)
    ).replace("@CONFIG_FILE@", str(config))


def test_service_install_verifies_rendered_shipped_template(
    config, checkout, runner, tmp_path, monkeypatch, capsys
):
    root = tmp_path / "shipped"
    source = root / "deployment/proscenium.service.in"
    source.parent.mkdir(parents=True)
    original = (APP_ROOT / "deployment/proscenium.service.in").read_text()
    source.write_text(original.replace("Type=simple", "Type=simple\nExecStop=/usr/bin/comfyui"))
    monkeypatch.setattr(service, "APP_ROOT", root)
    assert _install(checkout) == 2
    assert "unsafe shipped Proscenium unit template" in capsys.readouterr().err
    runner.assert_not_called()
    assert not cli.unit_directory().exists()


@pytest.mark.parametrize("inactive", [False, True])
def test_service_uninstall_only_removes_its_unit(config, checkout, runner, inactive):
    assert _install(checkout) == 0
    runner.reset_mock()
    if inactive:

        def not_enabled(command, **kwargs):
            if command[2] == "show":
                return _manager(command, **kwargs)
            code, error = {
                "stop": (5, "Unit proscenium.service not loaded."),
                "disable": (1, "Unit not enabled"),
            }.get(command[2], (0, ""))
            return subprocess.CompletedProcess(command, code, stderr=error)

        runner.side_effect = not_enabled
    units = cli.unit_directory()
    unrelated = units / "other.service"
    unrelated.write_text("unrelated unit")
    data = Settings.load(config).data_directory
    data.mkdir()
    (data / "keep").touch()
    assert cli.main(["service", "uninstall"]) == 0
    assert set(units.iterdir()) == {unrelated}
    assert config.is_file() and (data / "keep").is_file()
    assert _calls(runner) == [
        _show("proscenium.service"),
        ["systemctl", "--user", "stop", "proscenium.service"],
        _show("proscenium.service"),
        ["systemctl", "--user", "disable", "proscenium.service"],
        ["systemctl", "--user", "daemon-reload"],
    ]


@pytest.mark.parametrize("kind", ["unrelated", "symlink", "directory"])
def test_service_uninstall_refuses_other_units(config, runner, tmp_path, capsys, kind):
    units = cli.unit_directory()
    units.mkdir(parents=True)
    unit = units / "lmstudio.service"
    if kind == "unrelated":
        unit.write_text("[Service]\n# proscenium\nExecStart=/bin/lmstudio -m proscenium\n")
    elif kind == "symlink":
        unit.symlink_to(tmp_path / "missing")
    else:
        unit.mkdir()
    assert cli.main(["service", "uninstall", "--unit", "lmstudio"]) == 2
    assert "Refusing lmstudio.service" in capsys.readouterr().err
    assert os.path.lexists(unit)
    assert _calls(runner) == [_show(unit.name)]


def test_service_uninstall_retains_unit_on_real_stop_failure(config, checkout, runner):
    assert _install(checkout) == 0
    runner.reset_mock()

    def stop_failure(command, **kwargs):
        if command[2] == "show":
            return _manager(command, **kwargs)
        return subprocess.CompletedProcess(command, 9, stderr="Failed to connect to bus")

    runner.side_effect = stop_failure
    assert cli.main(["service", "uninstall"]) == 9
    assert (cli.unit_directory() / "proscenium.service").is_file()
    assert _calls(runner) == [
        _show("proscenium.service"),
        ["systemctl", "--user", "stop", "proscenium.service"],
    ]


@pytest.mark.parametrize("action", ["start", "stop", "restart", "status"])
def test_service_control_passthrough(config, runner, action):
    _local_unit("studio.service")
    runner.side_effect = lambda command, **kwargs: _manager(command, action_code=7, **kwargs)
    assert cli.main(["service", "--unit", "studio", action]) == 7
    assert _calls(runner) == [
        _show("studio.service"),
        ["systemctl", "--user", action, "studio.service"],
    ]


@pytest.mark.parametrize("follow", [[], ["--follow"]])
def test_service_logs_passthrough(config, runner, follow):
    _local_unit()
    runner.side_effect = lambda command, **kwargs: _manager(command, action_code=4, **kwargs)
    assert cli.main(["service", "logs", "--lines", "50", *follow]) == 4
    assert _calls(runner) == [
        _show("proscenium.service"),
        [
            "journalctl",
            "--user",
            "-u",
            "proscenium.service",
            "-n",
            "50",
            "--no-pager",
            *(["-f"] if follow else []),
        ],
    ]


@pytest.mark.parametrize("args", [["logs", "--follow"], ["status"]])
def test_service_read_only_command_does_not_keep_unit_directory_locked(config, runner, args):
    unit = _local_unit()

    def inspect_lock(command, **kwargs):
        if command == _show(unit.name):
            return _manager(command, **kwargs)
        if args[0] == "logs":
            assert command[0] == "journalctl" and command[-1] == "-f"
        else:
            assert command == ["systemctl", "--user", "status", unit.name]
        competing_fd = os.open(unit.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(competing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(competing_fd)
        return subprocess.CompletedProcess(command, 0)

    runner.side_effect = inspect_lock
    assert cli.main(["service", *args]) == 0
    assert len(_calls(runner)) == 2


@pytest.mark.parametrize("state", ["not-found", "loaded"])
@pytest.mark.parametrize("action", ["start", "stop", "restart", "uninstall", "status", "logs"])
def test_service_requires_loaded_fragment_only_for_mutations(config, runner, capsys, state, action):
    unit = _local_unit()

    def show_empty(command, **kwargs):
        if command == _show(unit.name):
            return subprocess.CompletedProcess(
                command, 0, stdout=f"LoadState={state}\nFragmentPath=\n"
            )
        return _manager(command, **kwargs)

    runner.side_effect = show_empty
    read_only = action in {"status", "logs"}
    assert cli.main(["service", action]) == (0 if read_only else 2)
    assert unit.is_file()
    if read_only:
        assert len(_calls(runner)) == 2
    else:
        assert "systemctl --user daemon-reload" in capsys.readouterr().err
        assert _calls(runner) == [_show(unit.name)]


def test_service_rejects_replaced_inode_at_same_fragment_path(config, runner, capsys):
    unit = _local_unit()

    def changed_directory(command, **kwargs):
        assert command == _show(unit.name)
        original = unit.read_text()
        unit.parent.rename(unit.parent.with_name("previous-user"))
        unit.parent.mkdir()
        unit.write_text(original)
        return _manager(command, **kwargs)

    runner.side_effect = changed_directory
    assert cli.main(["service", "start"]) == 2
    assert "loaded fragment does not match the local unit file" in capsys.readouterr().err
    assert _calls(runner) == [_show(unit.name)]


def test_service_uninstall_rechecks_loaded_fragment_before_disable(config, runner, capsys):
    unit = _local_unit()
    show_count = 0

    def vanished_after_stop(command, **kwargs):
        nonlocal show_count
        if command == _show(unit.name):
            show_count += 1
            if show_count == 2:
                return subprocess.CompletedProcess(command, 0, stdout="LoadState=not-found\n")
        return _manager(command, **kwargs)

    runner.side_effect = vanished_after_stop
    assert cli.main(["service", "uninstall"]) == 2
    assert "systemctl --user daemon-reload" in capsys.readouterr().err
    assert unit.is_file()
    assert _calls(runner) == [
        _show(unit.name),
        ["systemctl", "--user", "stop", unit.name],
        _show(unit.name),
    ]


@pytest.mark.parametrize("action", ["start", "stop", "restart", "status", "logs", "uninstall"])
@pytest.mark.parametrize("location", ["local", "loaded external", "shadowed"])
def test_all_service_actions_reject_unrelated_units(
    config, runner, tmp_path, capsys, action, location
):
    name = "lmstudio.service" if location == "local" else "comfyui-local.service"
    if location == "local":
        unrelated = _local_unit(name)
    else:
        unrelated = tmp_path / "external" / name
        unrelated.parent.mkdir()
        runner.side_effect = lambda command, **kwargs: _manager(
            command, fragment=unrelated, **kwargs
        )
        if location == "shadowed":
            _local_unit(name)
    original = "[Unit]\nDescription=Not proscenium\n[Service]\nExecStart=/usr/bin/comfyui\n"
    unrelated.write_text(original)
    assert cli.main(["service", action, "--unit", name]) == 2
    assert f"Refusing {name}" in capsys.readouterr().err
    assert _calls(runner) == [_show(name)]
    assert unrelated.read_text() == original


@pytest.mark.parametrize("action", ["status", "logs", "start", "stop", "restart", "uninstall"])
def test_verified_external_fragment_is_only_allowed_for_read_only_actions(
    config, runner, tmp_path, action
):
    name = "proscenium-local.service"
    external = tmp_path / name
    external.write_text("[Service]\nExecStart=/usr/bin/python -m proscenium\n")
    runner.side_effect = lambda command, **kwargs: _manager(command, fragment=external, **kwargs)
    read_only = action in {"status", "logs"}
    assert cli.main(["service", action, "--unit", name]) == (0 if read_only else 2)
    assert _calls(runner)[0] == _show(name)
    assert len(_calls(runner)) == (2 if read_only else 1)
    assert external.is_file()


@pytest.mark.parametrize(
    "metadata", ["DropInPaths=/unsafe/exec.conf", "NeedDaemonReload=yes", "Id=lmstudio.service"]
)
def test_service_refuses_unverified_effective_metadata(config, runner, metadata):
    unit = _local_unit()
    runner.side_effect = lambda command, **_: subprocess.CompletedProcess(
        command,
        0,
        stdout=f"LoadState=loaded\nFragmentPath={unit}\n{metadata}\n",
    )
    assert cli.main(["service", "start"]) == 2
    assert _calls(runner) == [_show(unit.name)]


@pytest.mark.parametrize(
    "directive",
    [
        "ExecStartPre",
        "ExecStartPost",
        "ExecStop",
        "ExecStopPost",
        "ExecReload",
        "ExecCondition",
        "Wants",
        "Also",
    ],
)
def test_unit_verifier_refuses_hooks_and_related_service_directives(directive):
    safe = "[Service]\nExecStart='/usr/bin/python3.12' -m proscenium --config /test/config.json\n"
    assert service._is_proscenium_unit_text(safe)
    assert not service._is_proscenium_unit_text(safe + f"{directive}=/usr/bin/comfyui\n")


@pytest.mark.parametrize(
    "flag", ["--pair", "--p", "--pa", "--pair=true", "--conf test", "service start"]
)
def test_unit_verifier_refuses_unsafe_or_unrecognised_launch_arguments(flag):
    assert not service._is_proscenium_unit_text(
        f"[Service]\nExecStart=/usr/bin/python -m proscenium {flag}\n"
    )


@pytest.mark.parametrize("action", ["--enable", "--start"])
def test_service_install_does_not_mutate_if_unit_is_not_loaded_after_reload(
    config, checkout, runner, capsys, action
):
    unit = cli.unit_directory() / "proscenium.service"

    def never_loaded(command, **kwargs):
        if command == _show(unit.name):
            return subprocess.CompletedProcess(
                command, 0, stdout="LoadState=not-found\nFragmentPath=\n"
            )
        return _manager(command, **kwargs)

    runner.side_effect = never_loaded
    assert _install(checkout, action) == 2
    error = capsys.readouterr().err
    assert f"Unit written to {unit}" in error and "systemctl --user daemon-reload" in error
    assert unit.is_file()
    assert _calls(runner) == [
        _show(unit.name),
        ["systemctl", "--user", "daemon-reload"],
        _show(unit.name),
    ]


@pytest.mark.parametrize(
    "directive", ["WorkingDirectory=%h", "WorkingDirectory=$HOME", "Description=Studio %i"]
)
def test_unit_verifier_refuses_substitutions_in_active_directive_values(directive):
    section = "[Unit]" if directive.startswith("Description=") else "[Service]"
    launch = "[Service]\nExecStart=/usr/bin/python -m proscenium\n"
    assert not service._is_proscenium_unit_text(f"{launch}{section}\n{directive}\n")
    assert service._is_proscenium_unit_text(f"{launch}{section}\n# {directive}\n")


def test_service_install_missing_systemctl_at_shadow_check_writes_no_unit(
    config, checkout, runner, capsys
):
    runner.side_effect = FileNotFoundError()
    assert _install(checkout, "--start") == 2
    unit = cli.unit_directory() / "proscenium.service"
    assert not unit.exists()
    capture = capsys.readouterr()
    assert not capture.out
    assert "systemctl was not found" in capture.err and "No unit file was written" in capture.err
    assert _calls(runner) == [_show(unit.name)]


def test_service_install_missing_systemctl_at_reload_reports_written_unit(
    config, checkout, runner, capsys
):
    def disappears(command, **kwargs):
        if command == ["systemctl", "--user", "daemon-reload"]:
            raise FileNotFoundError
        return _manager(command, **kwargs)

    runner.side_effect = disappears
    assert _install(checkout, "--start") == 1
    unit = cli.unit_directory() / "proscenium.service"
    assert unit.is_file() and service._is_proscenium_unit_text(unit.read_text())
    capture = capsys.readouterr()
    assert not capture.out
    assert (
        f"Unit written to {unit}" in capture.err
        and "systemctl --user daemon-reload could not run" in capture.err
    )
    assert _calls(runner) == [_show(unit.name), ["systemctl", "--user", "daemon-reload"]]


def test_service_install_refuses_failed_systemd_shadow_check(config, checkout, runner):
    runner.side_effect = lambda command, **_: subprocess.CompletedProcess(
        command, 1, stderr="no session bus"
    )
    assert _install(checkout) == 2
    assert not (cli.unit_directory() / "proscenium.service").exists()
    assert _calls(runner) == [_show("proscenium.service")]


@pytest.mark.parametrize("flags", [[], ["--force"], ["--force", "--yes"]])
def test_service_install_refuses_stale_loaded_fragment_without_publication(
    config, checkout, runner, tmp_path, capsys, flags
):
    fragment = tmp_path / "other-systemd/proscenium.service"
    fragment.parent.mkdir()
    disk_text = "[Service]\nExecStart=/usr/bin/python -m proscenium\n"
    fragment.write_text(disk_text)
    runner.side_effect = lambda command, **_: subprocess.CompletedProcess(
        command,
        0,
        stdout=f"LoadState=loaded\nFragmentPath={fragment}\nNeedDaemonReload=yes\n",
    )
    assert _install(checkout, *flags) == 2
    assert "systemctl --user daemon-reload" in capsys.readouterr().err
    assert not (cli.unit_directory() / fragment.name).exists()
    assert fragment.read_text() == disk_text
    assert _calls(runner) == [_show(fragment.name)]


_EXTERNAL_SOURCE = (
    "[Unit]\nDescription=Independent service\n[Service]\nExecStart=/usr/bin/comfyui\n"
)


class _TerminalInput(StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize(
    "action", ["start", "stop", "restart", "status", "logs", "uninstall", "install"]
)
@pytest.mark.parametrize(
    "response", ["no force", "non-terminal", "declined", "interactive yes", "flag yes"]
)
def test_explicit_confirmation_for_external_local_units(
    config, checkout, runner, monkeypatch, capsys, action, response
):
    name = "comfyui-local.service"
    unit = _local_unit(name)
    unit.write_text(_EXTERNAL_SOURCE)
    before = unit.stat()
    monkeypatch.setattr(
        sys,
        "stdin",
        {
            "non-terminal": StringIO("y\n"),
            "declined": _TerminalInput("yeah\n"),
            "interactive yes": _TerminalInput(" y \n"),
        }.get(response, StringIO("")),
    )
    opts = [] if response == "no force" else ["--force"]
    if response == "flag yes":
        opts.append("--yes")
        confirm = Mock(side_effect=AssertionError("--yes must not prompt"))
        monkeypatch.setattr(cli, "confirm_external", confirm)
    if action == "install":
        code = _install(checkout, "--unit", name, *opts)
    else:
        code = cli.main(["service", action, "--unit", name, *opts])
    result = capsys.readouterr()
    allowed = response in {"interactive yes", "flag yes"}
    assert code == (0 if allowed else 2)
    assert (
        "not a verified Proscenium unit" in result.err
        and name in result.err
        and str(unit) in result.err
    )
    operation = "overwrite" if action == "install" else action
    if response != "no force":
        assert f"Warning: {operation} on {name}" in result.err
    if response in {"declined", "interactive yes"}:
        assert f"Proceed with {operation} on external unit {name}? [y/N]: " in result.err
    if not allowed:
        assert "--force" in result.err
        assert unit.read_text() == _EXTERNAL_SOURCE and unit.stat().st_ino == before.st_ino
        assert _calls(runner) == (
            [] if action == "install" and response == "no force" else [_show(name)]
        )
        return
    inspected = [_show(name)] * (2 if response == "interactive yes" else 1)
    if action == "install":
        expected = (
            (APP_ROOT / "deployment/proscenium.service.in")
            .read_text()
            .replace("@CHECKOUT@", str(checkout))
            .replace("@CONFIG_FILE@", str(config))
        )
        assert unit.read_text() == expected and unit.stat().st_ino != before.st_ino
        assert stat.S_IMODE(unit.stat().st_mode) == 0o600
        assert _calls(runner) == [*inspected, ["systemctl", "--user", "daemon-reload"]]
    elif action == "uninstall":
        assert not unit.exists()
        assert _calls(runner) == [
            *inspected,
            _show_also(name),
            ["systemctl", "--user", "stop", name],
            _show(name),
            _show_also(name),
            ["systemctl", "--user", "disable", name],
            ["systemctl", "--user", "daemon-reload"],
        ]
    else:
        expected = (
            ["journalctl", "--user", "-u", name, "-n", "50", "--no-pager"]
            if action == "logs"
            else ["systemctl", "--user", action, name]
        )
        assert _calls(runner) == [*inspected, expected]
        assert unit.read_text() == _EXTERNAL_SOURCE


@pytest.mark.parametrize(
    "source",
    ["local", "local multiline", "loaded", "missing property", "show failure", "malformed local"],
)
def test_force_uninstall_never_disables_unconfirmed_companion_units(config, runner, capsys, source):
    unit = _local_unit("lmstudio.service")
    suffix = {
        "local": "[Install]\nAlso=companion.service\n",
        "local multiline": '[Install]\nAlso \\\n # skip comment\n = \\\n "companion.service" \\\n other.service\n',
        "malformed local": '[Install]\nAlso="companion.service\n',
    }.get(source, "")
    original = _EXTERNAL_SOURCE + suffix
    unit.write_text(original)

    def observe_also(command, **kwargs):
        if command == _show_also(unit.name):
            if source == "loaded":
                return subprocess.CompletedProcess(command, 0, stdout="Also=companion.service\n")
            if source == "missing property":
                return subprocess.CompletedProcess(command, 0, stdout="")
            if source == "show failure":
                return subprocess.CompletedProcess(command, 1)
        return _manager(command, **kwargs)

    runner.side_effect = observe_also
    assert cli.main(["service", "uninstall", "--unit", unit.name, "--force", "--yes"]) == 2
    output = capsys.readouterr().err
    assert "systemctl --user" in output and "Also=" in output
    if source in {"local", "local multiline", "loaded"}:
        assert "companion.service" in output
    if source == "local multiline":
        assert "other.service" in output
    assert _calls(runner) == [_show(unit.name), _show_also(unit.name)]
    assert unit.read_text() == original


@pytest.mark.parametrize("answer", [None, "YES\n", "no\n"])
def test_force_install_can_shadow_external_fragment_only_after_confirmation(
    config, checkout, runner, tmp_path, monkeypatch, capsys, answer
):
    fragment = tmp_path / "alternate-systemd/lmstudio.service"
    fragment.parent.mkdir()
    fragment.write_text(_EXTERNAL_SOURCE)
    runner.side_effect = lambda command, **kwargs: _manager(command, fragment=fragment, **kwargs)
    if answer is not None:
        monkeypatch.setattr(sys, "stdin", _TerminalInput(answer))
    extra = ["--yes"] if answer is None else []
    result = _install(checkout, "--unit", fragment.name, "--force", *extra)
    assert f"Warning: shadow on {fragment.name} ({fragment})" in capsys.readouterr().err
    unit = cli.unit_directory() / fragment.name
    assert fragment.read_text() == _EXTERNAL_SOURCE
    if answer == "no\n":
        assert result == 2 and not unit.exists()
        assert _calls(runner) == [_show(fragment.name)]
    else:
        assert result == 0 and service._is_proscenium_unit_text(unit.read_text())
        assert stat.S_IMODE(unit.stat().st_mode) == 0o600
        inspected = [_show(fragment.name)] * (1 if answer is None else 2)
        assert _calls(runner) == [*inspected, ["systemctl", "--user", "daemon-reload"]]


@pytest.mark.parametrize("action", ["restart", "uninstall"])
def test_force_only_operates_on_exact_loaded_external_fragment(config, runner, tmp_path, action):
    fragment = tmp_path / "alternate-systemd/lmstudio.service"
    fragment.parent.mkdir()
    fragment.write_text(_EXTERNAL_SOURCE)
    runner.side_effect = lambda command, **kwargs: _manager(command, fragment=fragment, **kwargs)
    assert cli.main(["service", action, "--unit", fragment.name, "--force", "--yes"]) == (
        0 if action == "restart" else 2
    )
    expected = [_show(fragment.name)]
    if action == "restart":
        expected.append(["systemctl", "--user", "restart", fragment.name])
    assert _calls(runner) == expected and fragment.read_text() == _EXTERNAL_SOURCE


@pytest.mark.parametrize("action", ["restart", "status", "logs", "install", "uninstall"])
def test_force_yes_never_bypasses_symlink_protection(config, checkout, runner, tmp_path, action):
    regular = tmp_path / "target.service"
    regular.write_text(_EXTERNAL_SOURCE)
    unit = _local_unit("lmstudio.service")
    unit.unlink()
    unit.symlink_to(regular)
    args = ["--unit", unit.name, "--force", "--yes"]
    result = (
        _install(checkout, *args) if action == "install" else cli.main(["service", action, *args])
    )
    assert result == 2 and unit.is_symlink() and regular.read_text() == _EXTERNAL_SOURCE
    assert _calls(runner) == ([] if action == "install" else [_show(unit.name)])


def test_external_confirmation_cannot_authorise_a_unit_changed_during_prompt(
    config, runner, monkeypatch, capsys
):
    unit = _local_unit("lmstudio.service")
    unit.write_text(_EXTERNAL_SOURCE)

    def confirm(prompt):
        unit.write_text(_EXTERNAL_SOURCE.replace("comfyui", "other-owner-work"))
        return True

    monkeypatch.setattr(cli, "confirm_external", confirm)
    assert cli.main(["service", "uninstall", "--unit", unit.name, "--force"]) == 2
    assert "unit file changed during the operation" in capsys.readouterr().err
    assert _calls(runner) == [_show(unit.name)] and unit.is_file()


@pytest.mark.parametrize("action", ["restart", "install"])
def test_external_confirmation_cannot_authorise_a_different_loaded_fragment(
    config, checkout, runner, tmp_path, monkeypatch, capsys, action
):
    unit = _local_unit("lmstudio.service")
    unit.write_text(_EXTERNAL_SOURCE)
    other = tmp_path / "other.service"
    other.write_text(_EXTERNAL_SOURCE)

    def confirm(prompt):
        runner.side_effect = lambda command, **kwargs: _manager(command, fragment=other, **kwargs)
        return True

    monkeypatch.setattr(cli, "confirm_external", confirm)
    args = ["--unit", unit.name, "--force"]
    code = (
        _install(checkout, *args) if action == "install" else cli.main(["service", action, *args])
    )
    assert code == 2 and "loaded fragment changed" in capsys.readouterr().err
    assert _calls(runner) == [_show(unit.name), _show(unit.name)]
    assert unit.read_text() == other.read_text() == _EXTERNAL_SOURCE


def test_yes_requires_force_even_when_target_is_proscenium(config, runner, capsys):
    _local_unit()
    assert cli.main(["service", "start", "--yes"]) == 2
    assert "--yes requires --force" in capsys.readouterr().err
    runner.assert_not_called()


@pytest.mark.parametrize("action", ["start", "stop", "restart", "uninstall"])
def test_force_yes_cannot_mutate_local_external_unit_without_loaded_fragment(
    config, runner, capsys, action
):
    unit = _local_unit("lmstudio.service")
    unit.write_text(_EXTERNAL_SOURCE)
    runner.side_effect = lambda command, **_: subprocess.CompletedProcess(
        command, 0, stdout="LoadState=not-found\n"
    )
    assert cli.main(["service", action, "--unit", unit.name, "--force", "--yes"]) == 2
    assert "systemctl --user daemon-reload" in capsys.readouterr().err
    assert _calls(runner) == [_show(unit.name)] and unit.read_text() == _EXTERNAL_SOURCE


@pytest.mark.parametrize("tool, action", [("systemctl", "start"), ("journalctl", "logs")])
def test_service_names_missing_executable(config, runner, capsys, tool, action):
    _local_unit()

    def missing(command, **kwargs):
        if command[0] == tool:
            raise FileNotFoundError
        return _manager(command, **kwargs)

    runner.side_effect = missing
    assert cli.main(["service", action]) == 1
    assert f"{tool} was not found" in capsys.readouterr().err


@pytest.mark.parametrize("lines", ["0", "-1", "10001", "invalid"])
def test_service_logs_rejects_bad_lines(config, runner, lines):
    with pytest.raises(SystemExit, match="^2$"):
        cli.main(["service", "logs", "--lines", lines])
    runner.assert_not_called()


def _snapshot_directory(directory):
    return {
        path.name: (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None)
        for path in directory.iterdir()
    }


def test_status_live_is_read_only(config, live, runner, monkeypatch, capsys):
    services = live.app.state.services
    settings = services.settings
    settings.frontend_directory.mkdir()
    (settings.frontend_directory / "index.html").touch()
    with services.registry.store.transaction() as tx:
        tx.put("registry", "active", {"project_id": "project-cli"})

    providers = [
        _service("comfy", "http://localhost:8188", ["image"]),
        _service("vision", "http://localhost:1234/api/v1/", ["vlm"]),
        _service("custom", "https://example.invalid", []),
    ]
    _edit(config, services=providers, runtime={"not_exposed": "private-token"})
    systemd = "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=12\nFragmentPath=/test/studio.service\nOther=private-token\n"
    runner.side_effect = lambda command, **_: subprocess.CompletedProcess(command, 0, systemd)
    http = Mock(side_effect=[200, 503, httpx.ReadTimeout("private-token")] * 2)
    monkeypatch.setattr(cli, "http_status", http)
    before = _snapshot_directory(settings.data_directory)

    assert cli.main(["status", "--unit", "studio", "--json"]) == 0
    capture = capsys.readouterr()
    assert not capture.err and "private-token" not in capture.out
    result = json.loads(capture.out)
    assert result["runtime"] == {
        "running": True,
        "pid": os.getpid(),
        "origin": settings.origin,
        "data_directory": str(settings.data_directory),
        "active_project_id": "project-cli",
        "owner_epoch": services.ownership.epoch,
        "frontend_ready": True,
    }
    assert result["systemd"] == {
        "unit": "studio.service",
        "available": True,
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "MainPID": "12",
        "FragmentPath": "/test/studio.service",
    }
    assert result["services"] == [
        {**{key: service[key] for key in ("provider_id", "endpoint", "capabilities")}, **result}
        for service, result in zip(
            providers,
            [
                {"reachable": True, "status_code": 200},
                {"reachable": False, "status_code": 503},
                {"reachable": False, "error": "Probe timed out"},
            ],
            strict=True,
        )
    ]
    systemd_command = [
        "systemctl",
        "--user",
        "show",
        "-p",
        "LoadState,ActiveState,SubState,MainPID,FragmentPath",
        "--",
        "studio.service",
    ]
    runner.assert_called_once_with(systemd_command, capture_output=True, text=True, timeout=2.0)
    assert _calls(http) == [
        "http://localhost:8188/system_stats",
        "http://localhost:1234/api/v1/models",
        "https://example.invalid/",
    ]

    assert cli.main(["status", "--unit", "studio"]) == 0
    capture = capsys.readouterr()
    assert not capture.err and "private-token" not in capture.out
    for expected in (
        "Runtime: running",
        "Active project: project-cli",
        "Frontend: ready",
        "Systemd: studio.service loaded/active/running",
        "HTTP 503",
        "Probe timed out",
    ):
        assert expected in capture.out
    assert _snapshot_directory(settings.data_directory) == before


@pytest.mark.parametrize(
    "problem", [FileNotFoundError(), subprocess.TimeoutExpired("systemctl", 2), None]
)
def test_status_stopped_and_systemd_unavailable(config, runner, capsys, problem):
    if problem is not None:
        runner.side_effect = problem
    else:
        runner.side_effect = lambda command, **_: subprocess.CompletedProcess(
            command, 1, stderr="no session bus"
        )
    assert cli.main(["status", "--json"]) == 0
    capture = capsys.readouterr()
    assert not capture.err
    result = json.loads(capture.out)
    assert result["runtime"] == {"running": False, "reason": "Control socket unavailable"}
    assert result["systemd"]["available"] is False
    assert result["services"] == []
    assert not Settings.load(config).data_directory.exists()


def test_http_probe_cannot_follow_redirects_or_use_ambient_credentials(monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=Mock(status_code=302))
    response.__exit__ = Mock(return_value=False)
    get = Mock(return_value=response)
    monkeypatch.setattr(httpx, "stream", get)
    assert diagnostics.http_status("http://localhost:1234/v1/models") == 302
    get.assert_called_once_with(
        "GET",
        "http://localhost:1234/v1/models",
        timeout=2.0,
        follow_redirects=False,
        trust_env=False,
    )


@pytest.fixture
def doctor_ready(config, runner, tmp_path, monkeypatch):
    root = tmp_path / "application"
    source = root / "apps/frontend/src"
    source.mkdir(parents=True)
    source_file = source / "app.ts"
    source_file.touch()
    monkeypatch.setattr(diagnostics, "APP_ROOT", root)
    tools = Mock(
        side_effect=lambda name: f"/test/bin/{name}" if not Path(name).is_absolute() else name
    )
    monkeypatch.setattr(cli.shutil, "which", tools)
    settings = Settings.load(config)
    settings.data_directory.mkdir()
    settings.frontend_directory.mkdir()
    index = settings.frontend_directory / "index.html"
    index.touch()
    os.utime(source_file, ns=(1, 1))
    return source_file, index, tools


def _doctor(capsys, *, config=None):
    args = ["--config", str(config)] if config is not None else []
    code = cli.main(["doctor", "--json", *args])
    capture = capsys.readouterr()
    assert not capture.err
    checks = json.loads(capture.out)["checks"]
    assert all(set(row) == {"name", "status", "required", "detail"} for row in checks)
    return code, {row["name"]: row for row in checks}


def test_doctor_healthy_and_redacts_gh_auth(config, doctor_ready, runner, capsys):
    _edit(
        config,
        rendering={"blender": "/test/Blender/blender"},
        runtime={"unity_editor": "/test/Unity/Unity"},
    )
    runner.side_effect = lambda command, **_: subprocess.CompletedProcess(
        command, 0, stderr="private-token"
    )
    code, checks = _doctor(capsys)
    assert code == 0 and set(checks) == {
        "config",
        "data_directory",
        "frontend",
        "tools",
        "services",
    }
    assert all(row["status"] == "ok" for row in checks.values())
    runner.assert_called_once_with(
        ["/test/bin/gh", "auth", "status"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5.0,
    )
    tools = doctor_ready[2]
    for tool in (
        "git",
        "uv",
        "node",
        "npm",
        "git-lfs",
        "ffmpeg",
        "ffprobe",
        "/test/Blender/blender",
        "/test/Unity/Unity",
        "gh",
    ):
        tools.assert_any_call(tool)
    assert any(
        call.args[0].endswith("node_modules/@github/copilot-linux-x64/copilot")
        for call in tools.call_args_list
    )
    assert cli.main(["doctor"]) == 0
    assert "OK   config: Private configuration is valid." in capsys.readouterr().out


def test_doctor_warnings_do_not_fail(config, doctor_ready, runner, monkeypatch, capsys):
    _, index, _ = doctor_ready
    config.chmod(0o644)
    os.utime(index, ns=(0, 0))
    _edit(config, services=[_service("optional", "http://localhost:1234", ["vlm"])])
    http = Mock(side_effect=OSError("private-token"))
    monkeypatch.setattr(cli, "http_status", http)
    runner.side_effect = subprocess.TimeoutExpired("gh", 5)
    code, checks = _doctor(capsys)
    assert code == 0
    assert checks["config"]["status"] == "warn" and "chmod 600" in checks["config"]["detail"]
    assert (
        checks["frontend"]["status"] == "warn"
        and "make build-frontend" in checks["frontend"]["detail"]
    )
    assert checks["services"]["status"] == "warn" and not checks["services"]["required"]
    assert checks["tools"]["status"] == "warn"
    assert stat.S_IMODE(config.stat().st_mode) == 0o644


@pytest.mark.parametrize(
    "source_name", ["index.html", "package.json", "vite.config.ts", "tsconfig.json"]
)
def test_doctor_checks_exact_config_mode_and_top_level_frontend_inputs(
    config, doctor_ready, capsys, source_name
):
    _, index, _ = doctor_ready
    config.chmod(0o700)
    source = diagnostics.APP_ROOT / "apps/frontend" / source_name
    source.touch()
    newer = index.stat().st_mtime_ns + 1_000_000_000
    os.utime(source, ns=(newer, newer))
    code, checks = _doctor(capsys)
    assert code == 0
    assert checks["config"]["status"] == "warn" and "chmod 600" in checks["config"]["detail"]
    assert (
        checks["frontend"]["status"] == "warn"
        and "make build-frontend" in checks["frontend"]["detail"]
    )


@pytest.mark.parametrize(
    "field, value",
    [
        ("data_directory", None),
        ("data_directory", 42),
        ("frontend_directory", []),
        ("origin", False),
        ("origin", "http://localhost:90000"),
        ("origin", "http://localhost:0"),
        ("free_space_reserve_bytes", "5"),
        ("execution_profiles", "not-a-list"),
        ("services", [{}]),
        ("runtime", None),
    ],
)
def test_malformed_config_fields_are_clean_value_errors_and_doctor_failures(
    config, doctor_ready, capsys, field, value
):
    _edit(config, **{field: value})
    with pytest.raises(ValueError):
        Settings.load(config)
    code, checks = _doctor(capsys)
    assert code == 1
    assert checks["config"] == {
        "name": "config",
        "status": "fail",
        "required": True,
        "detail": "Cannot read or validate the configuration JSON.",
    }


@pytest.mark.parametrize("kind", ["bad JSON", "missing", "symlink"])
def test_doctor_invalid_config_fails_without_creating_data(
    config, doctor_ready, tmp_path, capsys, kind
):
    data = Settings.load(config).data_directory
    data.rmdir()
    explicit = tmp_path / "missing.json"
    if kind == "bad JSON":
        explicit.write_text("private-token, not valid JSON")
    elif kind == "symlink":
        explicit.symlink_to(config)
    code, checks = _doctor(capsys, config=explicit)
    assert code == 1 and checks["config"]["status"] == "fail" and checks["config"]["required"]
    assert "private-token" not in str(checks)
    assert not data.exists()


@pytest.mark.parametrize("unsafe", [True, False])
def test_doctor_absent_data_is_never_created(config, doctor_ready, tmp_path, capsys, unsafe):
    parent = tmp_path / "data-parent"
    parent.mkdir()
    if unsafe:
        parent.chmod(0o777)
    target = parent / "new" / "data"
    _edit(config, data_directory=str(target))
    code, checks = _doctor(capsys)
    assert code == (1 if unsafe else 0)
    assert checks["data_directory"]["status"] == ("fail" if unsafe else "warn")
    assert not target.parent.exists()


def test_doctor_missing_tools_frontend_and_disk_reserve_fail(config, doctor_ready, capsys):
    _source, index, tools = doctor_ready
    index.unlink()
    tools.side_effect = lambda name: None if name == "npm" else name
    _edit(config, free_space_reserve_bytes=10**18)
    code, checks = _doctor(capsys)
    assert code == 1
    assert {name for name, row in checks.items() if row["status"] == "fail"} == {
        "tools",
        "frontend",
        "data_directory",
    }
    assert "npm" in checks["tools"]["detail"]
    assert "reserve" in checks["data_directory"]["detail"]


@pytest.mark.parametrize("command", ["backup", "verify", "restore", "import"])
def test_delegated_help_outside_checkout(config, tmp_path, monkeypatch, capsys, command):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [path for path in sys.path if path != str(APP_ROOT)])
    assert cli.main([command, "--help"]) == 0
    capture = capsys.readouterr()
    assert not capture.err and capture.out.startswith(f"usage: proscenium {command} ")
    assert sys.path.count(str(APP_ROOT)) == 1


def test_real_delegated_failures_preserve_exit_codes(config, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["verify", "bad-bundle"]) == 2
    assert cli.main(["restore", "bad-bundle", "--target", "restored"]) == 2
    bad_config = tmp_path / "bad.json"
    bad_config.write_text("{")
    assert (
        cli.main(["backup", "--config", "bad.json", "--project-id", "demo", "--target", "saved"])
        == 2
    )
    assert (
        cli.main(
            [
                "import",
                "--config",
                "bad.json",
                "--project-root",
                "project",
                "--manifest",
                "manifest.json",
            ]
        )
        == 1
    )
    capture = capsys.readouterr()
    assert not capture.out and capture.err.count("Recovery refused:") == 3
    assert "Import failed:" in capture.err
    assert not (tmp_path / "restored").exists() and not (tmp_path / "saved").exists()


def test_installed_and_legacy_help_outside_checkout(tmp_path):
    script = Path(sys.executable).with_name("proscenium")
    for args, expected in (
        ([str(script), "--help"], "Operate Proscenium Studio"),
        ([str(script), "backup", "--help"], "usage: proscenium backup"),
        ([sys.executable, "-m", "proscenium", "--help"], "Host-native Proscenium service"),
        ([sys.executable, "-m", "proscenium", "verify", "--help"], "usage: proscenium verify"),
    ):
        result = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0 and expected in result.stdout and not result.stderr
