# Catalog API Lambda image. Build context is the repository root:
#   docker build -f web/api/Dockerfile -t catalog-api .
FROM public.ecr.aws/lambda/python:3.12

COPY web/api/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY shared/ ${LAMBDA_TASK_ROOT}/shared/
COPY web/api/handler.py ${LAMBDA_TASK_ROOT}/handler.py
COPY web/api/queries.py ${LAMBDA_TASK_ROOT}/queries.py

CMD ["handler.handler"]
