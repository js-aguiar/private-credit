"""Networking: a VPC with a low-cost NAT instance for outbound scraping traffic.

Layout (2 AZs):
  - public subnets  : host the NAT instance
  - private (egress): host the scraper Lambdas (reach the internet via the NAT instance)
  - isolated subnets: host RDS (no internet route)

A single NAT *instance* (t4g.nano) is used instead of a managed NAT Gateway to minimize
cost. Swap in ``ec2.Vpc(..., nat_gateways=1)`` (default provider) if you prefer the
managed option.
"""

from __future__ import annotations

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class NetworkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        nat_provider = ec2.NatProvider.instance_v2(
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.NANO
            ),
        )

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateway_provider=nat_provider,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        # Allow instances inside the VPC to route through the NAT instance.
        nat_provider.security_group.add_ingress_rule(
            ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            ec2.Port.all_traffic(),
            "Allow VPC hosts to use the NAT instance",
        )
