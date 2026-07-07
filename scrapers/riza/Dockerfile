# Riza Lambda image (SPA -> needs headless Chromium for the browser-capture fallback).
# Build context is the repository root:
#   docker build -f scrapers/riza/Dockerfile -t riza .
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

ENV LAMBDA_TASK_ROOT=/var/task
WORKDIR ${LAMBDA_TASK_ROOT}

RUN pip install --no-cache-dir awslambdaric

COPY scrapers/riza/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY scrapers/riza/ ./app/

ENV PYTHONPATH=${LAMBDA_TASK_ROOT}
ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["app.handler.handler"]
