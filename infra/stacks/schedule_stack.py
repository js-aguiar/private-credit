"""Twice-daily EventBridge Scheduler invocations of the scraper Lambdas.

Each function is invoked at 10:00 and 18:00 America/Sao_Paulo (GMT-3), optionally
staggered by a few minutes so the shared NAT instance and RDS are not hit by all
four scrapers at once. Daily runs only invoke the existing handlers; schema is not
created or migrated here (``AUTO_CREATE_SCHEMA`` stays false on the Lambdas).
"""

from __future__ import annotations

from aws_cdk import Duration, Stack, TimeZone
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_scheduler_targets as targets
from constructs import Construct

# Stable order so stagger minutes stay predictable across synths.
_SCRAPER_ORDER = ("ecoagro", "opea", "riza", "vert", "bari")


class ScheduleStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        prefix: str,
        functions: dict[str, lambda_.IFunction],
        schedules_enabled: bool = True,
        stagger_minutes: int = 5,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stagger_minutes = max(0, stagger_minutes)
        ordered_names = [name for name in _SCRAPER_ORDER if name in functions]
        ordered_names.extend(name for name in functions if name not in _SCRAPER_ORDER)

        self.schedules: dict[str, scheduler.Schedule] = {}
        for index, name in enumerate(ordered_names):
            minute = index * stagger_minutes
            if minute > 59:
                raise ValueError(
                    f"schedule_stagger_minutes={stagger_minutes} overflows the hour "
                    f"for scraper {name!r} (minute={minute})"
                )
            function = functions[name]
            schedule = scheduler.Schedule(
                self,
                f"Schedule{name.capitalize()}",
                schedule_name=f"{prefix}-{name}-twice-daily",
                description=(
                    f"Invoke {function.function_name} at 10:{minute:02d} and "
                    f"18:{minute:02d} America/Sao_Paulo"
                ),
                schedule=scheduler.ScheduleExpression.cron(
                    minute=str(minute),
                    hour="10,18",
                    time_zone=TimeZone.AMERICA_SAO_PAULO,
                ),
                target=targets.LambdaInvoke(
                    function,
                    retry_attempts=2,
                    max_event_age=Duration.minutes(15),
                ),
                time_window=scheduler.TimeWindow.off(),
                enabled=schedules_enabled,
            )
            self.schedules[name] = schedule
