"""Strict Pydantic JSON Schemas for Paxton Bank Tool Inputs and Agent Outputs.

Enforces explicit input and output validation across all LLM tool declarations
and multi-agent structured responses (`extra="forbid"`).
"""
import re
from typing import Any, Literal, Optional

try:
    from pydantic import BaseModel, ConfigDict, Field
except ImportError:  # Offline stdlib fallback when pydantic wheel is not installed
    def ConfigDict(**kwargs):
        return kwargs

    def Field(default=..., **kwargs):
        return {"_field_meta": True, "default": default, **kwargs}

    class BaseModel:
        model_config: dict[str, Any] = {}

        def __init__(self, **data):
            cls = self.__class__
            annotations = getattr(cls, "__annotations__", {})
            if cls.model_config.get("extra") == "forbid":
                unknown = set(data.keys()) - set(annotations.keys())
                if unknown:
                    raise ValueError(f"Extra inputs are not permitted: {sorted(unknown)}")
            for name, typ in annotations.items():
                raw_spec = getattr(cls, name, None)
                meta = raw_spec if isinstance(raw_spec, dict) and raw_spec.get("_field_meta") else {}
                if name in data:
                    val = data[name]
                elif "default" in meta and meta["default"] is not ...:
                    val = meta["default"]
                elif "default_factory" in meta:
                    val = meta["default_factory"]()
                elif raw_spec is not None and not isinstance(raw_spec, dict):
                    val = raw_spec
                else:
                    raise ValueError(f"Field required: {name}")
                if isinstance(val, str):
                    if "min_length" in meta and len(val) < meta["min_length"]:
                        raise ValueError(f"{name} shorter than min_length={meta['min_length']}")
                    if "max_length" in meta and len(val) > meta["max_length"]:
                        raise ValueError(f"{name} longer than max_length={meta['max_length']}")
                    if "pattern" in meta and not re.match(meta["pattern"], val):
                        raise ValueError(f"{name} does not match pattern {meta['pattern']}")
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    if "ge" in meta and val < meta["ge"]:
                        raise ValueError(f"{name} must be >= {meta['ge']}")
                    if "le" in meta and val > meta["le"]:
                        raise ValueError(f"{name} must be <= {meta['le']}")
                setattr(self, name, val)

        def model_dump(self) -> dict[str, Any]:
            return {k: getattr(self, k) for k in getattr(self.__class__, "__annotations__", {})}

        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            props = {}
            for k in getattr(cls, "__annotations__", {}):
                raw_spec = getattr(cls, k, None)
                desc = raw_spec.get("description", "") if isinstance(raw_spec, dict) else ""
                props[k] = {"title": k, "description": desc}
            return {"title": cls.__name__, "type": "object", "properties": props}


# ============================================================================
# Tool Input Schemas
# ============================================================================

class ProspectEnrichmentInput(BaseModel):
    """Input schema for `enrich_prospect_intelligence` (`enrich_prospect`)."""
    model_config = ConfigDict(extra="forbid")
    company: str = Field(
        ...,
        min_length=2,
        max_length=120,
        description="Legal or commercial name of the prospective client company (e.g., 'Meridian Components Inc.').",
    )


class MarketSnapshotInput(BaseModel):
    """Input schema for `get_fx_market_snapshot` (`get_market_snapshot`)."""
    model_config = ConfigDict(extra="forbid")
    pairs: str = Field(
        ...,
        min_length=3,
        max_length=100,
        pattern=r"^[A-Za-z0-9,_-]+$",
        description="Comma-separated FX currency pair or index series IDs (e.g., 'USDMXN,USDSGD,EURUSD').",
    )


class CollateralSearchInput(BaseModel):
    """Input schema for `search_collateral_materials` (`find_materials`)."""
    model_config = ConfigDict(extra="forbid")
    area: str = Field(
        ...,
        min_length=2,
        max_length=120,
        description="Treasury or commercial banking product topic (e.g., 'liquidity sweeps', 'cross-border FX', 'ERP integration').",
    )
    kind: str = Field(
        default="",
        max_length=60,
        description="Optional asset format filter (e.g., 'case study', 'product sheet', 'whitepaper').",
    )


class KycScreenInput(BaseModel):
    """Input schema for `screen_counterparty_kyc_watchlist` (`screen_entity`)."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(
        ...,
        min_length=2,
        max_length=120,
        description="Company or counterparty legal name to screen against OFAC/AML watchlists.",
    )


class SpecialistLookupInput(BaseModel):
    """Input schema for `lookup_treasury_specialist` (`lookup_specialist`) and `get_specialist_calendar_slots` (`get_slots`)."""
    model_config = ConfigDict(extra="forbid")
    soeid: str = Field(
        ...,
        min_length=3,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Paxton Bank specialist directory identifier (SOEID, e.g., 'TS-104' or 'MW20491').",
    )


class MaterialsApprovalInput(BaseModel):
    """Input schema for `request_banker_materials_approval` (`request_materials`)."""
    model_config = ConfigDict(extra="forbid")
    area: str = Field(
        ...,
        min_length=2,
        max_length=120,
        description="Product or solution area for the collateral package requiring human banker approval.",
    )
    note: str = Field(
        default="",
        max_length=300,
        description="Contextual note for the human relationship manager explaining why these materials were selected.",
    )


class WarmHandoffInput(BaseModel):
    """Input schema for `initiate_specialist_warm_handoff` (`initiate_handoff`)."""
    model_config = ConfigDict(extra="forbid")
    specialist_soeid: str = Field(
        ...,
        min_length=3,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Paxton Bank Treasury Specialist SOEID confirmed by the human banker.",
    )
    availability_note: str = Field(
        default="",
        max_length=300,
        description="Prospect's stated preferred meeting times or timezone constraints.",
    )


# ============================================================================
# Agent Structured Output Schemas
# ============================================================================

class DimensionScore(BaseModel):
    """Single qualification dimension score (0-100) with verbatim rationale."""
    model_config = ConfigDict(extra="forbid")
    score: int = Field(..., ge=0, le=100, description="Numeric score from 0 to 100.")
    rationale: str = Field(..., min_length=3, description="One-line rationale quoting what the client said.")


class DisqualifierCheck(BaseModel):
    """Compliance or commercial disqualifier rule evaluation."""
    model_config = ConfigDict(extra="forbid")
    rule: str = Field(..., description="Name of the disqualifier rule checked.")
    triggered: bool = Field(..., description="True if the disqualifier was triggered.")


class QualificationScoreOutput(BaseModel):
    """Output schema for the `Scorer` (J2 five-dimension qualification) agent."""
    model_config = ConfigDict(extra="forbid")
    strategic_fit: DimensionScore
    revenue_potential: DimensionScore
    buying_intent: DimensionScore
    implementation_complexity: DimensionScore
    overall: DimensionScore
    disqualifiers_checked: list[DisqualifierCheck]
    next_best_action: str = Field(..., description="Single-sentence recommended next action for the banker.")


class InsightCardItem(BaseModel):
    """Single real-time banker insight card surfaced by the `Panel` (J9) agent."""
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., max_length=80, description="Paxton Bank capability or client need in plain words.")
    detail: str = Field(..., description="One plain-English sentence summarizing the capability.")
    source_quote: str = Field(..., description="Verbatim quote from the client turn.")
    category: Literal["need", "pain_point", "product_interest", "signal", "risk"]
    recommended_action: str = Field(..., description="Concrete next step for the human banker.")
    commercial_impact: str = Field(..., description="Concrete business outcome for the client.")


class InsightPanelOutput(BaseModel):
    """Output wrapper for `Panel` insight cards."""
    model_config = ConfigDict(extra="forbid")
    cards: list[InsightCardItem] = Field(default_factory=list)


class SentimentPulseOutput(BaseModel):
    """Output schema for the `Pulse` (J6 client sentiment) agent."""
    model_config = ConfigDict(extra="forbid")
    score: int = Field(..., ge=0, le=100, description="Client sentiment score (0=hostile, 50=neutral, 100=delighted).")
    driver: str = Field(..., description="3-6 words on the vocal tone/content reason.")
    topic: str = Field(..., description="2-4 words identifying the subject reacted to.")


class ComplianceVerdictOutput(BaseModel):
    """Output schema for the `Sentinel` (J4 compliance guardrail) classifier."""
    model_config = ConfigDict(extra="forbid")
    trip: bool = Field(..., description="True if the utterance asks for a prohibited guarantee or advice.")
    risk_type: Literal[
        "commitment", "credit", "legal", "sanctions",
        "kyc", "account", "binding", "regulatory", "none"
    ]
    reason: str = Field(..., description="Explanation of why the compliance guardrail did or did not trip.")


def validate_tool_input(schema_cls: type[BaseModel], payload: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Validate tool input dictionary against a strict Pydantic schema class.

    Returns:
        Tuple of `(validated_dict, error_dict)`. When valid, `error_dict` is `None`.
        When invalid, `error_dict` includes `status='validation_error'` and `recovery_guidance`.
    """
    try:
        instance = schema_cls(**payload)
        return instance.model_dump(), None
    except Exception as exc:
        return None, {
            "status": "validation_error",
            "error": str(exc),
            "recovery_guidance": (
                f"Invalid input for {schema_cls.__name__}: {exc}. "
                "Check required field names, length limits, and allowed characters."
            ),
        }
