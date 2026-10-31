/* The pane rule of the hub page (ADR-0137): which embedded run dashboard follows its run live. No document, no timer, no
   storage: hub.py inlines this file into the page, and the tests run it alone in node.
   An embedded dashboard holds a stream on its run's server and rereads its record on its own beat, so the page's cost
   is the number of embedded dashboards that hold a source. The budget is ONE, whatever the run count: with N runs on
   the page, the focused pane follows its run live and the others show the state the page already polled.
   A dashboard that is not followed holds no source at all (the page detaches it), so it costs no stream and no server timer. */
const LIVE_BUDGET=1;
const paneFocus=(slots,focus)=>slots.includes(focus)?focus:(slots[0]||null);   // a focus that left the page falls back to the first pane
function embedPlan(slots,focus){   // the one run that is followed live: the focused pane, if the page shows one
  return{pane:paneFocus(slots,focus)};
}
