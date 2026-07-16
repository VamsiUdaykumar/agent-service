# Screenshots referenced by the top-level README

Drop these in after `make demo` + a live Grafana Cloud run (see main README's
"Screenshots" section for exactly what each should show):

- `researcher-waterfall.png` — Tempo trace waterfall for an `agent-researcher`
  run: root span, per-step children, sub-agent nesting one level deep.
- `flaky-retry-trace.png` — Tempo trace for an `agent-flaky` run showing
  retry attempts as distinct child spans (bars + gaps for backoff).
- `dashboard.png` — the six-panel Grafana dashboard
  (`grafana/dashboard.json`) with `make demo`'s data loaded, post M9.T6 fix
  (legends reading real values, not `$0.000000`/`0`).
