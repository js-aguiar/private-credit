"""Monitoring: CloudWatch error/throttle alarms per scraper, routed to an SNS topic.

Set ``alert_email`` via CDK context (``-c alert_email=you@example.com``) to receive
notifications; otherwise the topic is created without a subscription.
"""

from __future__ import annotations

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct


class MonitoringStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        functions: dict[str, lambda_.IFunction],
        alert_email: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.topic = sns.Topic(self, "AlertsTopic", display_name="BR Sec Scrapers Alerts")
        if alert_email:
            self.topic.add_subscription(subs.EmailSubscription(alert_email))

        action = cw_actions.SnsAction(self.topic)

        for name, function in functions.items():
            errors_alarm = function.metric_errors(
                period=Duration.minutes(5), statistic="Sum"
            ).create_alarm(
                self,
                f"{name.capitalize()}ErrorsAlarm",
                alarm_name=f"{function.function_name}-errors",
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            errors_alarm.add_alarm_action(action)

            throttles_alarm = function.metric_throttles(
                period=Duration.minutes(5), statistic="Sum"
            ).create_alarm(
                self,
                f"{name.capitalize()}ThrottlesAlarm",
                alarm_name=f"{function.function_name}-throttles",
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            throttles_alarm.add_alarm_action(action)
