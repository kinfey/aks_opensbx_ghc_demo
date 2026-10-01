import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "run_copilot", Path(__file__).resolve().parents[1] / "sandbox/run_copilot.py"
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

MARKDOWN = "## Menu\n\n| Item | Price |\n| --- | ---: |\n| Fries | 13.50 |\n\n**Only a demo**\n\n```py\n  print('ok')\n```"


def message(content, **extra):
    return {"type": "assistant.message", "data": {"content": content, **extra}}


def stream(*events):
    return "\n".join(json.dumps(event) for event in events)


def test_extracts_original_markdown_not_terminal_output_or_tool_events():
    output = stream(
        message("Let me check", phase="commentary"),
        message("Tool request", toolRequests=[{"name": "menu"}]),
        {"type": "tool.execution_complete", "data": {"result": "private tool result"}},
        {"type": "assistant.message_delta", "data": {"deltaContent": "duplicate"}},
        message(MARKDOWN, phase="final_answer"),
        {"type": "result", "exitCode": 0},
    )
    assert runner.extract_reply(output) == MARKDOWN


def test_uses_last_complete_reply_and_supports_events_without_phase():
    assert (
        runner.extract_reply(
            stream(
                message("Earlier response"),
                message(MARKDOWN),
                {"type": "result", "exitCode": 0},
            )
        )
        == MARKDOWN
    )


@pytest.mark.parametrize(
    "output",
    [
        "not json",
        "[]",
        stream({"type": "assistant.message", "data": None}),
        stream(message(MARKDOWN)),
        stream(message(MARKDOWN), {"type": "result", "exitCode": 1}),
        stream(message(""), {"type": "result", "exitCode": 0}),
        stream(
            message("commentary", phase="commentary"), {"type": "result", "exitCode": 0}
        ),
    ],
)
def test_invalid_or_incomplete_output_fails_explicitly(output):
    with pytest.raises((ValueError, TypeError)):
        runner.extract_reply(output)


@pytest.mark.parametrize("exit_code", [0, 1])
def test_runner_requests_json_and_prints_only_completed_reply(
    tmp_path, monkeypatch, capsys, exit_code
):
    payload = tmp_path / "request.json"
    payload.write_text(
        json.dumps(
            {
                "locale": "en",
                "session_id": "test-session",
                "message": "menu",
                "copilot_session_id": "test-copilot",
                "model": "gpt-6-astra",
                "reasoning_effort": "high",
            }
        )
    )
    monkeypatch.setattr(runner.sys, "argv", ["run_copilot.py", str(payload)])
    run = Mock(
        return_value=SimpleNamespace(
            returncode=exit_code,
            stdout=stream(
                message(MARKDOWN, phase="final_answer"),
                {"type": "result", "exitCode": 0},
            ),
            stderr="",
        )
    )
    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main() == exit_code
    command = run.call_args.args[0]
    assert command[command.index("--output-format") + 1] == "json"
    output = capsys.readouterr()
    assert output.out == (
        json.dumps({"message": MARKDOWN}, ensure_ascii=False) + "\n"
        if exit_code == 0
        else ""
    )
    if exit_code:
        assert "failed" in output.err
