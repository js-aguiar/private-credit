"""EC2 one-off backfill: launch template, IAM, code asset, and backfill SSM tunables."""

from __future__ import annotations

import pathlib

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3_assets as s3_assets
from aws_cdk import aws_ssm as ssm
from constructs import Construct

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

BACKFILL_TAG_KEY = "br-sec-scrapers:backfill"
BACKFILL_TAG_VALUE = "owned"

# Shared with Lambda defaults except politeness (overridden below for backfill).
BACKFILL_SSM_TUNABLES = {
    "request_delay_seconds": "2",
    "request_jitter_seconds": "1",
    "max_requests_per_minute": "20",
    "detail_batch_limit": "5000",
}


class BackfillStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        prefix: str,
        vpc: ec2.IVpc,
        database,
        backfill_instance_type: str = "t4g.small",
        backfill_max_hours: int = 24,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        backfill_max_hours = max(1, int(backfill_max_hours))
        backfill_max_seconds = backfill_max_hours * 3600
        ssm_prefix = f"/{prefix}/backfill"
        private_subnet = vpc.select_subnets(
            subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
        ).subnets[0]

        self.log_group = logs.LogGroup(
            self,
            "BackfillLogGroup",
            log_group_name=f"/{prefix}/ec2-backfill",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        for param_name, param_value in BACKFILL_SSM_TUNABLES.items():
            ssm.StringParameter(
                self,
                f"BackfillParam{param_name.title().replace('_', '')}",
                parameter_name=f"{ssm_prefix}/{param_name}",
                string_value=param_value,
            )

        code_asset = s3_assets.Asset(
            self,
            "BackfillCodeAsset",
            path=str(REPO_ROOT),
            exclude=[
                "**/.venv/**",
                "**/.git/**",
                "**/infra/cdk.out/**",
                "**/web/**",
                "**/__pycache__/**",
                "**/*.pyc",
                "**/.pytest_cache/**",
                "**/.mypy_cache/**",
                "**/.ruff_cache/**",
            ],
        )

        self.security_group = ec2.SecurityGroup(
            self,
            "BackfillSecurityGroup",
            vpc=vpc,
            description="EC2 backfill scraper",
            allow_all_outbound=True,
        )
        ec2.CfnSecurityGroupIngress(
            self,
            "BackfillToPostgres",
            ip_protocol="tcp",
            from_port=5432,
            to_port=5432,
            group_id=database.security_group.security_group_id,
            source_security_group_id=self.security_group.security_group_id,
            description="Backfill EC2 to PostgreSQL",
        )

        role = iam.Role(
            self,
            "BackfillInstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="Role for one-off EC2 backfill instances",
        )
        database.secret.grant_read(role)
        code_asset.grant_read(role)
        self.log_group.grant_write(role)

        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ssm:GetParameter",
                    "ssm:GetParameters",
                    "ssm:GetParametersByPath",
                ],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/{prefix}/*",
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:TerminateInstances",
                    "ec2:DeleteVolume",
                    "ec2:DetachVolume",
                    "ec2:DescribeInstances",
                    "ec2:DescribeVolumes",
                ],
                resources=["*"],
                conditions={
                    "StringEquals": {f"ec2:ResourceTag/{BACKFILL_TAG_KEY}": BACKFILL_TAG_VALUE}
                },
            )
        )

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "#!/bin/bash",
            "set -euo pipefail",
            "exec > /var/log/backfill-user-data.log 2>&1",
            "mkdir -p /var/log/backfill",
            "dnf install -y awscli unzip",
            f"export AWS_REGION={self.region}",
            f"export CODE_BUCKET={code_asset.s3_bucket_name}",
            f"export CODE_KEY={code_asset.s3_object_key}",
            "export INSTALL_ROOT=/opt/br-sec-scrapers",
            f"export DB_SECRET_ARN={database.secret.secret_arn}",
            f"export DB_NAME={database.database_name}",
            f"export SSM_PREFIX={ssm_prefix}/",
            f"export LOG_GROUP_NAME={self.log_group.log_group_name}",
            f"export BACKFILL_MAX_SECONDS={backfill_max_seconds}",
            "export USE_BROWSER_FALLBACK=false",
            "rm -rf ${INSTALL_ROOT}",
            "mkdir -p ${INSTALL_ROOT}",
            'aws s3 cp "s3://${CODE_BUCKET}/${CODE_KEY}" /tmp/backfill-code.zip',
            "unzip -q /tmp/backfill-code.zip -d ${INSTALL_ROOT}",
            "chmod +x ${INSTALL_ROOT}/scripts/ec2_backfill_bootstrap.sh",
            "chmod +x ${INSTALL_ROOT}/scripts/ec2_backfill_teardown.sh",
            "bash ${INSTALL_ROOT}/scripts/ec2_backfill_bootstrap.sh",
        )

        machine_image = ec2.MachineImage.latest_amazon_linux2023(
            cpu_type=ec2.AmazonLinuxCpuType.ARM_64,
        )

        launch_template = ec2.LaunchTemplate(
            self,
            "BackfillLaunchTemplate",
            launch_template_name=f"{prefix}-backfill",
            instance_type=ec2.InstanceType(backfill_instance_type),
            machine_image=machine_image,
            role=role,
            security_group=self.security_group,
            user_data=user_data,
            require_imdsv2=True,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        20,
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                        delete_on_termination=True,
                    ),
                )
            ],
        )

        # Ensure launch template updates when the bundled code asset changes.
        launch_template.node.add_dependency(code_asset)

        CfnOutput(self, "LaunchTemplateId", value=launch_template.launch_template_id or "")
        CfnOutput(
            self,
            "LaunchTemplateLatestVersion",
            value=launch_template.latest_version_number or "",
        )
        CfnOutput(self, "LogGroupName", value=self.log_group.log_group_name)
        CfnOutput(
            self,
            "BackfillSecurityGroupId",
            value=self.security_group.security_group_id,
        )
        CfnOutput(self, "BackfillSsmPrefix", value=f"{ssm_prefix}/")
        CfnOutput(self, "BackfillMaxHours", value=str(backfill_max_hours))
        CfnOutput(self, "BackfillSubnetId", value=private_subnet.subnet_id)
