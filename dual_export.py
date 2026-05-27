"""Dual OTLP export: Arize AX + Databricks Unity Catalog (spans only)."""

from __future__ import annotations

import os
from typing import Optional

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from arize.otel import BatchSpanProcessor as ArizeBatchSpanProcessor
from arize.otel import HTTPSpanExporter as ArizeHTTPSpanExporter
from openinference.semconv.resource import ResourceAttributes


def _require(name: str, value: Optional[str]) -> str:
    if not value or not str(value).strip():
        raise ValueError(f"Missing required configuration: {name}")
    return str(value).strip()


def configure_dual_export(
    *,
    project_name: str,
    arize_space_id: Optional[str] = None,
    arize_api_key: Optional[str] = None,
    databricks_host: Optional[str] = None,
    databricks_token: Optional[str] = None,
    uc_spans_table: Optional[str] = None,
    service_name: str = "external-agent-dual-ingest",
    set_global_tracer_provider: bool = True,
) -> TracerProvider:
    """
    Configure one TracerProvider that exports spans to Arize and Databricks UC.

    Parameters
    ----------
    project_name:
        Arize project name (``openinference.project.name`` resource attribute).
    arize_space_id, arize_api_key:
        Defaults to ``ARIZE_SPACE_ID`` / ``ARIZE_API_KEY`` env vars.
    databricks_host:
        Workspace URL, e.g. ``https://dbc-xxx.cloud.databricks.com``.
        Defaults to ``DATABRICKS_HOST``.
    databricks_token:
        PAT with MODIFY on UC OTel span tables. Defaults to ``DATABRICKS_TOKEN``.
    uc_spans_table:
        Fully qualified spans table for ``X-Databricks-UC-Table-Name``, e.g.
        ``catalog.schema.my_prefix_otel_spans``.
    """
    project_name = _require("project_name", project_name)
    space_id = _require(
        "arize_space_id",
        arize_space_id or os.environ.get("ARIZE_SPACE_ID"),
    )
    api_key = _require(
        "arize_api_key",
        arize_api_key or os.environ.get("ARIZE_API_KEY"),
    )
    host = _require(
        "databricks_host",
        databricks_host or os.environ.get("DATABRICKS_HOST"),
    ).rstrip("/")
    token = _require(
        "databricks_token",
        databricks_token or os.environ.get("DATABRICKS_TOKEN"),
    )
    spans_table = _require("uc_spans_table", uc_spans_table)

    resource = Resource.create(
        {
            ResourceAttributes.PROJECT_NAME: project_name,
            "service.name": service_name,
        }
    )
    provider = TracerProvider(resource=resource)

    arize_exporter = ArizeHTTPSpanExporter(space_id=space_id, api_key=api_key)
    provider.add_span_processor(
        ArizeBatchSpanProcessor(span_exporter=arize_exporter)
    )

    dbx_exporter = OTLPSpanExporter(
        endpoint=f"{host}/api/2.0/otel/v1/traces",
        headers={
            "content-type": "application/x-protobuf",
            "X-Databricks-UC-Table-Name": spans_table,
            "Authorization": f"Bearer {token}",
        },
    )
    provider.add_span_processor(BatchSpanProcessor(dbx_exporter))

    if set_global_tracer_provider:
        trace.set_tracer_provider(provider)

    return provider


def shutdown_tracer(provider: TracerProvider) -> None:
    """Flush and shut down span processors (call before notebook exit)."""
    provider.force_flush()
    provider.shutdown()
