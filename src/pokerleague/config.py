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
    distribution_id_param: str | None
    site_url: str
    admin_url: str | None
    from_email: str | None
    group_email: str | None
    treasurer_email: str | None
    league_name: str
    bedrock_region: str | None
    admin_password: str
    signing_key: str


def _ssm(name: str | None) -> str | None:
    if not name:
        return None
    import boto3

    return boto3.client("ssm").get_parameter(Name=name)["Parameter"]["Value"]


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
        # In AWS the distribution id sits in an SSM parameter: the distribution routes /admin to this
        # Lambda, so the Lambda can't reference the distribution directly (CloudFormation cycle).
        distribution_id_param=env("DISTRIBUTION_ID_PARAM"),
        site_url=(env("SITE_URL") or _ssm(env("SITE_URL_PARAM")) or "http://localhost:8765/").rstrip("/") + "/",
        # Public base URL of the admin (e.g. the custom domain); unset = derive from the request.
        admin_url=(env("ADMIN_URL").rstrip("/") + "/") if env("ADMIN_URL") else None,
        from_email=env("FROM_EMAIL"),
        group_email=env("GROUP_EMAIL"),
        treasurer_email=env("TREASURER_EMAIL"),
        league_name=env("LEAGUE_NAME", "Hold'em League"),
        bedrock_region=env("BEDROCK_REGION") or env("AWS_REGION"),
        admin_password=password,
        signing_key=key,
    )
