import json
import shlex
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from opensandbox.models.sandboxes import SandboxState

from app.config import Settings
from app.sandbox_manager import ManagedSession, SandboxError, SandboxManager


@pytest.fixture
def manager():
    return SandboxManager(
        Settings(_env_file=None, github_token="test-only", mcd_mcp_token="test-only"), Mock()
    )


@pytest.fixture
def sandbox():
    result = Mock()
    result.id = "test-sandbox"
    result.commands.run.return_value = SimpleNamespace(
        exit_code=0,
        logs=SimpleNamespace(stdout=[SimpleNamespace(text='{"message":"ready"}')], stderr=[]),
    )
    return result


@pytest.mark.asyncio
async def test_chat_uses_sdk_shell_string_and_cleans_request(manager, sandbox):
    manager._sessions["test-session"] = ManagedSession(sandbox, "copilot-session")
    assert await manager.chat("test-session", "hello", [], "en") == ("ready", "test-sandbox")
    command = sandbox.commands.run.call_args.args[0]
    assert isinstance(command, str)
    assert shlex.split(command) == [
        "python3",
        "/opt/mcd/run_copilot.py",
        "/tmp/copilot-request-test-session.json",
    ]
    sandbox.files.delete_files.assert_called_once_with(["/tmp/copilot-request-test-session.json"])


@pytest.mark.asyncio
async def test_reply_envelope_preserves_markdown_across_log_chunks(manager, sandbox):
    markdown = (
        "## Menu\n\n| Item | Price |\n| --- | ---: |\n| Fries | 13.50 |"
        "\n\n**Demo**\n\n```py\n  print('ok')\n```"
    )
    wire = json.dumps({"message": markdown})
    sandbox.commands.run.return_value.logs.stdout = [
        SimpleNamespace(text=wire[:30]),
        SimpleNamespace(text=wire[30:]),
    ]
    manager._sessions["test-session"] = ManagedSession(sandbox, "copilot-session")
    reply, _ = await manager.chat("test-session", "hello", [], "en")
    assert reply == markdown


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", ["not JSON", "[]", '{"message":null}', '{"message":" "}', "{}"])
async def test_invalid_reply_envelope_fails_explicitly(manager, sandbox, wire):
    sandbox.commands.run.return_value.logs.stdout = [SimpleNamespace(text=wire)]
    manager._sessions["test-session"] = ManagedSession(sandbox, "copilot-session")
    with pytest.raises(SandboxError):
        await manager.chat("test-session", "hello", [], "en")


@pytest.mark.parametrize("stage", ["readiness", "credentials"])
def test_partial_setup_failure_deletes_sandbox(manager, sandbox, monkeypatch, stage):
    monkeypatch.setattr("app.sandbox_manager.SandboxSync.create", Mock(return_value=sandbox))
    readiness = Mock()
    monkeypatch.setattr(manager, "_wait_ready", readiness)
    failure = RuntimeError("setup failed")
    if stage == "readiness":
        readiness.side_effect = failure
    else:
        sandbox.credential_vault.create.side_effect = failure
    with pytest.raises(RuntimeError, match="setup failed"):
        manager._create_session("test-session")
    sandbox.kill.assert_called_once_with()


def test_vault_covers_copilot_apis_without_putting_secrets_in_sandbox(
    manager, sandbox, monkeypatch
):
    create = Mock(return_value=sandbox)
    monkeypatch.setattr("app.sandbox_manager.SandboxSync.create", create)
    monkeypatch.setattr(manager, "_wait_ready", Mock())
    manager._create_session("test-session")
    assert "test-only" not in create.call_args.kwargs["env"].values()
    github = sandbox.credential_vault.create.call_args.kwargs["bindings"][0]
    assert set(github.match.hosts) == {
        "api.github.com",
        "api.githubcopilot.com",
        "api.individual.githubcopilot.com",
        "api.business.githubcopilot.com",
        "api.enterprise.githubcopilot.com",
    }
    assert github.match.schemes == ["https"]
    assert github.match.ports == [443]
    sandbox.kill.assert_not_called()


@pytest.mark.asyncio
async def test_execution_error_cleans_request(manager, sandbox):
    manager._sessions["test-session"] = ManagedSession(sandbox, "copilot-session")
    sandbox.commands.run.side_effect = RuntimeError("execution failed")
    with pytest.raises(SandboxError, match="execution failed"):
        await manager.chat("test-session", "hello", [], "en")
    sandbox.files.delete_files.assert_called_once_with(["/tmp/copilot-request-test-session.json"])


def test_readiness_waits_for_execd_not_just_running_pod(manager, sandbox, monkeypatch):
    sandbox.get_info.return_value = SimpleNamespace(
        status=SimpleNamespace(state=SandboxState.RUNNING)
    )
    sandbox.is_healthy.side_effect = [False, False, True]
    sleep = Mock()
    monkeypatch.setattr("app.sandbox_manager.time.sleep", sleep)
    manager._wait_ready(sandbox)
    assert sandbox.is_healthy.call_count == 3
    assert sleep.call_count == 2


def test_execd_health_timeout_fails(manager, sandbox, monkeypatch):
    sandbox.get_info.return_value = SimpleNamespace(
        status=SimpleNamespace(state=SandboxState.RUNNING)
    )
    sandbox.is_healthy.return_value = False
    monkeypatch.setattr("app.sandbox_manager.time.monotonic", Mock(side_effect=[0, 1, 181]))
    monkeypatch.setattr("app.sandbox_manager.time.sleep", Mock())
    with pytest.raises(SandboxError, match="did not become ready"):
        manager._wait_ready(sandbox)
