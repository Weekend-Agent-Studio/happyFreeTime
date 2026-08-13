# HappyFreeTime Domain Glossary

## Actor

The person or system identity acting in a session. An Actor may be a demo, anonymous, or registered identity.

## Session

A continuous planning conversation owned by one Actor. A Session is also the recovery scope used by the orchestration graph.

## Run

One processing cycle triggered by a user turn within a Session.

## Interpretation

The validated semantic reading of one user turn. It contains intent, commands, raw constraints, evidence, and confidence, but no environment facts or defaults.

## Raw Constraint

A planning-related expression preserved from the user's language. It may be precise, fuzzy, or unresolved and is not yet safe for planning calculations.

## Normalized Constraint

A planning value with a stable type and an explicit source. It is the only form of constraint consumed by planning.

## Assumption

A system-selected, user-editable value used when a non-blocking constraint is absent. Assumptions must be visible to the user.

## Blocking Constraint

Missing or ambiguous information that prevents the current action from proceeding safely or feasibly. It requires a user question instead of an Assumption.

## Plan

A feasible itinerary composed of ordered Stops and Route Legs, with costs, evidence, scores, and execution actions.

## Stop

A place-based activity in a Plan, such as an attraction, restaurant, cafe, or dessert venue.

## Route Leg

Travel between two adjacent Stops, including mode, distance, duration, source, and degradation status.

## Order

The durable business record of executing one selected Plan. Graph recovery state is not the source of truth for an Order.

## Provider

An implementation that supplies external or simulated facts through a stable domain interface. Providers may operate in live, record, replay, or mock mode.
