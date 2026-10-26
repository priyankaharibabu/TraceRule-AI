"""Phase 5 text-integrity regression tests."""

import json
import tempfile
from pathlib import Path

import pytest

from backend.services.structured_extraction import (
    ExtractionError,
    validate_extraction_response,
    _find_source_quote,
    _detect_merged_words,
)


@pytest.fixture
def sample_evidence():
    return {
        "evidence_id": "EVID-001",
        "document_id": "DOC-CB-2025",
        "passage_text": "Banks shall maintain proper data privacy policies for all customer information. Data must be stored securely with encryption.",
        "printed_page_number": "12",
        "page_number": "12",
        "paragraph_reference": "15",
    }


def test_exact_quote_matching(sample_evidence):
    """Test exact substring matching."""
    response = {
        "document_id": "DOC-CB-2025",
        "evidence_id": "EVID-001",
        "page": 12,
        "candidate_obligation": "maintain proper data privacy policies",
        "candidate_type": "explicit_requirement",
        "supporting_quotation": "maintain proper data privacy policies",
        "uncertainty": "Low uncertainty",
        "confidence": 0.9,
        "reasoning": "Explicit requirement in source",
        "requires_human_review": True,
    }
    
    result = validate_extraction_response(json.dumps(response), sample_evidence)
    assert result["candidate_obligation"] == "maintain proper data privacy policies"
    # Don't assert exact offsets - they depend on whitespace normalization
    assert "source_quote_start" in result
    assert "source_quote_end" in result
    start = result["source_quote_start"]
    end = result["source_quote_end"]
    assert sample_evidence["passage_text"][start:end] == "maintain proper data privacy policies"


def test_unicode_normalization_preserved():
    """Test that Unicode normalization doesn't match partial characters."""
    source = "Résumé review required"
    # "Resume" (without accents) should NOT match "Résumé"
    quote = "Resume"
    
    result = _find_source_quote(source, quote)
    assert result is None  # Should not match partial decomposition


def test_whitespace_normalization():
    """Test whitespace run normalization."""
    source = "Banks  shall   maintain\tproper\r\npolicies"
    quote = "Banks shall maintain proper policies"
    
    result = _find_source_quote(source, quote)
    assert result is not None
    start, end = result
    assert source[start:end] == "Banks  shall   maintain\tproper\r\npolicies"


def test_missing_spaces_detection():
    """Test detection of missing spaces (merged words)."""
    # This should trigger merged word detection
    text = "Banks shall maintain proper dataprivacy policies for customerinformation"
    merged = _detect_merged_words(text)
    
    # "dataprivacy" = "data" + "privacy" (both in common words)
    # "customerinformation" = "customer" + "information" (both in common words)
    assert len(merged) >= 1
    assert any("dataprivacy" in token.lower() for token in merged)


def test_legitimate_compounds_not_flagged():
    """Test legitimate compound words are not flagged as merged."""
    text = "The bank cannot withhold information therefrom"
    merged = _detect_merged_words(text)
    
    # "cannot" is in legitimate compounds
    # "therefrom" is in legitimate compounds  
    assert len(merged) == 0


def test_paraphrased_quote_rejected(sample_evidence):
    """Test that paraphrased quotes are rejected."""
    response = {
        "document_id": "DOC-CB-2025",
        "evidence_id": "EVID-001",
        "page": 12,
        "candidate_obligation": "Banks need to have good data privacy rules",
        "candidate_type": "explicit_requirement",
        "supporting_quotation": "Banks need to have good data privacy rules",  # Paraphrase, not exact
        "uncertainty": "Medium uncertainty",
        "confidence": 0.7,
        "reasoning": "Paraphrased requirement",
        "requires_human_review": True,
    }
    
    with pytest.raises(ExtractionError) as exc:
        validate_extraction_response(json.dumps(response), sample_evidence)
    
    assert exc.value.code == "ungrounded_quotation"


def test_interpretation_candidate_ungrounded(sample_evidence):
    """Test interpretation candidates are flagged as ungrounded."""
    response = {
        "document_id": "DOC-CB-2025",
        "evidence_id": "EVID-001",
        "page": 12,
        "candidate_obligation": "Implement comprehensive data protection framework",
        "candidate_type": "interpretation",
        "supporting_quotation": "maintain proper data privacy policies",
        "uncertainty": "High uncertainty - interpretation",
        "confidence": 0.6,
        "reasoning": "Interpretation of privacy policy requirement",
        "requires_human_review": True,
    }
    
    result = validate_extraction_response(json.dumps(response), sample_evidence)
    assert result["candidate_obligation_grounded"] is False
    assert result["candidate_obligation"] == "Implement comprehensive data protection framework"  # Kept as-is


def test_wrong_offsets_rejected(sample_evidence):
    """Test that manually tampered offsets would be caught by validation."""
    # This would require mocking the structured_extraction output
    # The validation in workflow would catch mismatched offsets
    pass


def test_line_wrapping_normalization():
    """Test line-wrapped text normalization."""
    source = "This is a long requirement that\nwraps across multiple lines\nfor readability"
    quote = "This is a long requirement that wraps across multiple lines for readability"
    
    result = _find_source_quote(source, quote)
    assert result is not None


def test_model_quote_preserved(sample_evidence):
    """Test that model's original quote is preserved separately."""
    response = {
        "document_id": "DOC-CB-2025",
        "evidence_id": "EVID-001",
        "page": 12,
        "candidate_obligation": "data privacy policies",
        "candidate_type": "explicit_requirement",
        "supporting_quotation": "data privacy policies",
        "uncertainty": "Low",
        "confidence": 0.8,
        "reasoning": "Test",
        "requires_human_review": True,
    }
    
    result = validate_extraction_response(json.dumps(response), sample_evidence)
    assert "model_supporting_quotation" in result
    assert result["model_supporting_quotation"] == "data privacy policies"
    assert "model_candidate_obligation" in result
    assert result["model_candidate_obligation"] == "data privacy policies"


def test_evidence_mismatch_rejected(sample_evidence):
    """Test that evidence/document mismatch is rejected."""
    response = {
        "document_id": "WRONG-DOC",  # Mismatch
        "evidence_id": "EVID-001",
        "page": 12,
        "candidate_obligation": "test",
        "candidate_type": "explicit_requirement",
        "supporting_quotation": "test",
        "uncertainty": "Test",
        "confidence": 0.8,
        "reasoning": "Test",
        "requires_human_review": True,
    }
    
    with pytest.raises(ExtractionError) as exc:
        validate_extraction_response(json.dumps(response), sample_evidence)
    
    assert exc.value.code == "reference_mismatch"


def test_page_mismatch_rejected(sample_evidence):
    """Test that page mismatch is rejected."""
    response = {
        "document_id": "DOC-CB-2025",
        "evidence_id": "EVID-001",
        "page": 99,  # Wrong page
        "candidate_obligation": "test",
        "candidate_type": "explicit_requirement",
        "supporting_quotation": "test",
        "uncertainty": "Test",
        "confidence": 0.8,
        "reasoning": "Test",
        "requires_human_review": True,
    }
    
    with pytest.raises(ExtractionError) as exc:
        validate_extraction_response(json.dumps(response), sample_evidence)
    
    assert exc.value.code == "page_mismatch"


def test_invalid_json_rejected(sample_evidence):
    """Test that malformed JSON is rejected."""
    with pytest.raises(ExtractionError) as exc:
        validate_extraction_response("not valid json", sample_evidence)
    
    assert exc.value.code == "malformed_json"


def test_missing_required_fields_rejected(sample_evidence):
    """Test that missing required fields are rejected."""
    response = {
        "document_id": "DOC-CB-2025",
        "evidence_id": "EVID-001",
        "page": 12,
        # Missing candidate_obligation
        "candidate_type": "explicit_requirement",
        "supporting_quotation": "test",
        "uncertainty": "Test",
        "confidence": 0.8,
        "reasoning": "Test",
        "requires_human_review": True,
    }
    
    with pytest.raises(ExtractionError) as exc:
        validate_extraction_response(json.dumps(response), sample_evidence)
    
    assert exc.value.code == "invalid_fields"


def test_no_candidate_type(sample_evidence):
    """Test 'no_candidate' type validation."""
    response = {
        "document_id": "DOC-CB-2025",
        "evidence_id": "EVID-001",
        "page": 12,
        "candidate_obligation": "",  # Must be empty
        "candidate_type": "no_candidate",
        "supporting_quotation": "",  # Must be empty
        "uncertainty": "No obligation identified",
        "confidence": 0.5,
        "reasoning": "No clear obligation in passage",
        "requires_human_review": True,
    }
    
    result = validate_extraction_response(json.dumps(response), sample_evidence)
    assert result["candidate_type"] == "no_candidate"
    assert result["candidate_obligation"] == ""
    assert result["supporting_quotation"] == ""


def test_no_candidate_with_content_rejected(sample_evidence):
    """Test that 'no_candidate' with content is rejected."""
    response = {
        "document_id": "DOC-CB-2025",
        "evidence_id": "EVID-001",
        "page": 12,
        "candidate_obligation": "Some obligation",  # Should be empty
        "candidate_type": "no_candidate",
        "supporting_quotation": "",  # Empty is OK
        "uncertainty": "Test",
        "confidence": 0.5,
        "reasoning": "Test",
        "requires_human_review": True,
    }
    
    with pytest.raises(ExtractionError) as exc:
        validate_extraction_response(json.dumps(response), sample_evidence)
    
    assert exc.value.code == "unsupported_candidate"