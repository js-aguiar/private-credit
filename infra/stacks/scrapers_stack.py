"""Scrapers: one Lambda container function per website, built from each Dockerfile.

Each function runs in the private-with-egress subnets (internet via the NAT instance,
database via the RDS security group), reads its DB credentials from Secrets Manager, and
reads runtime tunables (delay, rate cap, ...) from SSM Parameter Store.

Schedules live in ``ScheduleStack``; this stack only defines the functions.
"""

from __future__ import annotations

import pathlib

from aws_cdk import Duration, Size, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_ssm as ssm
from constructs import Construct

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Per-scraper build/runtime settings. SPA scrapers bundle headless Chromium, so they need
# more memory + ephemeral storage; Ecoagro is plain HTML and stays lean.
SCRAPER_SPECS: dict[str, dict] = {
    "ecoagro": {"dockerfile": "scrapers/ecoagro/Dockerfile", "memory": 1024, "browser": "false", "ephemeral_mb": 512},
    "opea": {"dockerfile": "scrapers/opea/Dockerfile", "memory": 2048, "browser": "true", "ephemeral_mb": 2048},
    "riza": {"dockerfile": "scrapers/riza/Dockerfile", "memory": 1024, "browser": "false", "ephemeral_mb": 512},
    "vert": {"dockerfile": "scrapers/vert/Dockerfile", "memory": 1024, "browser": "false", "ephemeral_mb": 512},
}

# Default runtime tunables published to SSM (editable in the console without redeploy).
DEFAULT_SSM_TUNABLES = {
    "request_delay_seconds": "8",
    "request_jitter_seconds": "4",
    "max_requests_per_minute": "6",
    "detail_batch_limit": "5000",
}


class ScrapersStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        prefix: str,
        vpc: ec2.IVpc,
        database,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.lambda_security_group = ec2.SecurityGroup(
            self,
            "LambdaSecurityGroup",
            vpc=vpc,
            description="Scraper Lambdas",
            allow_all_outbound=True,
        )
        # Ingress lives in this stack (not DatabaseStack) to avoid a cyclic export.
        ec2.CfnSecurityGroupIngress(
            self,
            "ScrapersToPostgres",
            ip_protocol="tcp",
            from_port=5432,
            to_port=5432,
            group_id=database.security_group.security_group_id,
            source_security_group_id=self.lambda_security_group.security_group_id,
            description="Scraper Lambdas to PostgreSQL",
        )

        self.functions: dict[str, lambda_.DockerImageFunction] = {}

        for name, spec in SCRAPER_SPECS.items():
            ssm_prefix = f"/{prefix}/{name}/"

            function = lambda_.DockerImageFunction(
                self,
                f"Scraper{name.capitalize()}",
                function_name=f"{prefix}-{name}",
                code=lambda_.DockerImageCode.from_image_asset(
                    directory=str(REPO_ROOT), file=spec["dockerfile"]
                ),
                memory_size=spec["memory"],
                ephemeral_storage_size=Size.mebibytes(spec["ephemeral_mb"]),
                timeout=Duration.minutes(15),
                vpc=vpc,
                vpc_subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ),
                security_groups=[self.lambda_security_group],
                environment={
                    "DB_SECRET_ARN": database.secret.secret_arn,
                    "DB_NAME": database.database_name,
                    "DB_SSLMODE": "require",
                    "SSM_PREFIX": ssm_prefix,
                    "USE_BROWSER_FALLBACK": spec["browser"],
                    "AUTO_CREATE_SCHEMA": "false",
                    "LOG_LEVEL": "INFO",
                },
                log_retention=logs.RetentionDays.ONE_MONTH,
            )

            database.secret.grant_read(function)

            # Publish default tunables and allow the function to read its SSM path.
            for param_name, param_value in DEFAULT_SSM_TUNABLES.items():
                ssm.StringParameter(
                    self,
                    f"Param{name.capitalize()}{param_name.title().replace('_', '')}",
                    parameter_name=f"{ssm_prefix}{param_name}",
                    string_value=param_value,
                )
            function.add_to_role_policy(
                iam.PolicyStatement(
                    actions=[
                        "ssm:GetParameter",
                        "ssm:GetParameters",
                        "ssm:GetParametersByPath",
                    ],
                    resources=[
                        f"arn:aws:ssm:{self.region}:{self.account}:parameter{ssm_prefix}*"
                    ],
                )
            )

            self.functions[name] = function
