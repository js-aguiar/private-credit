#!/usr/bin/env python3
"""AWS CDK application entrypoint.

Provisions the scraping infrastructure across five stacks:
  - network     : VPC + low-cost NAT instance
  - database    : RDS PostgreSQL (db.t4g.micro, single-AZ, private)
  - scrapers    : four Lambda container functions + IAM + SSM tunables
  - web         : S3 + CloudFront catalog UI and read-only VPC Lambda API
  - monitoring  : CloudWatch alarms + SNS alerts

No schedule/automation is created (per project scope); invoke the Lambdas on demand.
"""

from __future__ import annotations

import os

import aws_cdk as cdk

from stacks.database_stack import DatabaseStack
from stacks.monitoring_stack import MonitoringStack
from stacks.network_stack import NetworkStack
from stacks.scrapers_stack import ScrapersStack
from stacks.web_stack import WebStack

app = cdk.App()

prefix = app.node.try_get_context("prefix") or "br-sec-scrapers"
alert_email = app.node.try_get_context("alert_email") or None

env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION"),
)

network = NetworkStack(app, f"{prefix}-network", env=env)

database = DatabaseStack(app, f"{prefix}-database", vpc=network.vpc, env=env)

scrapers = ScrapersStack(
    app,
    f"{prefix}-scrapers",
    prefix=prefix,
    vpc=network.vpc,
    database=database,
    env=env,
)

WebStack(
    app,
    f"{prefix}-web",
    prefix=prefix,
    vpc=network.vpc,
    database=database,
    env=env,
)

MonitoringStack(
    app,
    f"{prefix}-monitoring",
    functions=scrapers.functions,
    alert_email=alert_email,
    env=env,
)

app.synth()
