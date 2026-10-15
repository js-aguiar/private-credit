"""EC2 one-off ANBIMA agenda job: launch template, IAM, code asset, self-teardown."""

from __future__ import annotations

import pathlib

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3_assets as s3_assets
from constructs import Construct

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

AGENDA_TAG_KEY = "br-sec-scrapers:agenda"
AGENDA_TAG_VALUE = "owned"


class AgendaStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        prefix: str,
        vpc: ec2.IVpc,
        database,
        agenda_instance_type: str = "t4g.small",
        agenda_max_hours: int = 24,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        agenda_max_hours = max(1, int(agenda_max_hours))
        agenda_max_seconds = agenda_max_hours * 3600
        private_subnet = vpc.select_subnets(
            subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
        ).subnets[0]

        self.log_group = logs.LogGroup(
            self,
            "AgendaLogGroup",
            log_group_name=f"/{prefix}/ec2-agenda",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        code_asset = s3_assets.Asset(
            self,
            "AgendaCodeAsset",
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
            "AgendaSecurityGroup",
            vpc=vpc,
            description="EC2 ANBIMA agenda scraper",
            allow_all_outbound=True,
        )
        ec2.CfnSecurityGroupIngress(
            self,
            "AgendaToPostgres",
            ip_protocol="tcp",
            from_port=5432,
            to_port=5432,
            group_id=database.security_group.security_group_id,
            source_security_group_id=self.security_group.security_group_id,
            description="Agenda EC2 to PostgreSQL",
        )

        role = iam.Role(
            self,
            "AgendaInstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="Role for one-off EC2 ANBIMA agenda instances",
        )
        database.secret.grant_read(role)
        code_asset.grant_read(role)
        self.log_group.grant_write(role)

        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:DescribeInstances",
                    "ec2:DescribeVolumes",
                    "ec2:DescribeTags",
                ],
                resources=["*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:TerminateInstances",
                    "ec2:DeleteVolume",
                    "ec2:DetachVolume",
                ],
                resources=["*"],
                conditions={
                    "StringEquals": {f"ec2:ResourceTag/{AGENDA_TAG_KEY}": AGENDA_TAG_VALUE}
                },
            )
        )

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "#!/bin/bash",
            "set -euo pipefail",
            "exec > /var/log/agenda-user-data.log 2>&1",
            "mkdir -p /var/log/agenda",
            "dnf install -y awscli unzip",
            f"export AWS_REGION={self.region}",
            f"export CODE_BUCKET={code_asset.s3_bucket_name}",
            f"export CODE_KEY={code_asset.s3_object_key}",
            "export INSTALL_ROOT=/opt/br-sec-scrapers",
            f"export DB_SECRET_ARN={database.secret.secret_arn}",
            f"export DB_NAME={database.database_name}",
            f"export LOG_GROUP_NAME={self.log_group.log_group_name}",
            f"export AGENDA_MAX_SECONDS={agenda_max_seconds}",
            "export AGENDA_INPUT=${AGENDA_INPUT:-scripts/data/anbima_agenda_sample.csv}",
            "export MAX_REQUESTS_PER_MINUTE=10",
            "export REQUEST_DELAY_SECONDS=6",
            "export REQUEST_JITTER_SECONDS=1",
            "export EXECUTION_MODE=ec2_agenda",
            "rm -rf ${INSTALL_ROOT}",
            "mkdir -p ${INSTALL_ROOT}",
            'aws s3 cp "s3://${CODE_BUCKET}/${CODE_KEY}" /tmp/agenda-code.zip',
            "unzip -q /tmp/agenda-code.zip -d ${INSTALL_ROOT}",
            "chmod +x ${INSTALL_ROOT}/scripts/ec2_agenda_bootstrap.sh",
            "chmod +x ${INSTALL_ROOT}/scripts/ec2_agenda_teardown.sh",
            "bash ${INSTALL_ROOT}/scripts/ec2_agenda_bootstrap.sh",
        )

        machine_image = ec2.MachineImage.latest_amazon_linux2023(
            cpu_type=ec2.AmazonLinuxCpuType.ARM_64,
        )

        # Chromium needs a bit more disk than the plain backfill image.
        launch_template = ec2.LaunchTemplate(
            self,
            "AgendaLaunchTemplate",
            launch_template_name=f"{prefix}-agenda",
            instance_type=ec2.InstanceType(agenda_instance_type),
            machine_image=machine_image,
            role=role,
            security_group=self.security_group,
            user_data=user_data,
            require_imdsv2=True,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        30,
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                        delete_on_termination=True,
                    ),
                )
            ],
        )
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
            "AgendaSecurityGroupId",
            value=self.security_group.security_group_id,
        )
        CfnOutput(self, "AgendaMaxHours", value=str(agenda_max_hours))
        CfnOutput(self, "AgendaSubnetId", value=private_subnet.subnet_id)
