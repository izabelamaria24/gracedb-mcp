# Agent workflows: plan

Reusable workflows built on the existing tools, so users get useful results without
knowing which tools to chain.

## Packaging

| Form | Where it lives | Reaches | Use for |
| --- | --- | --- | --- |
| **MCP prompt** | `@app.mcp.prompt()` in `prompts.py` | Every MCP client (slash commands in Claude Desktop, VS Code, ...) | Workflows that only need this server's tools |
| **Agent skill** | `skills/<name>/SKILL.md` in this repo | Claude Code, Copilot and other skill-aware agents; listable on mcprush | Workflows that also need the agent's own abilities (running Python, writing files) |

## Workflows

| # | Workflow | Form | Status |
| --- | --- | --- | --- |
| 1 | Alert briefing | Prompt | Done |
| 2 | Event dossier | Prompt | Done |
| 3 | Alert vs. final result | Prompt | Done |
| 4 | Open-data analysis starter | Skill | Done |
| 5 | Alert audit | Prompt | Done |

### 1. Alert briefing (`alert_briefing`)

"Anything new in the last N hours?" For astronomers deciding whether to follow up.

- Arguments: `hours` (default 24), `max_far` (default 3.8e-7 Hz, about one false alarm per
  month), `category` (`Production` for real alerts, `MDC` for the simulated alerts GraceDB
  creates about hourly, `Test`).
- Steps: `search_superevents` over the window, then for each event `get_event_labels`
  (flag retractions, data-quality vetoes, injections), `get_event_metadata`
  (classification, astrophysical probability), and the sky-map image via
  `download_event_files` when `SKYMAP_READY`.
- Output: one-line verdict per event, then a short block each: significance, class,
  review status, sky map. Say plainly when nothing matched.
- Guardrails: low-latency values change; no public strain exists yet for recent alerts,
  so do not call the strain tools. MDC and Test events must be called simulations.
  Labels without a known meaning (e.g. `HIGH_PROFILE`) are reported, not guessed.

### 2. Event dossier (`event_dossier`)

Everything about one event, for scientists, students or the public.

- Arguments: `event` (GraceDB id like `S190814bv` or catalogue name like `GW190814`),
  `audience` (`scientist`, `student` or `public`).
- Steps: resolve both identities (`get_catalog_event` maps either way), then
  `get_event_metadata`, `get_event_labels`, sky map, `check_data_availability`.
- Output sections: summary, significance and review, source (alert classification and
  final parameters with 90% intervals), sky localisation, public data, links and the
  GWOSC citation.
- Guardrails: never mix up alert-time and final values; say when an event is not in a
  catalogue yet (or was retracted).

### 3. Alert vs. final result (`alert_vs_catalog`)

How did the low-latency classification hold up once the final analysis was published?
Combines GraceDB and the GWOSC catalogues, which few tools do.

- Arguments: `event` for one event, or `catalog` (e.g. `GWTC-3-confident`) plus
  `max_events` (default 15) for a batch.
- Steps: `get_catalog_event` for final masses with 90% intervals, `get_event_metadata`
  on the matching GraceDB id for the alert-time `p_astro`.
- Comparison rule: a component counts as a neutron star below 3 solar masses (the
  boundary `p_astro` uses); flag events whose 90% interval straddles it as ambiguous.
- Output: table of event, alert class (top `p_astro` entry), final masses, final class,
  agreement; then a short discussion of the disagreements.
- Guardrails: events before O3 have no GraceDB superevent; skip and count them.

### 4. Open-data analysis starter (skill)

Find events, check data, download strain, then write and run a notebook that whitens
the data and shows the chirp in a Q-transform. Needs code execution, hence a skill.

- Lives in `skills/gw-open-data-analysis/`: `SKILL.md` plus `scripts/qscan.py`. The script
  reads the server's strain files, or fetches from GWOSC itself when the server runs in
  Docker. It writes Q-transform, whitened-strain and ASD plots and prints JSON with the
  loudest tile per detector.
- `scripts/matched_filter.py` measures the SNR, the way LIGO detects signals:
  - the noise spectrum is a Welch estimate (scipy);
  - the data is Fourier-transformed with `np.fft.rfft`, weighted by the template
    (IMRPhenomD from lalsuite, no spins) and by 1/noise, then transformed back with
    `np.fft.ifft`;
  - a bank of 21 mass scalings covers the rounded catalogue masses;
  - SNRs are read within 30 ms across detectors and compared with each detector's own
    background;
  - `--gate` removes glitches.

  It reproduces published SNRs to within a few percent: GW150914 H1 19.5 / L1 13.6;
  GW170817 H1 17.9 / L1 24.2 with the L1 glitch gated.
- Steps: `get_catalog_event`, then `check_data_availability`, `download_strain_data` and the
  script. The window depends on chirp mass; the agent looks at the images before reporting.
- Checked against GW150914, GW170814, GW190521_074359, GW190521, GW190814 and GW170817.
- Guardrails:
  - visibility follows each detector's SNR (GW190814: clear in L1, faint in H1, none in V1);
  - normalised energy is not a detection statistic;
  - glitches (GW170817's L1) can swamp the signal;
  - never estimate parameters from the plots.

### 5. Alert audit (`alert_audit`)

For a period: how many alerts, how many retracted and why (labels), time to retraction,
which detectors were involved.

- Arguments: `start`, `end` (default now), `category`.
- Tool `audit_alerts` does the counting in one call, since a run has thousands of superevents
  (O4: 5,461):
  - totals come from GraceDB's `numRows` on one-row pages;
  - significant alerts (`GCN_PRELIM_SENT`) and retractions (`ADVNO`) are fetched as records;
  - hours to retraction come from the ADVNO label time;
  - the reviewer's email on labels is never returned.
- Reasons: GraceDB does not record them. The prompt reads the LVK retraction circular for
  each retraction (`search_gcn_circulars`, `get_gcn_circular`).
- Checked live:
  - O3: 80 superevents, 77 significant alerts, 24 retracted;
  - O4a: 1,697 superevents, 88 significant alerts, 1,605 low-significance alerts, 11 retracted.

  Each takes about 5 s.

## Testing

- Each prompt renders through a real MCP client (`prompts/list`, `prompts/get`).
- Guard test: every tool a prompt names exists, so renaming a tool cannot silently break
  a prompt.
- Manual run of each prompt in an MCP client against live services before release.

## Future: more data sources

No plugin framework until two sources share a shape: each new source is a client module
plus a `tools/<source>.py`, which the current layout already supports.

| # | Step | Adds | Size |
| --- | --- | --- | --- |
| A | Simulated alerts in the briefing (`category`) | Try workflows between observing runs | Done |
| B | More GraceDB and GWOSC data | `list_observing_runs`, `check_data_quality` (all GWOSC veto categories and hardware injections), each pipeline's best candidate in `get_event_metadata`, pipeline and detectors in search results; GPS/UTC without gwpy | Done |
| B2 | Per-pipeline candidate events (G-ids) and GraceDB's `gwtc` endpoint | Both need an LVK login (HTTP 401 anonymously); only worth it for authenticated users | Blocked for public use |
| C | Sky-map analysis | `analyze_skymap`: 50%/90% credible areas, distance, most probable position, probability at positions and inside telescope fields. Pure Python (works in the slim image); matches `ligo.skymap` exactly on real maps (`scripts/validate_skymap.py`) | Done |
| D | GCN (NASA's General Coordinates Network) | `search_gcn_circulars` (by event: S-id, GW name mapped to its GraceDB id, GRB, IceCube; or Lucene query, dates, relevance sort, pages) and `get_gcn_circular`. Stdlib HTTP, cached. Dossier and briefing prompts use them | Done |
| D2 | GCN Notices (machine-readable alerts over Kafka) | Real-time IceCube, Fermi, Swift, LVK notices; needs GCN client credentials and a long-running consumer | Not started |

Caveat: GCN has no documented JSON search API. Search uses the data route behind the archive's
search page (`?_data=routes/circulars._archive._index`); single circulars use the documented
`/circulars/<id>.json`. If GCN changes its site, search fails with a clear error and needs an
update; the bulk archive (`/circulars/archive.json.tar.gz`, ~32 MB) is the documented fallback.

Steps A-D and workflows 1-5 are done. Remaining: D2.
