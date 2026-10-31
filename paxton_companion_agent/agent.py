"""Paxton Bank AI Sales Companion — ADK 2.x Multi-Agent Coordinator Definition.

Exposes `root_agent` for the Google Agent Development Kit (`adk web`, `adk run`,
`adk eval`, and `adk deploy cloud_run`) and integrates:
- Multi-Agent Coordinator + Sequential/Parallel sub-agents (`enrichment_sub_agent`,
  `insights_sub_agent`, `compliance_sub_agent`)
- Strategic model routing (`config.MODEL_FAST` / `config.MODEL_WORKER` for low-latency
  live assistance, `config.MODEL_DEEP` for deep compliance and qualification scoring)
- Explicit JSON schemas and guided error recovery tools
- Human-in-the-Loop (HITL) approval hooks before external distribution
- Context window compression (`ContextWindowCompressionConfig` / `SlidingWindow`)
- Active PII redaction and compliance guardrails (`build_adk_plugins`)
"""

import os
import sys

# Ensure parent root directory is importable when invoked via `adk run paxton_companion_agent`
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

try:
    from google.adk.agents import Agent
    from google.adk.tools.background_live_function_tool import BackgroundLiveFunctionTool
    from google.genai import types
except ImportError:
    class BackgroundLiveFunctionTool:  # type: ignore
        def __init__(self, fn):
            self.fn = fn

    class Agent:  # type: ignore
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    types = None  # type: ignore

import config
from adk_runtime import (
    build_adk_plugins,
    compact_conversation_history,
    select_model_for_task,
)
from journeys import (
    decide_banker_materials_approval,
    initiate_specialist_warm_handoff,
    request_banker_materials_approval,
)
from personas import (
    COMPLIANCE_SYSTEM_INSTRUCTION,
    INSIGHT_SYSTEM_INSTRUCTION,
    SCORE_SYSTEM_INSTRUCTION,
)
from tools import (
    enrich_prospect_intelligence,
    get_crm_lead_dossier,
    get_fx_market_snapshot,
    get_specialist_calendar_slots,
    lookup_treasury_specialist,
    record_crm_prospect_activity,
    screen_counterparty_kyc_watchlist,
    search_collateral_materials,
    send_approved_prospect_email,
)

COORDINATOR_CONSTITUTION = """You are the Paxton Bank AI Sales Companion Coordinator (`paxton_sales_coordinator`).

CONSTITUTION & OPERATING PRINCIPLES:
1. PERSONA & DOMAIN: You are an institutional commercial banking AI copilot for Paxton Bank Relationship Managers (RMs) covering middle-market and corporate CFOs/Treasurers ($20M-$2B revenue).
2. FACTUAL GROUNDING: Never fabricate FX rates, spreads, pricing tiers, or counterparty KYC statuses. Always invoke the dedicated tools (`get_fx_market_snapshot`, `enrich_prospect_intelligence`, `screen_counterparty_kyc_watchlist`, `search_collateral_materials`).
3. HUMAN-IN-THE-LOOP (HITL) MANDATE: You must NEVER send collateral materials or external emails directly to a prospect without explicit human banker confirmation. Always call `request_banker_materials_approval` first, wait for `decide_banker_materials_approval`, and only invoke `send_approved_prospect_email` after banker confirmation.
4. REGULATORY COMPLIANCE: Enforce FINRA Rule 2210, OCC Bulletin 2023-17, and SEC Marketing Rule guardrails. Never guarantee returns, promise credit approval, or disparage competitors.
5. PRIVACY & PII REDACTION: Redact SSNs, account numbers, payment cards, IBANs, and personal contact data before persisting session notes or CRM activity logs.
"""

SAFETY_SETTINGS = (
    [
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
        ),
    ]
    if types is not None
    else []
)

enrichment_sub_agent = Agent(
    name="paxton_enrichment_specialist",
    model=select_model_for_task("worker_synthesis"),
    description=(
        "Asynchronous background specialist for company enrichment, FX market snapshots, "
        "KYC/OFAC counterparty screening, and CRM dossier lookups."
    ),
    instruction=(
        "Gather prospect firmographics, live FX rates, KYC screening status, and approved "
        "collateral documents using your tools. Return concise, citation-backed facts."
    ),
    tools=[
        BackgroundLiveFunctionTool(enrich_prospect_intelligence),
        BackgroundLiveFunctionTool(get_fx_market_snapshot),
        BackgroundLiveFunctionTool(screen_counterparty_kyc_watchlist),
        BackgroundLiveFunctionTool(search_collateral_materials),
        BackgroundLiveFunctionTool(get_crm_lead_dossier),
    ],
)

insights_sub_agent = Agent(
    name="paxton_insights_analyst",
    model=select_model_for_task("qualification_scoring"),
    description=(
        "Strategic BANT qualification scorer and live call insight generator using "
        "Pro-tier reasoning for multi-factor commercial banking deal qualification."
    ),
    instruction=f"{INSIGHT_SYSTEM_INSTRUCTION}\n\n{SCORE_SYSTEM_INSTRUCTION}",
)

compliance_sub_agent = Agent(
    name="paxton_compliance_auditor",
    model=select_model_for_task("compliance_guardrail"),
    description=(
        "Real-time regulatory compliance auditor enforcing FINRA 2210, OCC, and SEC "
        "guardrails on banker statements and outbound client communications."
    ),
    instruction=COMPLIANCE_SYSTEM_INSTRUCTION,
)

root_agent = Agent(
    name="paxton_sales_coordinator",
    model=config.MODEL_WORKER,
    description=(
        "Paxton Bank AI Sales Companion Root Coordinator — orchestrates real-time "
        "whisper coaching, background enrichment, compliance auditing, and HITL-gated "
        "collateral distribution and specialist warm handoffs."
    ),
    instruction=COORDINATOR_CONSTITUTION,
    tools=[
        BackgroundLiveFunctionTool(enrich_prospect_intelligence),
        BackgroundLiveFunctionTool(get_fx_market_snapshot),
        BackgroundLiveFunctionTool(search_collateral_materials),
        BackgroundLiveFunctionTool(screen_counterparty_kyc_watchlist),
        BackgroundLiveFunctionTool(lookup_treasury_specialist),
        BackgroundLiveFunctionTool(get_specialist_calendar_slots),
        BackgroundLiveFunctionTool(request_banker_materials_approval),
        BackgroundLiveFunctionTool(decide_banker_materials_approval),
        BackgroundLiveFunctionTool(send_approved_prospect_email),
        BackgroundLiveFunctionTool(initiate_specialist_warm_handoff),
        BackgroundLiveFunctionTool(get_crm_lead_dossier),
        BackgroundLiveFunctionTool(record_crm_prospect_activity),
    ],
    sub_agents=[
        enrichment_sub_agent,
        insights_sub_agent,
        compliance_sub_agent,
    ],
    generate_content_config=(
        types.GenerateContentConfig(
            temperature=0.3,
            safety_settings=SAFETY_SETTINGS,
        )
        if types is not None
        else None
    ),
)
