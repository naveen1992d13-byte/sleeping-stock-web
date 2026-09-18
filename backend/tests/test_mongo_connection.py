"""Isolated tests for DocumentDB TLS-aware Mongo client args. No live DB."""
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from mongo_connection import (
    build_mongo_client_args,
    ensure_documentdb_uri_options,
    mongo_url_from_documentdb_secret,
    resolve_mongo_url,
)


ATLAS_URL = "mongodb+srv://user:pass@cluster.mongodb.net/nmts?retryWrites=true&w=majority"
PLAIN_URL = "mongodb://localhost:27017"
DOCDB_URL = "mongodb://user:pass@docdb-cluster.cluster-xxxx.us-east-1.docdb.amazonaws.com:27017/?authSource=admin"
CA_PATH = "/etc/ssl/certs/global-bundle.pem"


def test_atlas_path_no_tls_args_when_ca_unset():
    url, kwargs = build_mongo_client_args(PLAIN_URL, env={})
    assert url == PLAIN_URL
    assert kwargs == {}


def test_atlas_path_preserves_existing_uri_exactly():
    url, kwargs = build_mongo_client_args(ATLAS_URL, env={})
    assert url is ATLAS_URL or url == ATLAS_URL
    assert kwargs == {}


def test_blank_ca_file_is_treated_as_unset():
    url, kwargs = build_mongo_client_args(PLAIN_URL, env={"DOCDB_TLS_CA_FILE": "   "})
    assert url == PLAIN_URL
    assert kwargs == {}


def test_documentdb_tls_args_when_ca_set():
    url, kwargs = build_mongo_client_args(DOCDB_URL, env={"DOCDB_TLS_CA_FILE": CA_PATH})
    assert kwargs == {
        "tls": True,
        "tlsCAFile": CA_PATH,
        "retryWrites": False,
        "readPreference": "secondaryPreferred",
    }
    assert "retryWrites=false" in url
    assert "readPreference=secondaryPreferred" in url
    assert "authSource=admin" in url


def test_documentdb_overrides_retry_writes_true_in_url():
    raw = "mongodb://host:27017/?retryWrites=true&readPreference=primary"
    url, kwargs = build_mongo_client_args(raw, env={"DOCDB_TLS_CA_FILE": CA_PATH})
    assert kwargs["retryWrites"] is False
    assert kwargs["readPreference"] == "secondaryPreferred"
    assert "retryWrites=false" in url
    assert "retryWrites=true" not in url
    assert "readPreference=secondaryPreferred" in url


def test_ensure_documentdb_uri_options_preserves_other_query_params():
    url = ensure_documentdb_uri_options("mongodb://host:27017/?authSource=admin&tls=true")
    assert "authSource=admin" in url
    assert "tls=true" in url
    assert "retryWrites=false" in url
    assert "readPreference=secondaryPreferred" in url


def test_os_environ_used_when_env_not_passed(monkeypatch):
    monkeypatch.setenv("DOCDB_TLS_CA_FILE", CA_PATH)
    url, kwargs = build_mongo_client_args(PLAIN_URL)
    assert kwargs["tls"] is True
    assert kwargs["tlsCAFile"] == CA_PATH
    assert "retryWrites=false" in url


def test_secret_payload_builds_url_without_exposing_password():
    url = mongo_url_from_documentdb_secret(
        {
            "host": "docdb.cluster-xxxx.us-east-1.docdb.amazonaws.com",
            "username": "nmts_app",
            "password": "p@ss/word:1",
            "port": "27017",
        }
    )
    assert url.startswith("mongodb://nmts_app:p%40ss%2Fword%3A1@")
    assert "docdb.cluster-xxxx.us-east-1.docdb.amazonaws.com:27017" in url
    assert "authSource=admin" in url
    assert "p@ss/word:1" not in url


def test_resolve_mongo_url_uses_atlas_when_ca_unset():
    url = resolve_mongo_url(env={"MONGO_URL": PLAIN_URL})
    assert url == PLAIN_URL


def test_resolve_mongo_url_fetches_secret_when_ca_set(monkeypatch):
    captured = {}

    class FakeSM:
        def get_secret_value(self, SecretId):
            captured["SecretId"] = SecretId
            return {
                "SecretString": (
                    '{"host":"docdb.example.docdb.amazonaws.com",'
                    '"username":"nmts_app","password":"secret","port":27017}'
                )
            }

    monkeypatch.setattr(
        "mongo_connection.boto3.client",
        lambda service, region_name=None: FakeSM(),
    )
    url = resolve_mongo_url(
        env={
            "DOCDB_TLS_CA_FILE": CA_PATH,
            "DOCDB_SECRET_ID": "nmts/documentdb/credentials",
            "AWS_REGION": "us-east-1",
            "MONGO_URL": PLAIN_URL,
        }
    )
    assert captured["SecretId"] == "nmts/documentdb/credentials"
    assert "docdb.example.docdb.amazonaws.com:27017" in url
    assert PLAIN_URL not in url
    # Atlas rollback URL must not be used when DocumentDB TLS is enabled.
    assert url.startswith("mongodb://nmts_app:")
