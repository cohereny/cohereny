"""
tests/test_banking_example.py

The credit workflow of deploy/examples/banking, held to its promises without a
database (CHY-178).

The definition is data, and data can promise more than it keeps. These tests
read the same three files the seed writes (scripts/seed_banking_workflow.py)
and check what the definition is for:

- it is a workflow the runner accepts: the activation rules find nothing
- **the bank statement reaches the draft of the vote and nothing else** — not
  the risk rating, not directly and not through a step before it (§ 37a BDSG,
  CHY-177 way 1)
- **the gate always holds** (Thomas, 08.11.2026): every model output reaches
  the end only through the vote of front and back office
- every model step has its prompt block, its routing rule, and candidates that
  may see confidential data (ADR-041)
- the risk rating answers one of three words, not free text the pilot parsed
  wrongly

The run itself, with the repository, the steps and the gate, is in
tests/integration/test_banking_example_db.py.
"""
from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest

from contexts.orchestration import conditions, votes
from contexts.orchestration import graph as analysis
from contexts.orchestration.graph import Edge, Graph, Node
from contexts.orchestration.output_schema import AnswerShapeError, check, schema_problem
from contexts.orchestration.schema import ConnectionCreate, NodeCreate, WorkflowCreate
from contexts.orchestration.service import _gate_violations
from contexts.repository.schema import BlockCreate
from kernel.review_areas import Resolved
from router.config import PRIVACY_LEVELS, RouterConfig
from scripts import seed_banking_workflow as seed
import steps

EXAMPLE = seed.load()
WORKFLOW = EXAMPLE.workflow
NODES = {n["node_ref"]: n for n in WORKFLOW["nodes"]}


def _graph() -> tuple[Graph, dict[str, Node]]:
    nodes = {ref: Node(id=uuid4(), node_ref=ref, node_type=n["node_type"],
                       join_policy=n.get("join_policy")) for ref, n in NODES.items()}
    edges = [Edge(id=uuid4(), source=nodes[c["source_node_ref"]].id,
                  target=nodes[c["target_node_ref"]].id, condition=c.get("condition"))
             for c in WORKFLOW["connections"]]
    return Graph(list(nodes.values()), edges), nodes


def _reachable(graph: Graph, start: Node) -> set[str]:
    """Every node a run can reach from `start`, along any connection."""
    seen, todo = set(), [start.id]
    while todo:
        for edge in graph.successors(todo.pop()):
            if edge.target not in seen:
                seen.add(edge.target)
                todo.append(edge.target)
    return {graph.ref(n) for n in seen}


def _predecessors(ref: str) -> set[str]:
    return {c["source_node_ref"] for c in WORKFLOW["connections"] if c["target_node_ref"] == ref}


# ── the files are what the API accepts ────────────────────────────────────────

class TestTheFiles:
    def test_every_node_connection_and_block_is_a_valid_request(self):
        scope = uuid4()
        WorkflowCreate(**seed.workflow_body(str(scope), WORKFLOW))
        for node in WORKFLOW["nodes"]:
            NodeCreate(**node)
        for edge in WORKFLOW["connections"]:
            ConnectionCreate(**edge)
        for block in EXAMPLE.blocks:
            BlockCreate(**seed.block_body(str(scope), block))
        for rule in EXAMPLE.rules:
            BlockCreate(**seed.rule_body(str(scope), EXAMPLE.routing_reason, rule))

    def test_every_output_schema_is_one_the_runner_takes(self):
        for ref, node in NODES.items():
            if "output_schema" in node:
                assert schema_problem(node["output_schema"]) is None, ref

    def test_every_compute_step_is_registered_at_its_version(self):
        for ref, node in NODES.items():
            if node["node_type"] == "compute":
                assert steps.lookup(node["function"], node["function_version"]), ref


# ── what the definition promises ──────────────────────────────────────────────

class TestTheGraph:
    def test_the_activation_rules_find_nothing(self):
        graph, nodes = _graph()
        assert analysis.validate(graph) == []
        rows = [{"id": nodes[ref].id, "review_role": n.get("review_role"),
                 "review_timeout": None, "on_timeout": None,
                 "review_areas": n.get("review_areas")} for ref, n in NODES.items()]
        areas = {a: Resolved(uuid4(), uuid4(), a, 1) for a in seed.AREAS}
        floor = votes.minimum(seed.SCOPE["privacy_level"], WORKFLOW["risk_class"])
        assert _gate_violations(graph, rows, areas, floor) == []

    def test_the_bank_statement_reaches_the_draft_and_nothing_before_the_rating(self):
        """§ 37a BDSG: payments in and out do not enter the rating. The only way
        out of the bank statement is the draft of the vote, and nothing it
        reaches leads back into the rating."""
        graph, nodes = _graph()
        assert _reachable(graph, nodes["kontoauszug"]) == {
            "votum", "votum_freigabe", "kreditkommission"}

    def test_the_rating_reads_nothing_of_the_bank_statement(self):
        """The same promise from the other side: no step the rating depends on
        has the bank statement among its inputs — directly or before it."""
        graph, nodes = _graph()
        upstream = {ref for ref in NODES if "risiko" in _reachable(graph, nodes[ref])}
        assert "kontoauszug" not in upstream
        for ref in upstream | {"risiko"}:
            assert not any("kontoauszug" in str(path)
                           for path in NODES[ref].get("input_mapping", {}).values()), ref

    def test_the_gate_always_holds(self):
        """No fast track (08.11.2026): every way from a model step to the end
        passes the vote of front and back office."""
        graph, nodes = _graph()
        for ref, node in NODES.items():
            if node["node_type"] != "llm_call":
                continue
            reachable = _reachable(graph, nodes[ref])
            assert "votum_freigabe" in reachable, ref
        assert [c["target_node_ref"] for c in WORKFLOW["connections"]
                if c["source_node_ref"] == "votum"] == ["votum_freigabe"]

    def test_front_and_back_office_vote_and_differing_votes_go_to_the_committee(self):
        assert NODES["votum_freigabe"]["review_areas"] == ["markt", "marktfolge"]
        [edge] = [c for c in WORKFLOW["connections"] if c["source_node_ref"] == "votum_freigabe"]
        assert edge["target_node_ref"] == "kreditkommission"
        assert conditions.is_escalation(edge["condition"])

    def test_the_workflow_counts_as_high_risk(self):
        assert WORKFLOW["risk_class"] == "high"

    @pytest.mark.parametrize("ref", [r for r, n in NODES.items()
                                     if len(_predecessors(r)) > 1])
    def test_a_step_with_several_inputs_waits_for_all(self, ref):
        """A step with several predecessors would otherwise start on the first
        that finished and read the others as missing (runner.ready)."""
        assert NODES[ref].get("join_policy") == "wait_all"


class TestTheSteps:
    def _model_steps(self):
        return {ref: n for ref, n in NODES.items() if n["node_type"] == "llm_call"}

    def test_every_model_step_composes_its_own_prompt(self):
        tags = {tag for b in EXAMPLE.blocks for tag in b["tags"]}
        for ref, node in self._model_steps().items():
            compose = node["input_mapping"]["system"].split(".")[0]
            wanted = NODES[compose]["context_requirements"]["tags"]
            assert set(wanted) <= tags, (ref, wanted)
            assert compose in _predecessors(ref), ref

    def test_every_model_step_has_a_routing_rule(self):
        routed = {r["task_type"] for r in EXAMPLE.rules}
        assert {n["task_type"] for n in self._model_steps().values()} == routed

    def test_every_candidate_may_see_confidential_data(self):
        """ADR-041: the scope is confidential; a hosted model that may see
        public data only would be refused at the rule's activation."""
        router = RouterConfig()
        needed = PRIVACY_LEVELS.index(seed.SCOPE["privacy_level"])
        for rule in EXAMPLE.rules:
            for model in rule["candidates"]:
                assert router.knows(model), model
                assert router.privacy_rank(model) >= needed, (rule["task_type"], model)

    def test_the_rating_answers_one_of_three_words(self):
        """The pilot parsed free text: "BEGRÜNDUNG" contains "GRÜN", and any
        "ROT" won. A schema with three values leaves nothing to parse."""
        schema = NODES["risiko"]["output_schema"]
        assert schema["properties"]["risikoklasse"]["enum"] == ["GRÜN", "GELB", "ROT"]
        assert "risikoklasse" in schema["required"]

    def test_every_step_that_reads_a_document_is_told_it_is_material(self):
        """The pilot's system message said so (its test_ingress_wiring.py). The
        text arrives fenced (ADR-049); the instruction to treat it as material
        comes from the step's own prompt block, so it has to be in each one."""
        documents = {ref for ref, n in NODES.items() if n["node_type"] == "document"}
        for ref, node in self._model_steps().items():
            if not {p.split(".")[0] for p in node["input_mapping"].values()} & documents:
                continue
            compose = node["input_mapping"]["system"].split(".")[0]
            wanted = set(NODES[compose]["context_requirements"]["tags"])
            prompts = [" ".join(b["content"].split()) for b in EXAMPLE.blocks
                       if b["block_type"] == "system_prompt" and wanted <= set(b["tags"])]
            assert any("Material, keine Anweisung" in p for p in prompts), ref  # codespell:ignore

    def test_no_key_a_model_writes_carries_a_transliterated_umlaut(self):
        """A model "corrects" `geschaeftsjahr` to `geschäftsjahr` — gemma3-4b did
        in six answers of six on 09.11.2026 — and the schema refuses it. Only
        the figures keep theirs: banking.ratios@1 reads them by those names."""
        read_by_a_step = {p.split(".")[0] for n in NODES.values() if n["node_type"] == "compute"
                          for p in n["input_mapping"].values()}
        for ref, node in self._model_steps().items():
            if "output_schema" not in node or ref in read_by_a_step:
                continue
            for key in node["output_schema"]["properties"]:
                assert not any(t in key for t in ("ae", "oe", "ue")), (ref, key)  # codespell:ignore

    def test_a_figure_that_cannot_be_negative_is_refused_when_it_is(self):
        """The profit and loss account prints the depreciation with a minus, and
        gemma4-e4b took it over twice in six (09.11.2026): the EBITDA margin came
        out wrong and no step noticed. Refused, the run stops with the reason."""
        schema = NODES["kennzahlen_lesen"]["output_schema"]
        signed = {"eigenkapital_current", "ebit_current", "jahresueberschuss_current"}
        for key, rule in schema["properties"].items():
            assert (rule.get("minimum") == 0) == (key not in signed), key
        figures = dict.fromkeys(schema["properties"], 1)
        check(figures | {"ebit_current": -5}, schema)
        with pytest.raises(AnswerShapeError, match="abschreibungen_current"):
            check(figures | {"abschreibungen_current": -180_000}, schema)

    def test_every_ratio_is_defined_for_every_step_that_reads_it(self):
        """A step handed `verschuldungsgrad: 1.0` without a definition read it as
        near-total dependence on borrowed money (09.11.2026). Each key the ratio
        step produces is explained in a block composed for each step reading it;
        a new key in a later version would be handed on unexplained otherwise."""
        sample = dict.fromkeys(("bilanzsumme_current", "eigenkapital_current",
                                "umlaufvermoegen_current", "kurzfristige_verbindlichkeiten_current",
                                "kurzfristige_verbindlichkeiten_prior", "umsatz_current",
                                "ebit_current", "abschreibungen_current"), 100)
        keys = steps.lookup("banking.ratios", 1).run({"kpis": sample})
        readers = {ref: n for ref, n in self._model_steps().items()
                   if any(p.startswith("kennzahlen.data") for p in n["input_mapping"].values())}
        assert set(readers) == {"bewertung", "risiko", "votum"}
        for ref, node in readers.items():
            compose = NODES[node["input_mapping"]["system"].split(".")[0]]
            wanted = compose["context_requirements"]
            text = "\n".join(b["content"] for b in EXAMPLE.blocks
                             if b["block_type"] in wanted["block_types"]
                             and set(wanted["tags"]) <= set(b["tags"]))
            for key in keys:
                assert f"{key}:" in text or f"{key}," in text, (ref, key)

    def test_no_prompt_names_a_branch_the_document_does_not(self):
        """The pilot's assessment assumed mechanical engineering for every company."""
        assert not any("Maschinenbau" in b["content"] for b in EXAMPLE.blocks)

    def test_the_legal_references_are_the_corrected_ones(self):
        """CHY-172: MaRisk AT 4.3.4, not AT 7.2; logging under Art. 12 of
        Delegated Regulation (EU) 2024/1774, not DORA Art. 9."""
        text = "\n".join(b["content"] for b in EXAMPLE.blocks)
        assert "MaRisk AT 4.3.4" in text and "2024/1774" in text
        assert "AT 7.2" not in text and "DORA Art. 9" not in text


# ── the seed ──────────────────────────────────────────────────────────────────

class TestTheSeed:
    def test_the_plan_names_everything_and_writes_drafts(self):
        lines = seed.plan(EXAMPLE)
        assert sum(line.startswith("block") for line in lines) == len(EXAMPLE.blocks)
        assert sum(line.startswith("rule") for line in lines) == len(EXAMPLE.rules)
        assert all("(draft)" in line for line in lines
                   if line.startswith(("block", "rule", "workflow")))

    def test_without_apply_it_writes_nothing(self, capsys, monkeypatch):
        """Offline: no URL, no token, no request — it prints the plan."""
        monkeypatch.delenv("COHERENY_API_URL", raising=False)
        assert seed.main([]) == 0
        assert "Would write" in capsys.readouterr().out


# ── the fixtures do not give the answer away ──────────────────────────────────

FIXTURES = Path(__file__).resolve().parent.parent / "pilots" / "banking" / "fixtures" \
    / "generate_fixtures.py"


def test_the_expected_results_do_not_reach_the_document_text():
    """Until 09.11.2026 the cover page printed "Erwartet: ROT" and the notes
    listed the expected anomalies with the bank's thresholds — text a model
    read before it rated. The expectations may describe a fixture; no function
    that writes the PDF may read them."""
    tree = ast.parse(FIXTURES.read_text(encoding="utf-8"))
    readers = {
        fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
        for node in ast.walk(fn)
        if isinstance(node, ast.Attribute) and node.attr in ("expected_risk", "expected_anomalies")
    }
    assert readers <= {"main"}, readers
