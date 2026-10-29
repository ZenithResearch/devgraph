"""Native test TLS remains proper server TLS; no application trust bypass."""

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID
from tests.native_read.holder_session_fixture import tls_files


def test_disposable_certificate_is_explicit_server_auth_end_entity(tmp_path):
    path, key = tls_files(tmp_path)
    certificate = x509.load_pem_x509_certificate(path.read_bytes())
    assert isinstance(certificate.public_key(), ec.EllipticCurvePublicKey)
    assert certificate.extensions.get_extension_for_class(
        x509.ExtendedKeyUsage
    ).value == x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH])
    constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    assert (
        constraints.critical
        and constraints.value.ca is False
        and constraints.value.path_length is None
    )
    usage = certificate.extensions.get_extension_for_class(x509.KeyUsage)
    assert usage.critical and usage.value.digital_signature and not usage.value.key_cert_sign
    assert not usage.value.crl_sign and not usage.value.key_encipherment
    assert path.stat().st_mode & 0o777 == 0o600
    assert key.stat().st_mode & 0o777 == 0o600
