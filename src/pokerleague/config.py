"""Runtime settings, read from environment variables.

In AWS these come from the CDK stack; locally, unset values switch each
integration to a local stand-in (folder store, outbox folder, local site dir).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    data_bucket: str | None
    data_dir: str
    site_bucket: str | None
    site_dir: str
    distribution_id: str | None
    site_url: str
    admin_url: str
    from_email: str | None
    group_email: str | None
    treasurer_email: str | None
    league_name: str
    bedrock_region: str | None
    admin_password: str
    signing_key: str


@lru_cache(maxsize=1)
def settings() -> Settings:
    env = os.environ.get
    password, key = env("ADMIN_PASSWORD", ""), env("SIGNING_KEY", "")
    if env("ADMIN_PASSWORD_SECRET_ARN") or env("SIGNING_KEY_SECRET_ARN"):
        import boto3

        sm = boto3.client("secretsmanager")

        def secret_field(arn: str, field: str) -> str:
            return json.loads(sm.get_secret_value(SecretId=arn)["SecretString"])[field]

        password = secret_field(env("ADMIN_PASSWORD_SECRET_ARN"), "password")
        key = secret_field(env("SIGNING_KEY_SECRET_ARN"), "signing_key")
    return Settings(
        data_bucket=env("DATA_BUCKET"),
        data_dir=env("DATA_DIR", "data/store"),
        site_bucket=env("SITE_BUCKET"),
        site_dir=env("SITE_DIR", "build/site"),
        distribution_id=env("DISTRIBUTION_ID"),
        site_url=env("SITE_URL", "http://localhost:8765/").rstrip("/") + "/",
        admin_url=env("ADMIN_URL", "http://localhost:8766/").rstrip("/") + "/",
        from_email=env("FROM_EMAIL"),
        group_email=env("GROUP_EMAIL"),
        treasurer_email=env("TREASURER_EMAIL"),
        league_name=env("LEAGUE_NAME", "Hold'em League"),
        bedrock_region=env("BEDROCK_REGION") or env("AWS_REGION"),
        admin_password=password,
        signing_key=key,
    )
