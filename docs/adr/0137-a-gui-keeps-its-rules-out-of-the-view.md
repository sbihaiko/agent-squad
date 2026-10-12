# ADR-0137: A GUI keeps its rules out of the view

## Status

Accepted.

## Context

A screen changes for two unrelated reasons: how it looks, and what it decides.
Mixed in one file, neither can be tested without a host, a window and a human
looking at it. The rule becomes unverifiable at exactly the moment it matters,
and the appearance change risks the decision.

Asking an agent for "MVVM" in a prompt guarantees nothing. What is verifiable
is the direction of the dependency: where a rule is allowed to live, and what
is allowed to reach it.

## Decision

1. **A view is markup and wiring.** The view file and its code-behind
   construct, bind and delegate. They do not decide.
2. **The rule lives where no host is needed to test it.** It is expressed in a
   type that compiles and runs without the GUI toolkit and without the
   application's native dependencies, so the ordinary test run exercises it.
3. **The view model is the only thing the view talks to.** A view does not
   reach a service, a file, a client or application state directly; it states
   an intent, and the decision behind that intent is made in the rule layer.
4. **The check is the direction of the dependency, not a reading of the
   code.** The host-free test project compiles the rule layer — that compile is
   the firewall — and a second check refuses a code-behind that reaches the
   domain instead of delegating to its view model.
5. **What this does not require.** A named framework, a folder layout, or a
   view model for every view. It requires the direction: a rule reachable
   without a host, and a view that carries none.

## Guard

Half of it exists in the host: the host-free compile of the rule layer, as
`scripts/verify-ui-logic-firewall.sh` does in MesenCE, keeps the rule testable
without a host. The other half is not yet named: a check that refuses a
code-behind file reaching the domain directly. Until that check exists, this
rule is a draft (ADR-0018).
