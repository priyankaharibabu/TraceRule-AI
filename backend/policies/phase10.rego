package tracerule.phase10

import rego.v1

default allow := false

# This policy permits only explicitly labelled synthetic test inputs.
# It is a sandbox eligibility gate, not a compliance decision.
allow if {
	input.test_provenance.kind == "synthetic"
	input.rule.source_obligation.dataset_kind == "synthetic"
	input.rule.status == "test"
	input.rule.approved_by == null
	input.case.dataset_kind == "synthetic"
	input.case.case_id == input.test_provenance.case_id
}
