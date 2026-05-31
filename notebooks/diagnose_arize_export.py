# Databricks notebook source
# MAGIC %md
# MAGIC # Diagnose Arize OTLP export (isolated test)
# MAGIC
# MAGIC Run this when UC has spans but **no project appears in Arize AX**.
# MAGIC Does **not** write to Databricks — Arize only.

# COMMAND ----------

# MAGIC %pip install arize-otel openinference-semantic-conventions --quiet
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import logging
import os

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("opentelemetry.exporter.otlp.proto.grpc.trace_exporter").setLevel(
    logging.DEBUG
)
logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").setLevel(
    logging.DEBUG
)

dbutils.widgets.text("arize_project_name", "databricks-dual-ingest-demo", "Arize project")
project = dbutils.widgets.get("arize_project_name").strip()

space_id = os.environ.get("ARIZE_SPACE_ID") or os.environ.get("ARIZE_SPACE")
api_key = os.environ.get("ARIZE_API_KEY")
if not space_id or not api_key:
    raise ValueError("Set ARIZE_SPACE_ID and ARIZE_API_KEY on the cluster.")

print(f"space_id len={len(space_id)}, api_key len={len(api_key)}, project={project}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test 1: gRPC (Arize default)

# COMMAND ----------

from arize.otel import Endpoint, Transport, register
from opentelemetry import trace

tp = register(
    space_id=space_id,
    api_key=api_key,
    project_name=project,
    endpoint=Endpoint.ARIZE,
    transport=Transport.GRPC,
    batch=False,
    verbose=True,
    log_to_console=True,
)

with trace.get_tracer("arize-diagnose-grpc").start_as_current_span("grpc_test_span") as span:
    span.set_attribute("diagnose", "grpc")

tp.force_flush()
tp.shutdown()
print(f"Sent grpc_test_span → check AX project '{project}' (wait ~60s)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test 2: HTTP (if gRPC blocked on cluster)
# MAGIC
# MAGIC Uncomment and run if Test 1 shows connection errors.

# COMMAND ----------

# from arize.otel import Endpoint, Transport, register
# from opentelemetry import trace
#
# tp = register(
#     space_id=space_id,
#     api_key=api_key,
#     project_name=project,
#     endpoint=Endpoint.ARIZE,
#     transport=Transport.HTTP,
#     batch=False,
#     verbose=True,
#     log_to_console=True,
# )
# with trace.get_tracer("arize-diagnose-http").start_as_current_span("http_test_span"):
#     pass
# tp.force_flush()
# tp.shutdown()
# print(f"Sent http_test_span → check AX project '{project}'")
