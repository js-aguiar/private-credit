"""Database: the cheapest sensible RDS PostgreSQL for a small dataset.

db.t4g.micro (Graviton, burstable), single-AZ, GP3 storage, private (isolated subnets),
not publicly accessible. Master credentials are generated into a Secrets Manager secret.
"""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from constructs import Construct

DATABASE_NAME = "securitizacao"


class DatabaseStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, vpc: ec2.IVpc, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.security_group = ec2.SecurityGroup(
            self,
            "DbSecurityGroup",
            vpc=vpc,
            description="RDS PostgreSQL access for the scraper Lambdas",
            allow_all_outbound=True,
        )

        self.instance = rds.DatabaseInstance(
            self,
            "Postgres",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MICRO
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[self.security_group],
            multi_az=False,
            allocated_storage=20,
            max_allocated_storage=100,
            storage_type=rds.StorageType.GP3,
            publicly_accessible=False,
            database_name=DATABASE_NAME,
            credentials=rds.Credentials.from_generated_secret(
                "scraper_admin", secret_name=f"{construct_id}/db-credentials"
            ),
            backup_retention=Duration.days(7),
            deletion_protection=False,
            storage_encrypted=True,
            # Keep a final snapshot rather than silently losing data on stack deletion.
            removal_policy=RemovalPolicy.SNAPSHOT,
            cloudwatch_logs_exports=["postgresql"],
        )

        self.secret = self.instance.secret
        self.database_name = DATABASE_NAME
