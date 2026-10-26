"""Thin wrapper around the official ``ligo-gracedb`` REST client.

Centralises client construction (auth + service URL) and exposes a few
convenience helpers used by the MCP tools. Keeping the GraceDB specifics here
means the MCP layer in ``server.py`` stays small and testable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from ligo.gracedb.rest import GraceDb

# Public production instance. Override with GRACEDB_SERVICE_URL for playground/test.
DEFAULT_SERVICE_URL = "https://gracedb.ligo.org/api/"

# Files that carry the astrophysical source classification (BBH/BNS/NSBH/Terrestrial).
P_ASTRO_FILENAMES = ("p_astro.json", "pipeline_p_astro.json")


@dataclass
class GraceDbConfig:
    service_url: str = DEFAULT_SERVICE_URL
    # Path to an X.509 proxy or a (cert, key) pair. If unset, the client falls
    # back to anonymous access, which is sufficient for public superevents.
    cred: str | tuple[str, str] | None = None
    force_noauth: bool = False

    @classmethod
    def from_env(cls) -> GraceDbConfig:
        service_url = os.environ.get("GRACEDB_SERVICE_URL", DEFAULT_SERVICE_URL)
        cert = os.environ.get("GRACEDB_CERT")
        key = os.environ.get("GRACEDB_KEY")
        proxy = os.environ.get("GRACEDB_PROXY")

        cred: str | tuple[str, str] | None = None
        if cert and key:
            cred = (cert, key)
        elif proxy:
            cred = proxy

        force_noauth = os.environ.get("GRACEDB_FORCE_NOAUTH", "").lower() in {"1", "true", "yes"}
        return cls(service_url=service_url, cred=cred, force_noauth=force_noauth)


class GraceDbClient:
    """Convenience facade over :class:`ligo.gracedb.rest.GraceDb`."""

    def __init__(self, config: GraceDbConfig | None = None) -> None:
        self._config = config or GraceDbConfig.from_env()
        self._client: GraceDb | None = None

    @property
    def client(self) -> GraceDb:
        if self._client is None:
            kwargs: dict[str, Any] = {"service_url": self._config.service_url}
            if self._config.force_noauth or self._config.cred is None:
                kwargs["force_noauth"] = True
            else:
                kwargs["cred"] = self._config.cred
            self._client = GraceDb(**kwargs)
        return self._client

    # -- Superevents -----------------------------------------------------

    def search_superevents(
        self,
        query: str,
        *,
        max_results: int = 20,
        columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a GraceDB superevent query string and return up to ``max_results`` rows."""
        results: list[dict[str, Any]] = []
        for record in self.client.superevents(query=query, orderby="-created", columns=columns):
            results.append(record)
            if len(results) >= max_results:
                break
        return results

    def get_superevent(self, superevent_id: str) -> dict[str, Any]:
        return self.client.superevent(superevent_id).json()

    def get_labels(self, superevent_id: str) -> list[dict[str, Any]]:
        return self.client.labels(superevent_id).json()["labels"]

    def list_files(self, superevent_id: str) -> dict[str, str]:
        """Map filename -> download URL for every file attached to a superevent."""
        return self.client.files(superevent_id).json()

    def read_json_file(self, superevent_id: str, filename: str) -> dict[str, Any]:
        response = self.client.files(superevent_id, filename)
        return json.loads(response.read())

    def download_file(self, superevent_id: str, filename: str, dest_path: str) -> str:
        response = self.client.files(superevent_id, filename)
        with open(dest_path, "wb") as handle:
            handle.write(response.read())
        return dest_path

    def get_p_astro(self, superevent_id: str) -> dict[str, Any] | None:
        """Return source-classification probabilities, or ``None`` if not published."""
        available = self.list_files(superevent_id)
        for candidate in P_ASTRO_FILENAMES:
            for name in available:
                if name.endswith(candidate):
                    return self.read_json_file(superevent_id, name)
        return None
