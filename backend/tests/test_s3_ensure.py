"""S3 init helpers — no live AWS calls."""

from pathlib import Path

import s3_storage as m


def _client_error(code: str, http: int = 403, message: str = "do-not-log"):
    err = Exception(message)
    err.response = {
        "Error": {"Code": code, "Message": message},
        "ResponseMetadata": {"HTTPStatusCode": http},
    }
    return err


def test_aws_error_code_extracts_code_only():
    class Fake(Exception):
        response = {"Error": {"Code": "AccessDenied", "Message": "do-not-log"}}

    assert m._aws_error_code(Fake()) == "AccessDenied"


def test_aws_error_code_empty_on_plain_exception():
    assert m._aws_error_code(RuntimeError("x")) == ""


def test_empty_overlay_does_not_wipe_process_env(monkeypatch, tmp_path):
    import os

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAPROCESSENVKEY01")  # pragma: allowlist secret
    overlay = tmp_path / ".env.s3.local"
    overlay.write_text("AWS_ACCESS_KEY_ID=\nNMTS_S3_BUCKET=\n", encoding="utf-8")
    m._apply_dotenv_file(overlay, allow_override=True)
    assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIAPROCESSENVKEY01"


def test_overlay_fills_missing_bucket_only(monkeypatch, tmp_path):
    import os

    monkeypatch.delenv("NMTS_S3_BUCKET", raising=False)
    overlay = tmp_path / ".env.s3.local"
    overlay.write_text('NMTS_S3_BUCKET="from-overlay"\n', encoding="utf-8")
    m._apply_dotenv_file(overlay, allow_override=False)
    assert os.environ["NMTS_S3_BUCKET"] == "from-overlay"


def test_overlay_does_not_clobber_existing_secret(monkeypatch, tmp_path):
    import os

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAPROCESSENVKEY01")  # pragma: allowlist secret
    overlay = tmp_path / ".env.s3.local"
    overlay.write_text("AWS_ACCESS_KEY_ID=AKIAFROMFILEKEY0001\n", encoding="utf-8")
    m._apply_dotenv_file(overlay, allow_override=False)
    assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIAPROCESSENVKEY01"


def _service_with_fake_client(monkeypatch, tmp_path, client, *, key="AKIATESTKEYEXAMPLE1", secret="testsecret", bucket="nmts-test-bucket"):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", key)  # pragma: allowlist secret
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret)  # pragma: allowlist secret
    monkeypatch.setenv("NMTS_S3_BUCKET", bucket)  # pragma: allowlist secret
    monkeypatch.setenv("AWS_REGION", "us-east-1")  # pragma: allowlist secret
    monkeypatch.setenv("NMTS_LOCAL_OBJECT_STORE", str(tmp_path))
    monkeypatch.setattr(m, "load_storage_dotenv", lambda force=False: None)
    svc = m.S3StorageService.__new__(m.S3StorageService)
    svc._local = m._LocalObjectStore(Path(tmp_path))
    svc._client = None
    svc._mode = "local"
    svc._make_client = lambda region: client
    svc._refresh_from_env()
    svc._init_client()
    return svc


def test_head_bucket_access_denied_keeps_real_s3(monkeypatch, tmp_path):
    class FakeClient:
        def head_bucket(self, Bucket):
            raise _client_error("AccessDenied", 403)

        def put_object(self, **kwargs):
            return {"ETag": '"abc"'}

    svc = _service_with_fake_client(monkeypatch, tmp_path, FakeClient())
    assert svc.is_s3() is True
    stored = svc.upload_bytes("dev/uploads/probe.xlsx", b"excel-bytes")
    assert stored.storage_provider == "s3"
    assert list(tmp_path.rglob("*.xlsx")) == []


def test_head_bucket_numeric_403_keeps_real_s3(monkeypatch, tmp_path):
    class FakeClient:
        def head_bucket(self, Bucket):
            raise _client_error("403", 403)

        def put_object(self, **kwargs):
            return {"ETag": '"abc"'}

    svc = _service_with_fake_client(monkeypatch, tmp_path, FakeClient())
    assert svc.is_s3() is True
    stored = svc.upload_bytes("dev/uploads/probe.xlsx", b"excel-bytes")
    assert stored.storage_provider == "s3"
    assert list(tmp_path.rglob("*.xlsx")) == []


def test_invalid_access_key_does_not_claim_real_s3(monkeypatch, tmp_path):
    class FakeClient:
        def head_bucket(self, Bucket):
            raise _client_error("InvalidAccessKeyId", 403)

    svc = _service_with_fake_client(monkeypatch, tmp_path, FakeClient())
    assert svc.is_s3() is False
    assert svc.mode == "local"


def test_signature_mismatch_does_not_claim_real_s3(monkeypatch, tmp_path):
    class FakeClient:
        def head_bucket(self, Bucket):
            raise _client_error("SignatureDoesNotMatch", 403)

    svc = _service_with_fake_client(monkeypatch, tmp_path, FakeClient())
    assert svc.is_s3() is False
    assert svc.mode == "local"


def test_missing_credentials_stay_local(monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("NMTS_S3_BUCKET", raising=False)
    monkeypatch.setenv("NMTS_LOCAL_OBJECT_STORE", str(tmp_path))
    monkeypatch.setattr(m, "load_storage_dotenv", lambda force=False: None)
    svc = m.S3StorageService.__new__(m.S3StorageService)
    svc._local = m._LocalObjectStore(Path(tmp_path))
    svc._client = None
    svc._mode = "local"
    svc._refresh_from_env()
    svc._init_client()
    assert svc.is_s3() is False
    stored = svc.upload_bytes("dev/uploads/local.xlsx", b"x")
    assert stored.storage_provider == "local"


def test_upload_bytes_does_not_local_store_when_s3_put_fails(tmp_path):
    class FailPut:
        def put_object(self, **kwargs):
            raise RuntimeError("put denied")

    svc = m.S3StorageService.__new__(m.S3StorageService)
    svc._local = m._LocalObjectStore(Path(tmp_path))
    svc._client = FailPut()
    svc._mode = "s3"
    try:
        svc.upload_bytes("dev/uploads/product-hub/x.xlsx", b"payload")
        assert False, "expected S3StorageError"
    except m.S3StorageError:
        pass
    assert not any(p.is_file() for p in tmp_path.rglob("*"))


def test_ensure_s3_retries_while_local():
    svc = object.__new__(m.S3StorageService)
    svc._mode = "local"
    svc._client = None
    svc.region = "us-east-1"
    svc.bucket = ""
    svc.env = "dev"
    svc.access_key = ""
    svc.secret_key = ""
    calls = []

    def fail_init():
        calls.append(1)
        svc._mode = "local"
        svc._client = None

    svc._init_client = fail_init
    assert svc.ensure_s3() is False
    assert svc.ensure_s3() is False
    assert len(calls) == 2


def test_ensure_s3_recovers_on_retry():
    svc = object.__new__(m.S3StorageService)
    svc._mode = "local"
    svc._client = None
    svc.region = "us-east-1"
    svc.bucket = "bucket"
    svc.env = "dev"
    svc.access_key = ""
    svc.secret_key = ""

    def ok_init():
        svc._mode = "s3"
        svc._client = object()

    svc._init_client = ok_init
    assert svc.ensure_s3() is True
    assert svc.is_s3() is True
    assert svc.ensure_s3() is True
