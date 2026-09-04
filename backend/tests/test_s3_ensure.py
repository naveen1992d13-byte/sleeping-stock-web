"""S3 init helpers — no live AWS calls."""

import s3_storage as m


def test_aws_error_code_extracts_code_only():
    class Fake(Exception):
        response = {"Error": {"Code": "AccessDenied", "Message": "do-not-log"}}

    assert m._aws_error_code(Fake()) == "AccessDenied"


def test_aws_error_code_empty_on_plain_exception():
    assert m._aws_error_code(RuntimeError("x")) == ""


def test_ensure_s3_retries_once_then_stops():
    svc = object.__new__(m.S3StorageService)
    svc._mode = "local"
    svc._client = None
    svc._ensure_attempted = False
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
    assert len(calls) == 1


def test_ensure_s3_recovers_on_retry():
    svc = object.__new__(m.S3StorageService)
    svc._mode = "local"
    svc._client = None
    svc._ensure_attempted = False
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
