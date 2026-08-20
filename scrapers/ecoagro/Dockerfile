# Ecoagro Lambda image. Build context is the repository root:
#   docker build -f scrapers/ecoagro/Dockerfile -t ecoagro .
# Ecoagro is server-rendered HTML, so no headless browser is bundled here.
FROM public.ecr.aws/lambda/python:3.12

# Install runtime dependencies.
COPY scrapers/ecoagro/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Shared library (top-level package) + scraper code (as the `app` package).
COPY shared/ ${LAMBDA_TASK_ROOT}/shared/
COPY scrapers/ecoagro/ ${LAMBDA_TASK_ROOT}/app/

# handler = <package>.<module>.<function>
CMD ["app.handler.handler"]
