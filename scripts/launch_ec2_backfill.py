#!/usr/bin/env python3
"""Launch a one-off EC2 backfill instance from the BackfillStack launch template."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

try:
    import boto3
except ImportError as exc:  # pragma: no cover
    raise SystemExit("boto3 is required: pip install boto3") from exc


def _stack_output(client, stack_name: str, key: str) -> str:
    paginator = client.get_paginator("describe_stacks")
    for page in paginator.paginate(StackName=stack_name):
        for stack in page.get("Stacks", []):
            for output in stack.get("Outputs", []):
                if output.get("OutputKey") == key:
                    return output["OutputValue"]
    raise RuntimeError(f"Output {key!r} not found on stack {stack_name!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch EC2 backfill instance.")
    parser.add_argument(
        "--stack",
        default=os.getenv("BACKFILL_STACK_NAME", "br-sec-scrapers-backfill"),
        help="CloudFormation stack name for the backfill stack.",
    )
    parser.add_argument(
        "--launch-template-id",
        default=os.getenv("BACKFILL_LAUNCH_TEMPLATE_ID"),
        help="Override launch template ID (skips stack lookup).",
    )
    parser.add_argument(
        "--launch-template-version",
        default=os.getenv("BACKFILL_LAUNCH_TEMPLATE_VERSION", "$Latest"),
    )
    parser.add_argument(
        "--sources",
        default=os.getenv("BACKFILL_SOURCES"),
        help="Comma-separated scrapers to run (stored on instance tag backfill_sources).",
    )
    parser.add_argument("--run-id", default=str(uuid.uuid4()))
    args = parser.parse_args()

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    cf = boto3.client("cloudformation", region_name=region)
    ec2 = boto3.client("ec2", region_name=region)

    lt_id = args.launch_template_id or _stack_output(cf, args.stack, "LaunchTemplateId")
    log_group = _stack_output(cf, args.stack, "LogGroupName")
    subnet_id = _stack_output(cf, args.stack, "BackfillSubnetId")

    sources = (args.sources or "").strip()
    name_suffix = sources.replace(",", "-") if sources else "all"
    tags = [
        {"Key": "Name", "Value": f"{args.stack}-{name_suffix}"},
        {"Key": "br-sec-scrapers:backfill", "Value": "owned"},
        {"Key": "run_id", "Value": args.run_id},
    ]
    if sources:
        tags.append({"Key": "backfill_sources", "Value": sources})

    response = ec2.run_instances(
        LaunchTemplate={
            "LaunchTemplateId": lt_id,
            "Version": args.launch_template_version,
        },
        MinCount=1,
        MaxCount=1,
        SubnetId=subnet_id,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": tags,
            }
        ],
    )

    instance = response["Instances"][0]
    instance_id = instance["InstanceId"]
    result = {
        "instance_id": instance_id,
        "run_id": args.run_id,
        "sources": sources or "all",
        "launch_template_id": lt_id,
        "log_group": log_group,
        "region": region,
        "insights_query": (
            f'fields @timestamp, message, source, execution_mode, run_id | '
            f'filter run_id = "{args.run_id}" | sort @timestamp asc'
        ),
    }
    print(json.dumps(result, indent=2))
    print(
        f"\nTail logs: aws logs tail {log_group} --follow --region {region}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
