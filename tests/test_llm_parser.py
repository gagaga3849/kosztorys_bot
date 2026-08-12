"""Regression tests for `llm_parser.py`. NEVER makes a live LLM call - every test injects a
fake `completion_fn`, per the production plan ("mock the LiteLLM completion() call").
"""

from __future__ import annotations

import json

import pytest

from llm_parser import (
    DESIGN_SERVICE_QUESTION,
    LLMParsingError,
    assign_precision_level,
    build_heritage_handoff_data,
    detect_heritage_keywords,
    extract_fields_via_llm,
    infer_default_phase,
    merge_design_service_answer,
    needs_design_service_clarification,
    parse_renovation_request,
    sanitize_raw_text,
)
from schema import DesignServiceRequest, PrecisionLevelEnum, WorkItem, WorkPhase


def _fake_completion(response_json: dict) -> callable:
    def _fn(system_prompt: str, user_prompt: str) -> str:
        return json.dumps(response_json)

    return _fn


def _fake_completion_sequence(responses: list) -> callable:
    """Returns each response in order (list of strings or dicts) across successive calls -
    used to exercise the retry-once-on-invalid-JSON path."""
    calls = {"n": 0}

    def _fn(system_prompt: str, user_prompt: str) -> str:
        response = responses[calls["n"]]
        calls["n"] += 1
        return response if isinstance(response, str) else json.dumps(response)

    return _fn


# --------------------------------------------------------------------------------------
# sanitize_raw_text
# --------------------------------------------------------------------------------------


def test_sanitize_raw_text_strips_whitespace() -> None:
    assert sanitize_raw_text("  hello world  ") == "hello world"


def test_sanitize_raw_text_caps_length() -> None:
    long_text = "a" * 5000
    result = sanitize_raw_text(long_text)
    assert len(result) == 4000


# --------------------------------------------------------------------------------------
# Heritage keyword detection (master prompt v2 section 3)
# --------------------------------------------------------------------------------------


def test_detect_heritage_keywords_matches_russian() -> None:
    matched = detect_heritage_keywords("ремонт в историческом здании, охраняется как памятник архитектуры")
    assert "памятник архитектуры" in matched


def test_detect_heritage_keywords_matches_polish() -> None:
    matched = detect_heritage_keywords("To jest budynek zabytkowy pod ochroną konserwatora zabytków")
    assert any("zabytk" in kw for kw in matched)


def test_detect_heritage_keywords_no_match_for_ordinary_request() -> None:
    assert detect_heritage_keywords("ремонт ванной 5 кв.м") == []


def test_build_heritage_handoff_data_produces_expert_required_with_no_pricing() -> None:
    data = build_heritage_handoff_data("текст", ["памятник"], rooms=["living_room"])

    assert data.precision_level == PrecisionLevelEnum.EXPERT_REQUIRED
    assert data.is_heritage_site is True
    assert data.heritage_keywords_matched == ["памятник"]
    assert data.work_items == []


def test_parse_renovation_request_short_circuits_on_heritage_keywords_without_calling_llm() -> None:
    def _fail_if_called(system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("LLM should never be called for a heritage-flagged request")

    data = parse_renovation_request(
        "ремонт в историческом здании, охраняется как памятник архитектуры",
        completion_fn=_fail_if_called,
    )

    assert data.precision_level == PrecisionLevelEnum.EXPERT_REQUIRED
    assert data.is_heritage_site is True


# --------------------------------------------------------------------------------------
# infer_default_phase
# --------------------------------------------------------------------------------------


def test_infer_default_phase_matches_known_keywords() -> None:
    assert infer_default_phase("demolition_tiling") == WorkPhase.DEMOLITION
    assert infer_default_phase("screed") == WorkPhase.SCREED
    assert infer_default_phase("electrical_point") == WorkPhase.ROUGH_MEP
    assert infer_default_phase("facade_insulation") == WorkPhase.FACADE_ROOF


def test_infer_default_phase_falls_back_to_finish() -> None:
    assert infer_default_phase("tiling_floor") == WorkPhase.FINISH


# --------------------------------------------------------------------------------------
# extract_fields_via_llm - retry-once-on-invalid-JSON behavior
# --------------------------------------------------------------------------------------


def test_extract_fields_via_llm_parses_valid_json_on_first_try() -> None:
    fn = _fake_completion({"rooms": ["bathroom"], "total_area_m2": 5.0, "work_items": []})

    fields = extract_fields_via_llm("ремонт ванной 5 кв.м", completion_fn=fn)

    assert fields.rooms == ["bathroom"]
    assert fields.total_area_m2 == 5.0


def test_extract_fields_via_llm_retries_once_on_malformed_json() -> None:
    fn = _fake_completion_sequence(["not json at all", {"rooms": ["kitchen"], "work_items": []}])

    fields = extract_fields_via_llm("some text", completion_fn=fn)

    assert fields.rooms == ["kitchen"]


def test_extract_fields_via_llm_raises_after_two_failures() -> None:
    fn = _fake_completion_sequence(["still not json", "also not json"])

    with pytest.raises(LLMParsingError):
        extract_fields_via_llm("some text", completion_fn=fn)


def test_extract_fields_via_llm_tolerates_code_fenced_response() -> None:
    fn = _fake_completion_sequence(['```json\n{"rooms": ["bathroom"], "work_items": []}\n```'])

    fields = extract_fields_via_llm("text", completion_fn=fn)

    assert fields.rooms == ["bathroom"]


# --------------------------------------------------------------------------------------
# assign_precision_level - pure function, table-driven
# --------------------------------------------------------------------------------------


def test_assign_precision_level_low_when_no_work_items() -> None:
    level, missing, questions = assign_precision_level([], is_old_building=None)

    assert level == PrecisionLevelEnum.LOW
    assert len(questions) <= 5
    assert questions  # LOW always gets clarifying questions


def test_assign_precision_level_low_when_only_generic_placeholder_item() -> None:
    items = [WorkItem(work_type="bathroom_renovation_generic", quantity=5, unit="m2", phase=WorkPhase.FINISH)]

    level, _missing, _questions = assign_precision_level(items, is_old_building=None)

    assert level == PrecisionLevelEnum.LOW


def test_assign_precision_level_mid_when_named_trades_missing_detail_fields() -> None:
    items = [
        WorkItem(work_type="demolition_tiling", quantity=5, unit="m2", phase=WorkPhase.DEMOLITION),
        WorkItem(work_type="tiling_floor", quantity=5, unit="m2", phase=WorkPhase.FINISH),
    ]

    level, missing, questions = assign_precision_level(items, is_old_building=True)

    assert level == PrecisionLevelEnum.MID
    assert any("substrate_condition" in m for m in missing)
    assert any("tile_size_cm" in m for m in missing)
    assert 1 <= len(questions) <= 5


def test_assign_precision_level_mid_when_is_old_building_unknown() -> None:
    items = [
        WorkItem(
            work_type="tiling_floor",
            quantity=5,
            unit="m2",
            material="ceramic tile",
            tile_size_cm="60x60",
            layout_pattern="straight",
            phase=WorkPhase.FINISH,
        )
    ]

    level, missing, _questions = assign_precision_level(items, is_old_building=None)

    assert level == PrecisionLevelEnum.MID
    assert "is_old_building" in missing


def test_assign_precision_level_high_when_all_fields_present() -> None:
    items = [
        WorkItem(
            work_type="demolition_tiling",
            quantity=17,
            unit="m2",
            substrate_condition="poor",
            phase=WorkPhase.DEMOLITION,
        ),
        WorkItem(
            work_type="tiling_floor",
            quantity=5,
            unit="m2",
            material="ceramic tile",
            tile_size_cm="120x60",
            layout_pattern="diagonal",
            phase=WorkPhase.FINISH,
            depends_on=[WorkPhase.DEMOLITION],
        ),
    ]

    level, missing, questions = assign_precision_level(items, is_old_building=True)

    assert level == PrecisionLevelEnum.HIGH
    assert missing == []
    assert questions == []


# --------------------------------------------------------------------------------------
# Design service dialogue seam (master prompt v2 section 4)
# --------------------------------------------------------------------------------------


def test_needs_design_service_clarification_true_when_unset() -> None:
    fn = _fake_completion({"rooms": ["bathroom"], "work_items": []})
    data = parse_renovation_request("ремонт ванной 5 кв.м", completion_fn=fn)

    assert needs_design_service_clarification(data) is True
    assert "projekt wnętrza" in DESIGN_SERVICE_QUESTION


def test_merge_design_service_answer_attaches_without_reparsing() -> None:
    fn = _fake_completion({"rooms": ["bathroom"], "work_items": []})
    data = parse_renovation_request("ремонт ванной 5 кв.м", completion_fn=fn)

    answer = DesignServiceRequest(needed=True, service_type="percent_of_budget", fee_percent="0.10")
    updated = merge_design_service_answer(data, answer)

    assert needs_design_service_clarification(updated) is False
    assert updated.design_service.fee_percent == answer.fee_percent
    # Original object is untouched (pure function, no mutation).
    assert data.design_service is None


# --------------------------------------------------------------------------------------
# parse_renovation_request - end to end with a fake LLM
# --------------------------------------------------------------------------------------


def test_parse_renovation_request_low_precision_end_to_end() -> None:
    fn = _fake_completion(
        {
            "rooms": ["bathroom"],
            "total_area_m2": 5.0,
            "work_items": [{"work_type": "bathroom_renovation_generic", "quantity": 5.0, "unit": "m2"}],
        }
    )

    data = parse_renovation_request("ремонт ванной 5 кв.м", completion_fn=fn)

    assert data.precision_level == PrecisionLevelEnum.LOW
    assert data.work_items[0].phase == WorkPhase.FINISH  # inferred, LLM omitted it
    assert data.is_heritage_site is False


def test_parse_renovation_request_high_precision_end_to_end() -> None:
    fn = _fake_completion(
        {
            "rooms": ["bathroom"],
            "total_area_m2": 5.0,
            "is_old_building": True,
            "work_items": [
                {
                    "work_type": "demolition_tiling",
                    "quantity": 17.0,
                    "unit": "m2",
                    "substrate_condition": "poor",
                    "phase": "demolition",
                },
                {
                    "work_type": "tiling_floor",
                    "quantity": 5.0,
                    "unit": "m2",
                    "material": "ceramic tile",
                    "tile_size_cm": "120x60",
                    "layout_pattern": "diagonal",
                    "phase": "finish",
                    "depends_on": ["demolition"],
                },
            ],
        }
    )

    data = parse_renovation_request("...", completion_fn=fn)

    assert data.precision_level == PrecisionLevelEnum.HIGH
    assert len(data.work_items) == 2
    assert data.missing_fields == []


def test_parse_renovation_request_raises_llm_parsing_error_when_llm_keeps_failing() -> None:
    fn = _fake_completion_sequence(["nope", "still nope"])

    with pytest.raises(LLMParsingError):
        parse_renovation_request("ремонт кухни", completion_fn=fn)


def test_parse_renovation_request_raises_when_work_item_missing_work_type() -> None:
    fn = _fake_completion({"rooms": ["bathroom"], "work_items": [{"quantity": 5.0, "unit": "m2"}]})

    with pytest.raises(LLMParsingError):
        parse_renovation_request("текст", completion_fn=fn)
