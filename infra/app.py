#!/usr/bin/env python3
"""AWS infrastructure for the poker league (AWS CDK, Python).

Creates:
  - a private, versioned S3 bucket for league data (players, tournaments, raw logs)
  - a private S3 bucket + CloudFront distribution for the public website
  - the admin Lambda (function URL) that handles uploads, review and approval
  - two generated secrets: the admin password and the session signing key

Settings come from CDK context (cdk.json or `-c key=value`):
  group_email      Google Group address the recap goes to
  treasurer_email  who gets "ready to review" emails
  from_email       verified SES sender (must be allowed to post to the group)
  league_name      shown on the admin pages
  recap_provider   "anthropic" (default, uses the AnthropicApiKey secret) or "bedrock"
  recap_model      model id (default claude-opus-5-5)
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_ses as ses,
)
from constructs import Construct

LAMBDA_BUILD = Path(__file__).resolve().parents[1] / "build" / "lambda"


class PokerLeagueStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        ctx = lambda key, default="": self.node.try_get_context(key) or default  # noqa: E731

        data_bucket = s3.Bucket(
            self, "DataBucket",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[s3.LifecycleRule(noncurrent_version_expiration=cdk.Duration.days(365))],
        )
        site_bucket = s3.Bucket(
            self, "SiteBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        distribution = cloudfront.Distribution(
            self, "Site",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
            ),
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
        )

        password = secretsmanager.Secret(
            self, "AdminPassword",
            description="Poker league admin password (share with hosts and the treasurer)",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"user": "admin"}', generate_string_key="password",
                exclude_punctuation=True, password_length=20),
        )
        signing_key = secretsmanager.Secret(
            self, "SigningKey",
            description="Signs admin session cookies",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template="{}", generate_string_key="signing_key",
                exclude_punctuation=True, password_length=48),
        )

        recap_provider = ctx("recap_provider", "anthropic")
        # Holds a placeholder until you store the real key (see the README); never put the key in code.
        anthropic_key = secretsmanager.Secret(
            self, "AnthropicApiKey",
            description="Anthropic API key used to write the weekly recap",
            secret_string_value=cdk.SecretValue.unsafe_plain_text("REPLACE_ME"),
        )

        from_email = ctx("from_email")
        if from_email:
            # Sends a verification email to this address on first deploy.
            ses.EmailIdentity(self, "Sender", identity=ses.Identity.email(from_email))

        fn = lambda_.Function(
            self, "Admin",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="pokerleague.handlers.admin.handler",
            code=lambda_.Code.from_asset(str(LAMBDA_BUILD)),
            memory_size=1024,
            timeout=cdk.Duration.seconds(120),
            log_group=logs.LogGroup(self, "AdminLogs", retention=logs.RetentionDays.THREE_MONTHS,
                                    removal_policy=cdk.RemovalPolicy.DESTROY),
            environment={
                "DATA_BUCKET": data_bucket.bucket_name,
                "SITE_BUCKET": site_bucket.bucket_name,
                "DISTRIBUTION_ID": distribution.distribution_id,
                "SITE_URL": f"https://{distribution.distribution_domain_name}/",
                "ADMIN_PASSWORD_SECRET_ARN": password.secret_arn,
                "SIGNING_KEY_SECRET_ARN": signing_key.secret_arn,
                "FROM_EMAIL": from_email,
                "GROUP_EMAIL": ctx("group_email"),
                "TREASURER_EMAIL": ctx("treasurer_email"),
                "LEAGUE_NAME": ctx("league_name", "Hold'em League"),
                "RECAP_PROVIDER": recap_provider,
                "RECAP_MODEL": ctx("recap_model", "claude-opus-5-5" if recap_provider == "anthropic" else "anthropic.claude-opus-5-5"),
                "ANTHROPIC_API_KEY_SECRET_ARN": anthropic_key.secret_arn,
                "BEDROCK_REGION": ctx("bedrock_region", self.region),
            },
        )
        anthropic_key.grant_read(fn)
        data_bucket.grant_read_write(fn)
        site_bucket.grant_read_write(fn)
        distribution.grant_create_invalidation(fn)
        password.grant_read(fn)
        signing_key.grant_read(fn)
        fn.add_to_role_policy(iam.PolicyStatement(actions=["ses:SendEmail"], resources=["*"]))
        if recap_provider == "bedrock":
            # Claude in Amazon Bedrock (Messages API endpoint).
            fn.add_to_role_policy(iam.PolicyStatement(actions=["bedrock-mantle:CreateInference"], resources=["*"]))

        url = fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)

        cdk.CfnOutput(self, "WebsiteUrl", value=f"https://{distribution.distribution_domain_name}/")
        cdk.CfnOutput(self, "AdminUrl", value=f"{url.url}admin")
        cdk.CfnOutput(self, "AdminPasswordSecret", value=password.secret_name,
                      description="aws secretsmanager get-secret-value --secret-id <this> --query SecretString")
        cdk.CfnOutput(self, "AnthropicApiKeySecret", value=anthropic_key.secret_name,
                      description="aws secretsmanager put-secret-value --secret-id <this> --secret-string <your key>")
        cdk.CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)


app = cdk.App()
PokerLeagueStack(app, "PokerLeague", env=cdk.Environment(
    account=app.node.try_get_context("account"), region=app.node.try_get_context("region") or "us-east-1"))
app.synth()
