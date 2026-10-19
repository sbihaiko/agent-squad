// Dashboard core: the same JSON Schema and the same rules as squad/graph.py,
// the canonical graph hash (squad.graph.canon_hash), and the adaptation overlay (squad/report.py).
// No DOM here. A test runs the bundle under node and compares it with the engine (tests/test_dashboard.py).
// ENGINE comes from scripts/build_dashboard.py: the schema and the registries, read from the engine's own modules.
function make(ENGINE) {
  const { isObj, same, clone, pyFloat, canonText, canonFromText, graphHash, cmpCodePoints, parseImport } = canonModule(ENGINE);
  const { SCHEMA, KINDS, resolve, validate } = schemaModule({ ENGINE, isObj, canonText, same, pyFloat });
  const { applyNodeEdit, fieldGroups, setField, addNode, removeNode, reviewGate, connect, disconnect, renameNode, layoutRename, changeKind, unseenValues, READ_ONLY_TOP } = editsModule({ SCHEMA, KINDS, isObj, same, clone, resolve });
  const { autoLayout, graphKids, layoutForSave, viewFit, viewFitFloor, viewZoomAt, viewPanBy, viewReveal } = layoutModule();
  const { REVIEW_NOTE, diffGraphs, declaredCaps, commandTemplate, reviewSheet, saveText, hashChip, hashRelation, draftRecord, draftOffer, saveGate, saveMode } = reviewModule({ canonText, graphHash, cmpCodePoints });
  const { snapshot, histNew, histRecord, histUndo, histRedo } = historyModule({ clone });
  const { num, sumOf, orNull, lastOf, tokenFigures, runTimeline } = figuresModule();
  const { parseRecord, parseLive, overlayRows, overlayFor } = recordModule({ graphHash });
  const { readDelay, liveState, openCalls } = liveModule({ lastOf, num, orNull });
  const { reasonParts, causeSummary, failureCauses, skillName, nodeLabel } = failuresModule({ orNull });
  const { issueNumber, runChildren, skillLabel, CHILD_COLUMN_HEIGHT, childLayout, dispatchChecks, freeSpot, childAnchor } = childrenModule({ num, openCalls, orNull, skillName });
  const { OUTCOME_STATE, STATES, inFlight, isDone, isRunning, nodeState, stateClass, childStateClass } = stateModule({ num });
  const { LEGEND, LEGEND_CHIPS, RUN_STATES, STATE_TEXT, HELP, httpSource, followPush } = sourcesModule({ STATES });
  const { pauseView, controlBody } = controlModule({ lastOf });
  const { scriptDiagram, scriptSvg, guiRun } = scriptModule();
  const { runCap, runTotals, totalsCells, issueProgress, progressCells, fmt } = totalsModule({ lastOf, num, sumOf, tokenFigures, models: ENGINE.models });
  const { unresolvedChildren, replayAt, moveCursor } = replayModule({ num, orNull });
  const { stateMark, childMark, splitChildren, groupKey, isGroupKey, toggleKey, DOUBLE_PRESS_MS, isDoublePress, childRoster, groupLayout, childPlan, childBus, CARD_NAMES, cardGroups, cardBody } = groupsModule({ OUTCOME_STATE, STATES, inFlight, isDone, isRunning, childAnchor, childLayout, fmt, freeSpot, num, sumOf });
  const { nodeHarness, runStats } = statsModule({ causeSummary, dispatchChecks, failureCauses, lastOf, liveState, num, openCalls, orNull, runCap, runChildren, runTimeline, runTotals, skillLabel, sumOf, tokenFigures });

  return { scriptDiagram, scriptSvg, guiRun, pauseView, controlBody, LEGEND, LEGEND_CHIPS, RUN_STATES, STATE_TEXT, HELP, httpSource, followPush, snapshot, histNew, histRecord, histUndo, histRedo, REVIEW_NOTE, diffGraphs, declaredCaps, commandTemplate, reviewSheet, saveText, hashChip, hashRelation, draftRecord, draftOffer, layoutForSave, saveGate, saveMode, viewFit, viewFitFloor, viewZoomAt, viewPanBy, viewReveal, validate, canonText, canonFromText, graphHash, parseImport, parseRecord, parseLive, readDelay, liveState, replayAt, moveCursor, unresolvedChildren, overlayRows, overlayFor, runStats, runTotals, runCap, totalsCells, issueProgress, progressCells, nodeState, stateClass, childStateClass, inFlight, isRunning, graphKids, nodeLabel, issueNumber, reasonParts, causeSummary, failureCauses, runChildren, nodeHarness, childLayout, childAnchor, dispatchChecks, fmt, applyNodeEdit, reviewGate, fieldGroups, setField, addNode, removeNode, connect, disconnect, renameNode, layoutRename, changeKind, unseenValues, freeSpot, stateMark, childMark, splitChildren, groupKey, isGroupKey, toggleKey, DOUBLE_PRESS_MS, isDoublePress, childRoster, groupLayout, childPlan, childBus, CARD_NAMES, cardGroups, cardBody, autoLayout, CHILD_COLUMN_HEIGHT, READ_ONLY_TOP, KINDS };
}
