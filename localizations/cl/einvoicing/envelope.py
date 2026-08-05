"""Sobres del SII: EnvioDTE para facturas, EnvioBOLETA para boletas.

No es el mismo sobre ni el mismo destino. Meter boletas en un EnvioDTE es un
rechazo garantizado, así que el tipo de sobre es un parámetro explícito y no
algo que se deduzca por descuido.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

SII_RUT = "60803000-K"
ENVELOPE_ROOTS = {"dte": "EnvioDTE", "boleta": "EnvioBOLETA"}


@dataclass(frozen=True)
class EnvelopeData:
    issuer_rut: str
    sender_rut: str  # quien firma y sube; puede ser un tercero autorizado
    resolution_date: str  # fecha de la resolución del SII que autoriza a emitir
    resolution_number: str


def build_envelope(
    documents: list[tuple[str, bytes]],
    data: EnvelopeData,
    *,
    kind: str = "dte",
    now: datetime | None = None,
) -> bytes:
    """Construye el sobre. `documents` son pares (tipo, xml del DTE firmado).

    `kind` elige entre EnvioDTE y EnvioBOLETA. La firma del sobre completo la
    aplica el `Signer` (ADR-014); aquí solo se arma la carátula con los
    subtotales por tipo que el esquema exige.
    """
    root = ENVELOPE_ROOTS.get(kind)
    if root is None:
        raise ValueError(f"Tipo de sobre desconocido: {kind}")
    timestamp = (now or datetime.now(tz=UTC)).strftime("%Y-%m-%dT%H:%M:%S")
    by_type = Counter(doc_type for doc_type, _ in documents)
    subtotals = "".join(
        f"<SubTotDTE><TpoDTE>{doc_type}</TpoDTE><NroDTE>{count}</NroDTE></SubTotDTE>"
        for doc_type, count in sorted(by_type.items())
    )
    body = "".join(payload.decode("iso-8859-1", errors="replace") for _, payload in documents)
    envelope = (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        f'<{root} xmlns="http://www.sii.cl/SiiDte" version="1.0">'
        '<SetDTE ID="SetDoc">'
        '<Caratula version="1.0">'
        f"<RutEmisor>{data.issuer_rut}</RutEmisor>"
        f"<RutEnvia>{data.sender_rut}</RutEnvia>"
        f"<RutReceptor>{SII_RUT}</RutReceptor>"
        f"<FchResol>{data.resolution_date}</FchResol>"
        f"<NroResol>{data.resolution_number}</NroResol>"
        f"<TmstFirmaEnv>{timestamp}</TmstFirmaEnv>"
        f"{subtotals}"
        "</Caratula>"
        f"{body}"
        "</SetDTE>"
        f"</{root}>"
    )
    return envelope.encode("iso-8859-1", errors="replace")
