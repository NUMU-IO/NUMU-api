"""n8n webhook client (research R9).

The LLM never calls n8n directly. NUMU-api triggers only **allow-listed** named
workflows, injecting the caller's `tenant_id` + a server-side HMAC signature; the
secret + base URL never reach the LLM or client. The same secret verifies the
result callback. The LLM cannot invoke an arbitrary URL.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from uuid import UUID, uuid4

import httpx

from src.config import settings as app_settings
from src.core.logging import get_logger

logger = get_logger(__name__)


class N8nError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class N8nClient:
    def __init__(
        self, *, base_url: str, secret: str, allowed_workflows: list[str]
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._allowed = set(allowed_workflows or [])

    def is_allowed(self, workflow: str) -> bool:
        return workflow in self._allowed

    async def trigger(self, workflow: str, *, tenant_id: UUID, params: dict) -> dict:
        if not self.is_allowed(workflow):
            raise N8nError("not_allowed", f"Workflow '{workflow}' is not allow-listed.")
        if not self._secret:
            raise N8nError("not_configured", "n8n webhook secret is not configured.")

        run_id = str(uuid4())
        payload = {
            "run_id": run_id,
            "workflow": workflow,
            "tenant_id": str(tenant_id),
            "params": params,
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-NUMU-Signature": _sign(self._secret, body),
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self._base_url}/webhook/{workflow}",
                    content=body,
                    headers=headers,
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "agent_n8n_trigger_failed", workflow=workflow, error=str(exc)
            )
            raise N8nError(
                "dispatch_failed", "Could not reach the workflow engine."
            ) from exc

        logger.info("agent_n8n_triggered", workflow=workflow, run_id=run_id)
        return {"run_id": run_id, "status": "queued"}

    def verify_callback(self, body: bytes, signature: str | None) -> bool:
        if not self._secret or not signature:
            return False
        return hmac.compare_digest(_sign(self._secret, body), signature)


def get_n8n_client() -> N8nClient:
    s = app_settings
    return N8nClient(
        base_url=s.agent_n8n_base_url,
        secret=s.agent_n8n_webhook_secret,
        allowed_workflows=s.agent_n8n_allowed_workflows,
    )
