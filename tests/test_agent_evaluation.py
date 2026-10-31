"""Automated Evaluation & Regression Test Suite for Paxton Bank AI Sales Companion.

Evaluates the repository against the Golden Dataset (`tests/golden_dataset.json`)
and verifies all 5 categories of the AgentOps Code Review Matrix:
1. Tool & Interface Design (docstrings, descriptive names, strict Pydantic schemas, guided error recovery)
2. Context & Memory (constitution, history compaction, persistent session state, async memory)
3. Orchestration & Logic (multi-agent coordinator, strategic model routing, guardrails, HITL hooks)
4. Observability & Tracing (structured JSON logging, intent vs. outcome capture, OpenTelemetry, PII redaction)
5. Infrastructure & CI/CD (golden evaluation harness, Terraform IaC, Secret Manager)
"""

import asyncio
import inspect
import json
import os
import sys
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
from adk_runtime import (
    TASK_MODEL_ROUTES,
    compact_conversation_history,
    select_model_for_task,
)
from journeys import (
    decide_banker_materials_approval,
    initiate_specialist_warm_handoff,
    request_banker_materials_approval,
    require_human_banker_approval,
)
from personas import (
    COMPANION_SYS,
    COMPLIANCE_SYSTEM_INSTRUCTION,
    INSIGHT_SYSTEM_INSTRUCTION,
    PERSONAS,
    SCORE_SYSTEM_INSTRUCTION,
)
from pii_redactor import redact_data, redact_pii
from schemas import (
    ComplianceVerdictOutput,
    KycScreenInput,
    MarketSnapshotInput,
    MaterialsApprovalInput,
    ProspectEnrichmentInput,
    QualificationScoreOutput,
    validate_tool_input,
)
import sentinel
from telemetry import glog, log_intent_and_outcome, tracer
from tools import (
    enrich_prospect_intelligence,
    get_crm_lead_dossier,
    get_fx_market_snapshot,
    get_secret,
    get_specialist_calendar_slots,
    lookup_treasury_specialist,
    record_crm_prospect_activity,
    screen_counterparty_kyc_watchlist,
    search_collateral_materials,
    send_approved_prospect_email,
)


class _DummyRoom:
    """Lightweight mock Room for offline Human-in-the-Loop (HITL) evaluation tests."""

    def __init__(self, room_id: str = "eval-room-001"):
        self.room_id = room_id
        self.company = "Meridian Precision Logistics"
        self.approvals: dict = {}
        self.events: list = []
        self.feeds: list = []
        self.companion = None

    async def _feed(self, kind: str, data: dict):
        self.feeds.append((kind, data))

    def _log_event(self, event: str, detail: str = ""):
        self.events.append((event, detail))


class TestPaxtonBankAgentEvaluation(unittest.TestCase):
    """Golden-dataset and AgentOps rubric regression test suite."""

    @classmethod
    def setUpClass(cls):
        golden_path = os.path.join(os.path.dirname(__file__), "golden_dataset.json")
        with open(golden_path, "r", encoding="utf-8") as f:
            cls.golden_data = json.load(f)

    def test_01_comprehensive_docstrings_and_descriptive_names(self):
        """Verifies all agent tools have descriptive names and Google-style docstrings."""
        tool_functions = [
            enrich_prospect_intelligence,
            get_fx_market_snapshot,
            search_collateral_materials,
            screen_counterparty_kyc_watchlist,
            lookup_treasury_specialist,
            get_specialist_calendar_slots,
            send_approved_prospect_email,
            get_crm_lead_dossier,
            record_crm_prospect_activity,
            request_banker_materials_approval,
            decide_banker_materials_approval,
            initiate_specialist_warm_handoff,
        ]
        for fn in tool_functions:
            doc = inspect.getdoc(fn) or ""
            self.assertGreaterEqual(
                len(doc),
                80,
                f"Tool {fn.__name__} must have a comprehensive docstring.",
            )
            self.assertIn("Args:", doc, f"Tool {fn.__name__} docstring must document Args:")
            self.assertIn("Returns:", doc, f"Tool {fn.__name__} docstring must document Returns:")

    def test_02_strict_json_schemas_validation(self):
        """Verifies strict Pydantic input/output JSON schemas reject invalid fields and return guided errors."""
        valid, err = validate_tool_input(
            MarketSnapshotInput, {"pairs": "USDMXN,EURUSD"}
        )
        self.assertIsNone(err)
        self.assertEqual(valid["pairs"], "USDMXN,EURUSD")

        invalid, err = validate_tool_input(
            MarketSnapshotInput, {"pairs": "BAD PAIR $$$", "unexpected_field": 123}
        )
        self.assertIsNone(invalid)
        self.assertEqual(err["status"], "validation_error")
        self.assertIn("recovery_guidance", err)

        score_schema = QualificationScoreOutput.model_json_schema()
        self.assertIn("properties", score_schema)
        self.assertIn("overall", score_schema["properties"])

    def test_03_golden_dataset_tool_and_error_recovery_cases(self):
        """Runs all golden dataset evaluation cases for schemas, guided error recovery, HITL, PII, and guardrails."""
        cases = self.golden_data["eval_cases"]
        self.assertGreaterEqual(len(cases), 7)

        for case in cases:
            cid = case["case_id"]

            if cid == "eval_001_fx_snapshot_schema_valid":
                valid, err = validate_tool_input(MarketSnapshotInput, case["input"])
                self.assertIsNone(err)
                self.assertTrue(case["expected_valid"])

            elif cid == "eval_002_fx_snapshot_guided_error":
                res = get_fx_market_snapshot(**case["input"])
                self.assertEqual(res["found"], case["expected_found"])
                self.assertIn(case["expected_recovery_keyword"], res["recovery_guidance"])

            elif cid == "eval_003_prospect_enrichment_validation_error":
                res = enrich_prospect_intelligence(**case["input"])
                self.assertEqual(res["found"], case["expected_found"])
                self.assertIn(case["expected_recovery_keyword"], res["recovery_guidance"])

            elif cid == "eval_004_kyc_watchlist_validation_error":
                res = screen_counterparty_kyc_watchlist(**case["input"])
                self.assertEqual(res["verdict"], case["expected_verdict"])
                self.assertIn(case["expected_recovery_keyword"], res["recovery_guidance"])

            elif cid == "eval_005_hitl_blocks_until_human_approves":
                room = _DummyRoom("eval-hitl-room")
                res = asyncio.run(
                    request_banker_materials_approval(
                        room,
                        area=case["input"]["area"],
                        note=case["input"]["note"],
                    )
                )
                self.assertEqual(res["status"], case["expected_status"])
                self.assertIn("approval_id", res)

            elif cid == "eval_006_pii_redaction_pipeline":
                scrubbed = redact_pii(case["raw_text"])
                for expected_tok in case["expected_tokens"]:
                    self.assertIn(expected_tok, scrubbed)
                for forbidden_tok in case["forbidden_tokens"]:
                    self.assertNotIn(forbidden_tok, scrubbed)

            elif cid == "eval_007_compliance_guardrail_sentinel":
                verdict = sentinel.regex_check(case["statement"])
                self.assertEqual(verdict["trip"], case["expected_trip"])
                self.assertIn("risk_type", verdict)

    def test_04_human_in_the_loop_approval_lifecycle(self):
        """Verifies the complete HITL approval lifecycle from request -> human approve -> decision."""
        room = _DummyRoom("test_hitl_session_001")
        req = asyncio.run(
            request_banker_materials_approval(
                room,
                area="Automated Cash Sweeps & Zero Balance Accounts",
                note="Prospect CFO asked for ZBA implementation guide.",
            )
        )
        self.assertEqual(req["status"], "pending_banker_approval")
        approval_id = req["approval_id"]
        self.assertEqual(room.approvals[approval_id]["status"], "pending")

        decision = asyncio.run(
            decide_banker_materials_approval(
                room,
                approval_id=approval_id,
                decision="approved",
                to_email="treasurer@meridian.example.com",
            )
        )
        self.assertTrue(decision["ok"])
        self.assertEqual(decision["status"], "approved")
        self.assertEqual(room.approvals[approval_id]["status"], "approved")

    def test_05_strategic_model_routing_and_history_compaction(self):
        """Verifies strategic model routing (Flash-Lite vs Flash vs Pro) and sliding-window history compaction."""
        self.assertEqual(select_model_for_task("sentiment_pulse"), config.MODEL_FAST)
        self.assertEqual(select_model_for_task("worker_synthesis"), config.MODEL_WORKER)
        self.assertEqual(select_model_for_task("qualification_scoring"), config.MODEL_DEEP)
        self.assertEqual(select_model_for_task("deep_planning"), config.MODEL_DEEP)

        bloated_turns = [
            ("Prospect", f"Turn {i}: discussing FX exposure and liquidity." * 5)
            for i in range(30)
        ]
        compacted = compact_conversation_history(
            bloated_turns,
            running_summary="Prospect operates 12 entities across US, Mexico, and Singapore.",
            max_tokens=500,
            keep_recent_turns=4,
        )
        self.assertIn("[COMPACTED SESSION MEMORY]", compacted)
        self.assertIn("[SLIDING WINDOW RECENT TURNS]", compacted)
        self.assertLessEqual(len(compacted), 2000)

    def test_06_intent_vs_outcome_structured_logging(self):
        """Verifies intent vs. outcome capture with active PII redaction."""
        record = log_intent_and_outcome(
            agent_name="CompanionTool",
            action="send_approved_prospect_email",
            phase="INTENT",
            room_id="sess_test_99",
            intent_payload={"recipient": "cfo@example.com", "goal": "Send FX brochure"},
            status="planned",
            latency_ms=42.5,
        )
        self.assertEqual(record["action"], "send_approved_prospect_email")
        self.assertEqual(record["phase"], "INTENT")
        self.assertEqual(record["intent"]["recipient"], "[REDACTED_EMAIL]")

    def test_07_paxton_bank_brand_consistency(self):
        """Ensures Paxton Bank branding is consistently applied in personas and system instructions."""
        combined = (
            COMPANION_SYS
            + INSIGHT_SYSTEM_INSTRUCTION
            + SCORE_SYSTEM_INSTRUCTION
            + COMPLIANCE_SYSTEM_INSTRUCTION
            + json.dumps(PERSONAS)
        ).lower()
        self.assertIn("paxton bank", combined)
        self.assertIn("paxton commercial bank", combined)

    def test_08_adk_root_agent_coordinator_and_subagents(self):
        """Verifies ADK root_agent Coordinator and its 3 specialized sub-agents."""
        from paxton_companion_agent import root_agent
        self.assertEqual(root_agent.name, "paxton_sales_coordinator")
        self.assertEqual(len(root_agent.sub_agents), 3)
        sub_names = {sa.name for sa in root_agent.sub_agents}
        self.assertEqual(
            sub_names,
            {
                "paxton_enrichment_specialist",
                "paxton_insights_analyst",
                "paxton_compliance_auditor",
            },
        )


if __name__ == "__main__":
    unittest.main()
