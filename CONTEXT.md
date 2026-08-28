# HappyFreeTime Domain Glossary

This glossary defines the project language used in requirements, code, tests, and interviews. It describes domain meaning rather than implementation details.

## Language

**Actor**:
The person or system identity acting in a Session. An Actor may be a demo, anonymous, or registered identity.

**Session**:
A continuous planning conversation owned by one Actor. It is also the recovery boundary for an interrupted planning flow.

**Planning Run**:
The processing lifecycle created for one logical planning submission within a Session. Retries that share a Request ID belong to the same Planning Run rather than creating duplicate work.

**Request ID**:
A client-generated identifier for one logical message submission. Retries reuse it, while a genuinely new user action receives a new value.

**Session Status**:
The user-facing lifecycle state of a Session, such as waiting for input, completed, or abandoned. It is independent of the orchestration engine's current node.

**Session View**:
A stable read model that restores the planning workspace from relevant session records and workflow state. It exposes product concepts rather than internal checkpoint structures.

**Interpretation**:
The validated semantic reading of one user turn. It may contain intent, commands, Raw Constraints, evidence, and confidence, but does not add environment facts or product defaults.

**Raw Constraint**:
A planning-related expression preserved from the user's language. It may be precise, fuzzy, or unresolved and is not yet safe for planning calculations.

**Normalized Constraint**:
A planning value with a stable type and explicit provenance. It is the constraint form consumed by planning.

**Constraint Source**:
The provenance of a Normalized Constraint, such as explicit user language, an evidence-based inference, system context, or a default rule. Source answers “where did this value come from?” and is independent of Constraint Strength.

**Constraint Strength**:
Whether planning must satisfy a constraint or may trade it off. A constraint can be a Hard Constraint or a Planning Preference regardless of its source.

**Hard Constraint**:
A condition that a feasible Plan must satisfy. A candidate with a Constraint Violation is removed rather than merely ranked lower.

**Planning Preference**:
A desired but relaxable condition that influences retrieval, scoring, ranking, or a reported Tradeoff. Avoid treating every user-explicit expression as automatically hard.

**Planning Intent**:
An evidence-linked description of desired role coverage, precedence, stop-count range, pace, and themes. It guides candidate generation and semantic ranking but does not prove feasibility or determine Constraint Strength.
_Avoid_: LLM plan, free-form itinerary

**Assumption**:
A system-selected, user-editable value used when a non-blocking constraint is absent. Assumptions must be visible to the user and are distinct from evidence-based user inference.

**Blocking Constraint**:
Missing or ambiguous information that prevents the current action from proceeding safely or feasibly. It requires a user question instead of an Assumption.

**Stop Candidate**:
A single activity, restaurant, or other place-based resource eligible for inclusion in a Plan after retrieval and initial checks.

**Stop Role**:
The purpose a Stop fulfills within a Plan, such as activity, lunch, dinner, or break. It is distinct from resource category: one restaurant may fulfill lunch or dinner, and one cafe may fulfill a break.

**Plan Skeleton**:
A bounded structural shape for a Plan, defined by ordered or partially ordered Stop Roles, required or optional roles, and a stop-count range, without selecting concrete resources.
_Avoid_: route, Plan Strategy, fixed POI template

**Plan**:
A feasible itinerary composed of ordered Stops and Route Legs, with costs, evidence, scores, and execution actions.

**Plan Composition Fingerprint**:
A stable identifier for the ordered Plan Skeleton and concrete resources of a Plan. It supports candidate deduplication but is not a Plan's identity and may recur across Sessions or Planning Runs.
_Avoid_: Plan ID, persistence ID

**Plan Version**:
An immutable planning outcome produced by one Planning Run under one constraint snapshot. A later successful run may supersede it without rewriting its history.

**Selected Plan**:
A candidate explicitly confirmed by an Actor for downstream execution. The Plan currently open in the interface is not selected merely because it is being viewed.

**Plan Strategy**:
The named optimization emphasis of a Plan, such as lower cost or shorter travel. It changes ranking priorities but does not define the Plan Skeleton or replace the Plan's structured Stops, Route Legs, timeline, and totals.

**Stop**:
A scheduled visit to a place-based resource that fulfills a Stop Role in a Plan, such as an attraction, restaurant, cafe, or dessert venue.

**Route Leg**:
Travel between two adjacent Stops, including mode, distance, duration, source, and degradation status.

**Plan Verification**:
The evaluation of an assembled Plan against whole-itinerary constraints, including its actual sequence, arrival times, totals, and route effects.

**Constraint Violation**:
A candidate-level failure to satisfy one Hard Constraint. The violation explains why that candidate is infeasible.

**Constraint Conflict**:
The aggregate no-solution result produced when every candidate is eliminated by one or more Constraint Violations. It should explain the blocking constraints and possible relaxation directions.

**Tradeoff**:
An acceptable sacrifice against a Planning Preference in an otherwise feasible Plan. It must not be used to excuse a Hard Constraint violation.

**Warning**:
Non-blocking information about uncertainty, stale data, degraded capability, or execution risk. A Warning does not make a Plan infeasible by itself.

**Source Fact**:
A value obtained from an identified external dataset or Provider, with enough provenance to state where and when it was observed. Source provenance does not by itself mean the value is currently verified.

**Estimate**:
A project-computed approximation used when its uncertainty is acceptable and visible. An Estimate is not a Source Fact and cannot prove a strict Hard Constraint unless the domain contract explicitly permits it.

**Unknown Fact**:
A required value for which no reliable evidence is available. Unknown is neither a passing result nor a Constraint Violation by itself; it may produce a Warning or prevent proof of a strict constraint.

**Fixture**:
Deliberately simulated data used for tests or demonstrations. A Fixture must remain identifiable and must not be silently merged into a source-derived production snapshot.

**Candidate Set**:
The result of planning: either one or more feasible Plans or a Constraint Conflict. These outcomes should be mutually exclusive.

**Order**:
The durable business record of executing one selected Plan. Recovery state is not the source of truth for an Order.

**Provider**:
An implementation that supplies external or simulated facts through a stable domain interface. Providers may operate in live, record, replay, or mock mode.
