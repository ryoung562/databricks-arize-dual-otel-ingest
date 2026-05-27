# Databricks notebook source
# MAGIC %md
# MAGIC # Path 2: Dual OTel ingest (Arize AX + Unity Catalog)
# MAGIC
# MAGIC This notebook demonstrates **split-stream tracing** for agents running outside Databricks-native auto-persist:
# MAGIC
# MAGIC 1. **Arize AX** — sub-second OTLP ingest for trace exploration, evaluation, and monitoring.
# MAGIC 2. **Databricks governed storage** — the same spans exported via OTLP to Unity Catalog Delta tables for retention, governance, and SQL analytics.
# MAGIC
# MAGIC **Demo workload:** OpenAI + LangChain tool-calling agent with OpenInference auto-instrumentation.
# MAGIC
# MAGIC **Prerequisites:** Unity Catalog workspace, [OpenTelemetry on Databricks](https://docs.databricks.com/aws/en/mlflow3/genai/tracing/trace-unity-catalog) preview enabled, `mlflow>=3.11`, Arize space API key, OpenAI API key.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Install dependencies

# COMMAND ----------

# MAGIC %pip install -r ../requirements.txt --quiet
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Configuration
# MAGIC
# MAGIC Run this section **after** the install cell restarts Python.
# MAGIC
# MAGIC Set widgets below or map secrets to environment variables in your cluster policy.
# MAGIC
# MAGIC | Variable | Description |
# MAGIC |----------|-------------|
# MAGIC | `catalog_name` / `schema_name` / `table_prefix` | UC trace location |
# MAGIC | `experiment_name` | MLflow experiment (optional UI for traces) |
# MAGIC | `sql_warehouse_id` | Warehouse for trace search / SQL |
# MAGIC | `arize_project_name` | Arize project for spans |
# MAGIC | Secrets: `ARIZE_SPACE_ID`, `ARIZE_API_KEY`, `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `OPENAI_API_KEY` | |

# COMMAND ----------

import os

dbutils.widgets.text("catalog_name", "main", "UC catalog")
dbutils.widgets.text("schema_name", "otel_traces", "UC schema")
dbutils.widgets.text("table_prefix", "partner_demo", "UC table prefix")
dbutils.widgets.text(
    "experiment_name",
    "/Shared/partner-dual-ingest-demo",
    "MLflow experiment",
)
dbutils.widgets.text("sql_warehouse_id", "", "SQL warehouse ID")
dbutils.widgets.text("arize_project_name", "databricks-dual-ingest-demo", "Arize project")
dbutils.widgets.text("openai_model", "gpt-4o-mini", "OpenAI model")
dbutils.widgets.text("demo_user_message", "What does our travel policy say about demo expenses?", "Demo prompt")

# Optional: load from a secret scope (uncomment and set scope name)
# scope = "arize-databricks-partner"
# os.environ["ARIZE_SPACE_ID"] = dbutils.secrets.get(scope=scope, key="arize-space-id")
# os.environ["ARIZE_API_KEY"] = dbutils.secrets.get(scope=scope, key="arize-api-key")
# os.environ["DATABRICKS_HOST"] = dbutils.secrets.get(scope=scope, key="databricks-host")
# os.environ["DATABRICKS_TOKEN"] = dbutils.secrets.get(scope=scope, key="databricks-token")
# os.environ["OPENAI_API_KEY"] = dbutils.secrets.get(scope=scope, key="openai-api-key")

catalog_name = dbutils.widgets.get("catalog_name").strip()
schema_name = dbutils.widgets.get("schema_name").strip()
table_prefix = dbutils.widgets.get("table_prefix").strip()
experiment_name = dbutils.widgets.get("experiment_name").strip()
sql_warehouse_id = dbutils.widgets.get("sql_warehouse_id").strip()
arize_project_name = dbutils.widgets.get("arize_project_name").strip()
openai_model = dbutils.widgets.get("openai_model").strip()
demo_user_message = dbutils.widgets.get("demo_user_message").strip()

if not sql_warehouse_id:
    raise ValueError("Set the sql_warehouse_id widget to a warehouse you can use.")

os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = sql_warehouse_id

# Default workspace URL when running on Databricks (override with DATABRICKS_HOST if needed)
if not os.environ.get("DATABRICKS_HOST"):
    try:
        os.environ["DATABRICKS_HOST"] = (
            dbutils.notebook.entry_point.getDbutils()
            .notebook()
            .getContext()
            .apiUrl()
            .get()
            .rstrip("/")
        )
    except Exception:
        pass

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Bind MLflow experiment to Unity Catalog (public preview)
# MAGIC
# MAGIC Creates `{prefix}_otel_spans` (and related tables) and links the experiment for optional trace UI browsing.

# COMMAND ----------

import mlflow
from mlflow.entities.trace_location import UnityCatalog

mlflow.set_tracking_uri("databricks")

experiment = mlflow.set_experiment(
    experiment_name=experiment_name,
    trace_location=UnityCatalog(
        catalog_name=catalog_name,
        schema_name=schema_name,
        table_prefix=table_prefix,
    ),
)

uc_spans_table = experiment.trace_location.full_otel_spans_table_name
print(f"Experiment ID: {experiment.experiment_id}")
print(f"UC spans table: {uc_spans_table}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Configure dual OTLP export
# MAGIC
# MAGIC One `TracerProvider`, two span processors — Arize and Databricks. See `dual_export.py` in the repo root.

# COMMAND ----------

import sys
from pathlib import Path

# Repo root is on PYTHONPATH when this notebook lives in a Databricks Repo.
_repo_root = Path.cwd()
if _repo_root.name == "notebooks":
    _repo_root = _repo_root.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dual_export import configure_dual_export, shutdown_tracer

tracer_provider = configure_dual_export(
    project_name=arize_project_name,
    uc_spans_table=uc_spans_table,
    service_name="langchain-external-agent-demo",
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Instrument LangChain and run the demo agent

# COMMAND ----------

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from openinference.instrumentation.langchain import LangChainInstrumentor

LangChainInstrumentor().instrument(tracer_provider=tracer_provider)


@tool
def lookup_policy(topic: str) -> str:
    """Return a short internal policy snippet for the given topic."""
    policies = {
        "travel": "Demo travel expenses under $500 are pre-approved for partner workshops.",
        "security": "All agent outputs must stay within approved data boundaries.",
    }
    return policies.get(topic.lower(), f"No policy on file for '{topic}'.")


llm = ChatOpenAI(model=openai_model, temperature=0)
prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an enterprise assistant. Use tools when you need policy details. Be concise.",
        ),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ]
)
tools = [lookup_policy]
agent = create_tool_calling_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

print("Running demo agent...")
result = executor.invoke({"input": demo_user_message})
print("\n--- Agent response ---")
print(result["output"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Flush spans
# MAGIC
# MAGIC Ensures OTLP batches are delivered before verification (required for short notebook runs).

# COMMAND ----------

shutdown_tracer(tracer_provider)
print("Tracer shut down and spans flushed.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Verify Unity Catalog ingest (Databricks SQL)
# MAGIC
# MAGIC Spans should appear in `{catalog}.{schema}.{prefix}_otel_spans` within a short delay after export.

# COMMAND ----------

import time

time.sleep(15)  # allow OTLP batch + Delta write; increase if your workspace is slow

display(
    spark.sql(
        f"""
        SELECT
          trace_id,
          span_id,
          name,
          kind,
          start_time_unix_nano,
          end_time_unix_nano,
          attributes
        FROM {uc_spans_table}
        ORDER BY start_time_unix_nano DESC
        LIMIT 20
        """
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Span volume by name

# COMMAND ----------

display(
    spark.sql(
        f"""
        SELECT
          name,
          COUNT(*) AS span_count
        FROM {uc_spans_table}
        WHERE start_time_unix_nano >= (
          SELECT MAX(start_time_unix_nano) - 300000000000 FROM {uc_spans_table}
        )
        GROUP BY name
        ORDER BY span_count DESC
        """
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Verify Arize AX
# MAGIC
# MAGIC Open your Arize project (widget: `arize_project_name`) in the AX UI. You should see the same agent run with LLM and tool spans.
# MAGIC
# MAGIC If traces are missing:
# MAGIC - Confirm `ARIZE_SPACE_ID` and `ARIZE_API_KEY`
# MAGIC - Confirm `arize_project_name` is set (required resource attribute)
# MAGIC - Re-run after checking cluster env vars

# COMMAND ----------

print(
    f"Check Arize AX → project '{arize_project_name}' for traces from service "
    "'langchain-external-agent-demo'."
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Optional: MLflow Experiment UI
# MAGIC
# MAGIC In the workspace **Experiments** page, open `{experiment_name}` → **Traces** tab (select your SQL warehouse). This is optional; SQL on the UC spans table is the primary governed-store proof.

# COMMAND ----------

print(f"MLflow experiment: {experiment_name}")
print(f"Experiment ID: {experiment.experiment_id}")
