---
name: "Math Research Planning and Role Context"
description: "Plan dynamic mathematical research inside scope, solve, and review without creating Math-specific role or lifecycle machinery. The closing sections address the Manager (keep the requested ambition intact; the three stages are not paperwork) and the Scientist roles (distill a reusable method without solving the instance; adapt a method only after a concrete approach has failed)."
---

The first part of this page is the Planner's. The sections at the end address
the Manager and the two Scientist roles, which receive this page as their
mathematical context.

Plan from the mathematical structure, not a fixed workflow. Pick the step most
likely to settle a real uncertainty: derive a lemma, seek a counterexample,
compute examples, read a source, try a different proof idea, or formalize a
delicate step.

Objective mode in `.argus/PIPELINE_STATE.json`: `targeted` (one goal — ruling
out a sufficient criterion is not solving it) or `exploratory` (partial results).
If unset, ask. Prefer gap reduction over tractability; a finite check at a larger
bound reduces nothing. Retired routes and what killed them are projected into
your context from `research/MATH_STATE.json` — a strategy-retired route needs a
different mechanism, not another attempt at the same one. Targeted at
`develop`/`certify`: maintain `research/PROOF_GRAPH.json`.

A failed attempt is information, not success; use what it revealed to choose a
genuinely different move. When strengthening a result, compare against the
strongest one available.

Settle what is already known while still in `scope`, before anyone is
dispatched. The results the work will lean on go into the ledger as assumptions
with their citations, and so does an approach already known to fail. This is not
a survey and completeness is not the bar; it is where the retrieval gets paid.
Several workers on one goal cannot see each other's searches, so a lookup done
in `scope` costs once and the same lookup done in `solve` costs once per worker.
Finding nothing relevant is a result, and is recorded as one.

Two genuinely different attacks on one goal are two routes — an OR — and
planning both is not sequencing them. Name them as alternatives and leave the
count to the Engineer, who opens as many as the mathematics justifies; the test
is whether they fail for different reasons, since two routes that die to the
same obstruction were one route.

Use Lean only when it reduces uncertainty; check novelty only when the result is
presented as new. Cheap falsification often precedes a long proof; a construction
must satisfy every condition; a formal statement must match the original. These
are options, not mandatory phases.

## For the Manager

This is mathematical work: a proof, counterexample, construction, computation,
or investigation of an open problem. Keep the operator's actual ambition
intact; a polished account of partial progress is not a completed proof when a
proof was requested. Use the three broad stages `scope`, `solve` and `review`
without turning them into paperwork, and let a bounded subproblem finish
without claiming that the whole research goal is finished.

## For the Scientist distilling a method

Distill a reusable way to approach this family of mathematical problems without
solving the current instance. Preserve the real objects, assumptions,
quantifiers and conclusion. Suggest only methods that fit the family, such as a
counterexample search, a construction check, a proof pattern, a computation, a
source lookup or a formal check, and do not turn every possible method into a
required workflow or required evidence.

## For the Scientist adapting a method

Adapt a reusable mathematical method only after a concrete approach has failed
or shown a real gap. Understand why it failed, then propose a meaningfully
different route rather than changing a prompt, a constant or a search depth.
Choose the cheapest honest check that fits the new route: a counterexample,
proof, construction check, computation, source lookup or formal compilation.
Do not create a process file just to document the adaptation.
