"""Command handlers for external memory API key management.

Kept in a mixin so the large ``CommandHandler`` module only gains the import and
the base class, which keeps the change local and review-friendly.

Chat form (documented in ``/lmem help``):

- ``/lmem apikey list``
- ``/lmem apikey create <name> [read|write] [personas] [scopes] [rate]``
- ``/lmem apikey revoke <id|name>``

``personas``/``scopes`` accept comma-separated values; ``rate`` overrides the
per-minute request limit for that key.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, MessageEventResult

from .external_api import ExternalApiService
from .i18n_backend import t


def _split_csv(value: str) -> list[str] | None:
    items = [item.strip() for item in str(value or "").split(",")]
    cleaned = [item for item in items if item]
    return cleaned or None


class ExternalApiCommandMixin:
    """``/lmem apikey`` subcommands (create/list/revoke)."""

    async def handle_apikey(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        name: str = "",
        mode: str = "read",
        personas: str = "",
        scopes: str = "",
        rate: int = 0,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """Handle ``/lmem apikey ...`` and yield one plain-text result."""
        service = self._external_api_service()
        if not service.enabled:
            yield event.plain_result(t("external_api.command.disabled"))
            return
        normalized_action = str(action or "list").strip().lower()
        try:
            if normalized_action == "create":
                message = await self._apikey_create(
                    service, name, mode, personas, scopes, rate
                )
            elif normalized_action == "list":
                message = await self._apikey_list(service)
            elif normalized_action == "revoke":
                message = await self._apikey_revoke(service, name)
            else:
                message = t("external_api.command.invalid_action", action=action)
        except Exception as exc:
            message = t("external_api.command.create_failed", error=str(exc))
        yield event.plain_result(message)

    def _external_api_service(self) -> ExternalApiService:
        """Build the service from the ready memory engine and plugin config."""
        db_path = str(getattr(self.memory_engine, "db_path", "") or "")
        data_dir = Path(db_path).parent if db_path else Path(".")
        return ExternalApiService(self.config_manager, self.memory_engine, data_dir)

    async def _apikey_create(
        self,
        service: ExternalApiService,
        name: str,
        mode: str,
        personas: str,
        scopes: str,
        rate: int,
    ) -> str:
        key_name = str(name or "").strip()
        if not key_name:
            return t("external_api.command.name_required")
        normalized_mode = str(mode or "read").strip().lower()
        scopes_list = (
            ["read", "write"] if normalized_mode in ("write", "rw") else ["read"]
        )
        try:
            rate_limit = int(rate or 0)
        except (TypeError, ValueError):
            rate_limit = 0
        record, token = await service.create_key(
            key_name,
            scopes=scopes_list,
            allowed_personas=_split_csv(personas),
            allowed_scopes=_split_csv(scopes),
            rate_limit_per_min=rate_limit if rate_limit > 0 else None,
        )
        return t(
            "external_api.command.created",
            name=record.name,
            key_id=record.id,
            scopes=",".join(record.scopes),
            rate=record.rate_limit_per_min,
            write_rate=record.write_limit_per_min,
            token=token,
        )

    async def _apikey_list(self, service: ExternalApiService) -> str:
        keys = await service.list_keys()
        if not keys:
            return t("external_api.command.list_empty")
        rows = [
            t(
                "external_api.command.list_row",
                key_id=key.id,
                name=key.name,
                key_prefix=key.key_prefix,
                status=t("external_api.command.status_revoked")
                if key.revoked
                else t("external_api.command.status_active"),
                scopes=",".join(key.scopes),
                rate=key.rate_limit_per_min,
                write_rate=key.write_limit_per_min,
            )
            for key in keys
        ]
        return (
            t("external_api.command.list_header", total=len(keys))
            + "\n"
            + "\n".join(rows)
        )

    async def _apikey_revoke(self, service: ExternalApiService, identifier: str) -> str:
        target = str(identifier or "").strip()
        if not target:
            return t("external_api.command.usage")
        record = await service.revoke_key(target)
        if record is None:
            return t("external_api.command.revoke_not_found", identifier=target)
        return t("external_api.command.revoked", name=record.name, key_id=record.id)


__all__ = ["ExternalApiCommandMixin"]
