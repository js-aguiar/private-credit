# Riza Lambda image. Build context is the repository root:
#   docker build -f scrapers/riza/Dockerfile -t riza .
# Uses the public Virgo BFF API via httpx (no headless browser).
FROM public.ecr.aws/lambda/python:3.12

ENV LAMBDA_TASK_ROOT=/var/task
WORKDIR ${LAMBDA_TASK_ROOT}

COPY scrapers/riza/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt awslambdaric

COPY shared/ ./shared/
COPY scrapers/riza/ ./app/

ENV PYTHONPATH=${LAMBDA_TASK_ROOT}
ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["app.handler.handler"]
