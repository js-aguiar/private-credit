"""CETIP asset-type mapping and ANBIMA URL helpers."""

from __future__ import annotations

from dataclasses import dataclass

PORTAL_BASE = "https://data.anbima.com.br"
BFF_MARKER = "/web-bff/v1/"

_DEBENTURE_ALIASES = {
    "debenture",
    "debentures",
    "deb",
    "db",
}
_CRA_ALIASES = {"cra"}
_CRI_ALIASES = {"cri"}


@dataclass(frozen=True)
class CetipAsset:
    codigo_cetip: str
    tipo: str  # "debenture" | "cra" | "cri"

    @property
    def portal_agenda_url(self) -> str:
        code = self.codigo_cetip
        if self.tipo == "debenture":
            return f"{PORTAL_BASE}/debentures/{code}/agenda"
        return f"{PORTAL_BASE}/certificado-de-recebiveis/{code}/agenda"

    @property
    def bff_agenda_path_fragment(self) -> str:
        """Substring used to match intercepted BFF agenda responses."""
        code = self.codigo_cetip
        if self.tipo == "debenture":
            return f"/v1/debentures/{code}/agenda"
        return f"/v1/certificado-recebiveis/{code}/agenda"


def normalize_tipo(raw: str | None) -> str | None:
    """Map a free-form tipo string to ``debenture`` / ``cra`` / ``cri``."""
    if raw is None:
        return None
    token = raw.strip().lower()
    if not token:
        return None
    if token in _DEBENTURE_ALIASES:
        return "debenture"
    if token in _CRA_ALIASES:
        return "cra"
    if token in _CRI_ALIASES:
        return "cri"
    # Accept "Certificado de Recebíveis" style labels if CETIP prefix is clear later.
    if "debent" in token:
        return "debenture"
    if token.startswith("cra") or " agroneg" in token:
        return "cra"
    if token.startswith("cri") or "imobili" in token:
        return "cri"
    return None


def infer_tipo_from_cetip(codigo_cetip: str) -> str | None:
    upper = (codigo_cetip or "").strip().upper()
    if upper.startswith("CRA"):
        return "cra"
    if upper.startswith("CRI"):
        return "cri"
    return None
