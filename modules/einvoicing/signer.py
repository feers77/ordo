"""Firma XMLDSig de documento completo (ADR-014, aceptado).

Implementa el contrato `Signer` con firma envuelta (enveloped), que es lo que
exigen tanto el EnvioDTE del SII como el DE del SIFEN. El material
criptográfico llega por parámetro: viene del vault en producción y de un
certificado efímero en los tests. Nunca de la base de datos.
"""

from __future__ import annotations

from lxml import etree
from ordo_core.errors import KernelError
from signxml import DigestAlgorithm, SignatureMethod, XMLSigner, XMLVerifier
from signxml.exceptions import InvalidSignature

# El SII exige RSA-SHA1 en la firma de documento (formato DTE); el SIFEN
# acepta RSA-SHA256. La elección es del formato, no nuestra.
ALGORITHMS = {
    "rsa-sha1": (SignatureMethod.RSA_SHA1, DigestAlgorithm.SHA1),
    "rsa-sha256": (SignatureMethod.RSA_SHA256, DigestAlgorithm.SHA256),
}


class SignerError(KernelError):
    """Fallo al firmar o verificar un documento."""


class XmlDSigSigner:
    """Firma enveloped XMLDSig con la clave y el certificado entregados."""

    def __init__(
        self,
        *,
        key_pem: bytes,
        cert_pem: bytes,
        algorithm: str = "rsa-sha256",
    ) -> None:
        if algorithm not in ALGORITHMS:
            raise SignerError(
                "EDI_SIGN_BAD_ALGORITHM",
                f"Algoritmo de firma desconocido: {algorithm}",
                hint="Usa 'rsa-sha1' (SII) o 'rsa-sha256' (SIFEN).",
            )
        self.key_pem = key_pem
        self.cert_pem = cert_pem
        signature_method, digest = ALGORITHMS[algorithm]
        if algorithm == "rsa-sha1":
            # signxml bloquea SHA-1 por defecto, con razón. El formato del SII
            # lo exige igual: se habilita solo para esta instancia, no global.
            class _LegacySigner(XMLSigner):
                def check_deprecated_methods(self) -> None:
                    return None

            self._signer: XMLSigner = _LegacySigner(
                signature_algorithm=signature_method, digest_algorithm=digest
            )
        else:
            self._signer = XMLSigner(signature_algorithm=signature_method, digest_algorithm=digest)

    def sign(self, xml: bytes, reference: str) -> bytes:
        try:
            root = etree.fromstring(xml)
        except etree.XMLSyntaxError as exc:
            raise SignerError(
                "EDI_SIGN_INVALID_XML", "El documento a firmar no es XML válido"
            ) from exc
        try:
            signed = self._signer.sign(root, key=self.key_pem, cert=self.cert_pem)
        except Exception as exc:
            raise SignerError(
                "EDI_SIGN_FAILED",
                "No se pudo firmar el documento",
                hint="Revisa que la clave y el certificado se correspondan.",
            ) from exc
        return etree.tostring(signed)


def verify_signature(signed_xml: bytes, cert_pem: bytes) -> bytes:
    """Verifica la firma y devuelve los bytes canónicos verificados.

    La usa la suite de tests y la usará la recepción de documentos de
    proveedores. Una firma inválida es un error estable, no una excepción de
    librería.
    """
    try:
        root = etree.fromstring(signed_xml)
        result = XMLVerifier().verify(root, x509_cert=cert_pem.decode())
    except InvalidSignature as exc:
        raise SignerError("EDI_SIGN_INVALID", "La firma del documento no es válida") from exc
    except etree.XMLSyntaxError as exc:
        raise SignerError("EDI_SIGN_INVALID_XML", "El documento firmado no es XML válido") from exc
    if result.signed_xml is not None:
        return etree.tostring(result.signed_xml)
    return result.signed_data or b""
