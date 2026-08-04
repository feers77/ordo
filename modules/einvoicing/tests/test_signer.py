"""Firma XMLDSig real: firma, verifica y detecta manipulación."""

from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from signxml import DigestAlgorithm, SignatureConfiguration, SignatureMethod, XMLVerifier

from modules.einvoicing.signer import SignerError, XmlDSigSigner, verify_signature


def make_cert() -> tuple[bytes, bytes]:
    """Clave y certificado autofirmado efímeros, solo para la suite."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ACME SpA Test")])
    now = datetime(2026, 8, 4, tzinfo=UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return key_pem, cert_pem


SAMPLE = (
    b'<rDE><dVerFor>150</dVerFor><DE Id="01800123450010010000042">'
    b"<gTotSub><dTotGralOpe>2200000</dTotGralOpe></gTotSub></DE></rDE>"
)


class TestSignAndVerify:
    def test_sha256_signature_roundtrip(self) -> None:
        key_pem, cert_pem = make_cert()
        signer = XmlDSigSigner(key_pem=key_pem, cert_pem=cert_pem)
        signed = signer.sign(SAMPLE, reference="42")

        assert b"<ds:Signature" in signed or b"<Signature" in signed
        verified = verify_signature(signed, cert_pem)
        assert b"2200000" in verified

    def test_tampering_invalidates_the_signature(self) -> None:
        key_pem, cert_pem = make_cert()
        signer = XmlDSigSigner(key_pem=key_pem, cert_pem=cert_pem)
        signed = signer.sign(SAMPLE, reference="42")
        tampered = signed.replace(b"2200000", b"9900000")

        with pytest.raises(SignerError) as excinfo:
            verify_signature(tampered, cert_pem)
        assert excinfo.value.code == "EDI_SIGN_INVALID"

    def test_sha1_for_sii_is_supported(self) -> None:
        """El formato del SII exige RSA-SHA1; se firma y valida explícitamente."""
        key_pem, cert_pem = make_cert()
        signer = XmlDSigSigner(key_pem=key_pem, cert_pem=cert_pem, algorithm="rsa-sha1")
        signed = signer.sign(b'<DTE><Documento ID="F42T33"><x>1</x></Documento></DTE>', "F42T33")

        from lxml import etree

        config = SignatureConfiguration(
            signature_methods=frozenset([SignatureMethod.RSA_SHA1]),
            digest_algorithms=frozenset([DigestAlgorithm.SHA1]),
        )
        XMLVerifier().verify(
            etree.fromstring(signed), x509_cert=cert_pem.decode(), expect_config=config
        )

    def test_wrong_certificate_does_not_verify(self) -> None:
        key_pem, cert_pem = make_cert()
        _, other_cert = make_cert()
        signer = XmlDSigSigner(key_pem=key_pem, cert_pem=cert_pem)
        signed = signer.sign(SAMPLE, reference="42")
        with pytest.raises(SignerError):
            verify_signature(signed, other_cert)

    def test_unknown_algorithm_is_a_stable_error(self) -> None:
        key_pem, cert_pem = make_cert()
        with pytest.raises(SignerError) as excinfo:
            XmlDSigSigner(key_pem=key_pem, cert_pem=cert_pem, algorithm="md5")
        assert excinfo.value.code == "EDI_SIGN_BAD_ALGORITHM"

    def test_garbage_input_is_a_stable_error(self) -> None:
        key_pem, cert_pem = make_cert()
        signer = XmlDSigSigner(key_pem=key_pem, cert_pem=cert_pem)
        with pytest.raises(SignerError) as excinfo:
            signer.sign(b"esto no es xml", reference="x")
        assert excinfo.value.code == "EDI_SIGN_INVALID_XML"
