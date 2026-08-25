"""Public document catalog: S3 + CloudFront UI and a VPC Lambda API.

The static site never receives database credentials. CloudFront serves HTML/CSS/JS from a
private S3 bucket (Origin Access Control) and proxies ``/api/*`` to API Gateway HTTP API,
which invokes a read-only Lambda in private-with-egress subnets. That Lambda reads RDS
credentials from Secrets Manager and connects with TLS (``sslmode=require``).
"""

from __future__ import annotations

import pathlib

from aws_cdk import CfnOutput, Duration, Fn, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web"


class WebStack(Stack):
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

        api_sg = ec2.SecurityGroup(
            self,
            "CatalogApiSecurityGroup",
            vpc=vpc,
            description="Catalog API Lambda",
            allow_all_outbound=True,
        )
        # Ingress lives in this stack (not DatabaseStack) to avoid a cyclic export.
        ec2.CfnSecurityGroupIngress(
            self,
            "CatalogApiToPostgres",
            ip_protocol="tcp",
            from_port=5432,
            to_port=5432,
            group_id=database.security_group.security_group_id,
            source_security_group_id=api_sg.security_group_id,
            description="Catalog API Lambda to PostgreSQL",
        )

        api_fn = lambda_.DockerImageFunction(
            self,
            "CatalogApi",
            function_name=f"{prefix}-catalog-api",
            code=lambda_.DockerImageCode.from_image_asset(
                directory=str(REPO_ROOT), file="web/api/Dockerfile"
            ),
            memory_size=512,
            timeout=Duration.seconds(15),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[api_sg],
            environment={
                "DB_SECRET_ARN": database.secret.secret_arn,
                "DB_NAME": database.database_name,
                "DB_SSLMODE": "require",
                "AUTO_CREATE_SCHEMA": "false",
                "LOG_LEVEL": "INFO",
            },
            log_retention=logs.RetentionDays.ONE_MONTH,
        )
        database.secret.grant_read(api_fn)

        http_api = apigwv2.HttpApi(
            self,
            "CatalogHttpApi",
            api_name=f"{prefix}-catalog",
            description="Read-only document catalog API",
        )
        integration = apigwv2_integrations.HttpLambdaIntegration(
            "CatalogIntegration", api_fn
        )
        http_api.add_routes(
            path="/api",
            methods=[apigwv2.HttpMethod.GET, apigwv2.HttpMethod.OPTIONS],
            integration=integration,
        )
        http_api.add_routes(
            path="/api/{proxy+}",
            methods=[apigwv2.HttpMethod.GET, apigwv2.HttpMethod.OPTIONS],
            integration=integration,
        )
        # Keep a public catalog from flooding the shared t4g.micro RDS.
        cfn_stage = http_api.default_stage.node.default_child
        cfn_stage.add_property_override(
            "DefaultRouteSettings",
            {"ThrottlingRateLimit": 50, "ThrottlingBurstLimit": 100},
        )

        bucket = s3.Bucket(
            self,
            "CatalogBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        s3_origin = origins.S3BucketOrigin.with_origin_access_control(bucket)
        api_origin = origins.HttpOrigin(
            Fn.select(2, Fn.split("/", http_api.api_endpoint)),
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
        )

        # Clean /documentos path → static file (default_root_object only covers /).
        documentos_rewrite = cloudfront.Function(
            self,
            "DocumentosPathRewrite",
            code=cloudfront.FunctionCode.from_inline(
                """
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri === '/documentos' || uri === '/documentos/') {
    request.uri = '/documentos.html';
  }
  return request;
}
"""
            ),
        )

        distribution = cloudfront.Distribution(
            self,
            "CatalogCdn",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=documentos_rewrite,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    )
                ],
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=api_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                ),
            },
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
            ],
        )

        s3deploy.BucketDeployment(
            self,
            "CatalogUiDeploy",
            destination_bucket=bucket,
            distribution=distribution,
            distribution_paths=["/*"],
            sources=[
                s3deploy.Source.asset(
                    str(WEB_DIR),
                    exclude=[
                        "api",
                        "api/**",
                        "**/*.py",
                        "**/Dockerfile",
                        "**/requirements.txt",
                        "**/__pycache__/**",
                    ],
                )
            ],
        )

        self.distribution = distribution
        CfnOutput(
            self,
            "CatalogUrl",
            value=f"https://{distribution.distribution_domain_name}",
            description="HTTPS URL of the document catalog",
        )
