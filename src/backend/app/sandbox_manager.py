import asyncio
import json
import shlex
import time
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from opensandbox import SandboxSync
from opensandbox.config import ConnectionConfigSync
from opensandbox.models.sandboxes import (
    Credential,
    CredentialBinding,
    CredentialProxyConfig,
    NetworkPolicy,
    NetworkRule,
    SandboxState,
)

from app.config import Settings


class SandboxError(RuntimeError):
    pass


@dataclass
class ManagedSession:
    sandbox: SandboxSync
    copilot_session_id: str
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SandboxManager:
    def __init__(self, settings: Settings, logger) -> None:
        self.settings = settings
        self.logger = logger
        self._sessions: dict[str, ManagedSession] = {}
        self._sessions_lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None
        self._connection = ConnectionConfigSync(
            domain=settings.opensandbox_domain,
            api_key=settings.opensandbox_api_key,
            request_timeout=timedelta(seconds=settings.sandbox_request_timeout_seconds),
        )

    async def start(self) -> None:
        self.settings.require_runtime_secrets()
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        async with self._sessions_lock:
            session_ids = list(self._sessions)
        await asyncio.gather(*(self.delete(session_id) for session_id in session_ids))

    async def chat(
        self,
        session_id: str,
        message: str,
        cart: list[dict],
        locale: str,
    ) -> tuple[str, str]:
        session = await self._get_or_create(session_id)
        async with session.lock:
            session.last_used = time.monotonic()
            request_path = f"/tmp/copilot-request-{session_id}.json"
            payload = {
                "message": message,
                "cart": cart,
                "locale": locale,
                "session_id": session_id,
                "copilot_session_id": session.copilot_session_id,
                "model": self.settings.copilot_model,
                "reasoning_effort": self.settings.copilot_reasoning_effort,
            }
            try:
                await asyncio.to_thread(
                    session.sandbox.files.write_file,
                    request_path,
                    json.dumps(payload, ensure_ascii=False),
                    mode=600,
                )
                execution = await asyncio.to_thread(
                    session.sandbox.commands.run,
                    shlex.join(["python3", "/opt/mcd/run_copilot.py", request_path]),
                )
            except Exception as exc:
                raise SandboxError("Copilot sandbox execution failed") from exc
            finally:
                try:
                    await asyncio.to_thread(
                        session.sandbox.files.delete_files,
                        [request_path],
                    )
                except Exception:
                    self.logger.warning(
                        "request_file_cleanup_failed",
                        extra={"session_id": session_id, "sandbox_id": session.sandbox.id},
                    )

            stdout = "".join(item.text for item in execution.logs.stdout).strip()
            stderr = "".join(item.text for item in execution.logs.stderr).strip()
            if execution.exit_code != 0:
                self.logger.error(
                    "copilot_execution_failed",
                    extra={
                        "session_id": session_id,
                        "sandbox_id": session.sandbox.id,
                        "exit_code": execution.exit_code,
                    },
                )
                detail = stderr[-500:] if stderr else "no diagnostic output"
                raise SandboxError(
                    f"Copilot CLI returned exit code {execution.exit_code}: {detail}"
                )
            if not stdout:
                raise SandboxError("Copilot CLI returned an empty response")
            try:
                envelope = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise SandboxError("Copilot runner returned invalid JSON") from exc
            if not isinstance(envelope, dict) or not isinstance(envelope.get("message"), str):
                raise SandboxError("Copilot runner returned an invalid reply envelope")
            reply = envelope["message"]
            if not reply.strip():
                raise SandboxError("Copilot CLI returned an empty response")
            if len(reply) > 100_000:
                raise SandboxError("Copilot CLI response exceeded the allowed size")
            return reply, session.sandbox.id

    async def delete(self, session_id: str) -> bool:
        async with self._sessions_lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        try:
            await asyncio.to_thread(session.sandbox.kill)
        except Exception as exc:
            self.logger.error(
                "sandbox_delete_failed",
                extra={"session_id": session_id, "sandbox_id": session.sandbox.id},
            )
            raise SandboxError("Failed to delete sandbox") from exc
        return True

    async def _get_or_create(self, session_id: str) -> ManagedSession:
        async with self._sessions_lock:
            existing = self._sessions.get(session_id)
            if existing:
                return existing
            if len(self._sessions) >= self.settings.max_sessions:
                raise SandboxError("The service has reached its active session limit")

            try:
                session = await asyncio.to_thread(self._create_session, session_id)
            except Exception as exc:
                raise SandboxError("Failed to create Kata sandbox") from exc
            self._sessions[session_id] = session
            return session

    def _create_session(self, session_id: str) -> ManagedSession:
        sandbox = SandboxSync.create(
            image=self.settings.sandbox_image,
            timeout=timedelta(seconds=self.settings.sandbox_timeout_seconds),
            metadata={"app": "mcdonalds-copilot", "session": session_id, "runtime": "kata"},
            entrypoint=["bash", "-lc", "exec sleep infinity"],
            connection_config=self._connection,
            skip_health_check=True,
            env={
                "GH_TOKEN": "credential-vault-placeholder",
                "COPILOT_GITHUB_TOKEN": "credential-vault-placeholder",
                "COPILOT_MCP_MCD_TOKEN": "credential-vault-placeholder",
                "COPILOT_HOME": "/workspace/.copilot",
                "IS_SANDBOX": "1",
            },
            network_policy=NetworkPolicy(
                defaultAction="deny",
                egress=[
                    NetworkRule(action="allow", target="github.com"),
                    NetworkRule(action="allow", target="*.github.com"),
                    NetworkRule(action="allow", target="*.githubusercontent.com"),
                    NetworkRule(action="allow", target="*.githubcopilot.com"),
                    NetworkRule(action="allow", target="*.individual.githubcopilot.com"),
                    NetworkRule(action="allow", target="*.business.githubcopilot.com"),
                    NetworkRule(action="allow", target="*.enterprise.githubcopilot.com"),
                    NetworkRule(action="allow", target="default.exp-tas.com"),
                    NetworkRule(action="allow", target="mcp.mcd.cn"),
                ],
            ),
            credential_proxy=CredentialProxyConfig(enabled=True),
            secure_access=True,
        )
        try:
            self._wait_ready(sandbox)
            sandbox.credential_vault.create(
                credentials=[
                    Credential(name="github-token", source={"value": self.settings.github_token}),
                    Credential(name="mcd-mcp-token", source={"value": self.settings.mcd_mcp_token}),
                ],
                bindings=[
                    CredentialBinding(
                        name="github-api",
                        match={
                            "schemes": ["https"],
                            "ports": [443],
                            "hosts": [
                                "api.github.com",
                                "api.githubcopilot.com",
                                "api.individual.githubcopilot.com",
                                "api.business.githubcopilot.com",
                                "api.enterprise.githubcopilot.com",
                            ],
                            "methods": ["GET", "POST"],
                            "paths": ["/*"],
                        },
                        auth={"type": "bearer", "credential": "github-token"},
                    ),
                    CredentialBinding(
                        name="mcd-mcp",
                        match={
                            "schemes": ["https"],
                            "ports": [443],
                            "hosts": ["mcp.mcd.cn"],
                            "methods": ["GET", "POST"],
                            "paths": ["/*"],
                        },
                        auth={"type": "bearer", "credential": "mcd-mcp-token"},
                    ),
                ],
            )
        except Exception:
            try:
                sandbox.kill()
            except Exception:
                self.logger.error("sandbox_setup_cleanup_failed", extra={"sandbox_id": sandbox.id})
            raise
        copilot_session_id = str(uuid5(NAMESPACE_URL, f"mcdonalds-copilot:{session_id}"))
        return ManagedSession(sandbox=sandbox, copilot_session_id=copilot_session_id)

    def _wait_ready(self, sandbox: SandboxSync) -> None:
        deadline = time.monotonic() + self.settings.sandbox_request_timeout_seconds
        while time.monotonic() < deadline:
            info = sandbox.get_info()
            if info.status.state == SandboxState.RUNNING and sandbox.is_healthy():
                return
            if info.status.state == SandboxState.FAILED:
                detail = info.status.message or info.status.reason or "unknown failure"
                raise SandboxError(f"Sandbox failed to start: {detail}")
            time.sleep(3)
        raise SandboxError("Sandbox did not become ready before the timeout")

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            cutoff = time.monotonic() - self.settings.sandbox_idle_seconds
            async with self._sessions_lock:
                expired = [
                    session_id
                    for session_id, session in self._sessions.items()
                    if session.last_used < cutoff and not session.lock.locked()
                ]
            for session_id in expired:
                try:
                    await self.delete(session_id)
                except SandboxError:
                    self.logger.exception(
                        "idle_sandbox_cleanup_failed",
                        extra={"session_id": session_id},
                    )
