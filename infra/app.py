#!/usr/bin/env python3
"""AWS CDK application entrypoint.

Provisions the scraping infrastructure across seven stacks:
  - network     : VPC + low-cost NAT instance
  - database    : RDS PostgreSQL (db.t4g.micro, single-AZ, private)
  - scrapers    : five Lambda container functions + IAM + SSM tunables
  - backfill    : EC2 launch template for one-off full backfill runs
  - schedule    : EventBridge Scheduler (10:00 and 18:00 America/Sao_Paulo)
  - web         : S3 + CloudFront catalog UI and read-only VPC Lambda API
  - monitoring  : CloudWatch alarms + SNS alerts
"""

from __future__ import annotations

import os

import aws_cdk as cdk

from stacks.backfill_stack import BackfillStack
from stacks.database_stack import DatabaseStack
from stacks.monitoring_stack import MonitoringStack
from stacks.network_stack import NetworkStack
from stacks.schedule_stack import ScheduleStack
from stacks.scrapers_stack import ScrapersStack
from stacks.web_stack import WebStack


def _context_bool(app: cdk.App, key: str, default: bool) -> bool:
    value = app.node.try_get_context(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _context_int(app: cdk.App, key: str, default: int) -> int:
    value = app.node.try_get_context(key)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _context_str(app: cdk.App, key: str, default: str) -> str:
    value = app.node.try_get_context(key)
    if value is None or value == "":
        return default
    return str(value)


app = cdk.App()

prefix = app.node.try_get_context("prefix") or "br-sec-scrapers"
alert_email = app.node.try_get_context("alert_email") or None
schedules_enabled = _context_bool(app, "schedules_enabled", True)
schedule_stagger_minutes = _context_int(app, "schedule_stagger_minutes", 5)
backfill_instance_type = _context_str(app, "backfill_instance_type", "t4g.small")
backfill_max_hours = _context_int(app, "backfill_max_hours", 24)

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

BackfillStack(
    app,
    f"{prefix}-backfill",
    prefix=prefix,
    vpc=network.vpc,
    database=database,
    backfill_instance_type=backfill_instance_type,
    backfill_max_hours=backfill_max_hours,
    env=env,
)

ScheduleStack(
    app,
    f"{prefix}-schedule",
    prefix=prefix,
    functions=scrapers.functions,
    schedules_enabled=schedules_enabled,
    stagger_minutes=schedule_stagger_minutes,
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
