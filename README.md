# GraceDB MCP Server

[![PyPI](https://img.shields.io/pypi/v/gracedb-mcp)](https://pypi.org/project/gracedb-mcp/)
[![CI](https://github.com/izabelamaria24/gracedb-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/izabelamaria24/gracedb-mcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/pypi/l/gracedb-mcp)](https://github.com/izabelamaria24/gracedb-mcp/blob/main/LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23810872.svg)](https://doi.org/10.5281/zenodo.23810872)
[![GraceDB MCP on mcprush](https://mcprush.com/badge/gracedb-mcp/score.svg)](https://mcprush.com/to/gracedb-mcp?ref=badge)

<!-- mcp-name: io.github.izabelamaria24/gracedb-mcp -->

An open-source [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that
exposes **LIGO/Virgo/KAGRA gravitational-wave alerts** from
[GraceDB](https://gracedb.ligo.org) to LLM agents. It bridges the official
[`ligo-gracedb`](https://ligo-gracedb.readthedocs.io) client with the MCP standard so an
agent can query, classify, and fetch data for gravitational-wave candidate events.

Intended as the **data-ingestion layer** for a downstream signal-processing / anomaly-detection
pipeline (e.g. isolating astrophysical "chirps" from instrumental glitches).

## Tools

| Tool | What it does |
| --- | --- |
| `search_superevents` | Search recent superevents by time window and significance (FAR / astrophysical probability). |
| `get_event_metadata` | Full superevent record + source classification (BBH / BNS / NSBH / Terrestrial). |
| `download_event_files` | Download GraceDB artifacts: skymaps, `p_astro.json`, PE results, logs. |
| `download_strain_data` | Fetch **public** strain time series from GWOSC around the event GPS time. |
| `utc_to_gps` | Convert an ISO-8601 UTC timestamp to GPS seconds. |
| `search_catalog_events` | Search published GWOSC catalogue events (GWTC-1 to GWTC-5) by masses, distance, SNR, p_astro. |
| `get_catalog_event` | Source parameters with 90% intervals for one event, by GW name or GraceDB id. |
| `get_event_labels` | GraceDB labels (data quality, review, retraction, products ready) explained in plain English. |
| `check_data_availability` | Which detectors have public GWOSC strain for an event or GPS span, before you download. |

### ⚠️ Important: where strain data actually comes from

GraceDB does **not** host raw strain data. It stores *event artifacts* (skymaps, classifications,
parameter-estimation results). The public strain time series lives at
**[GWOSC](https://gwosc.org)** and is fetched by GPS time via `gwpy`. Public strain is only
available for **released observing runs** — fresh low-latency alerts will not have public strain
yet. `download_strain_data` reflects this: it reads `t_0` from GraceDB, then pulls open data from
GWOSC (and cleanly reports when data isn't public).

## Requirements

- Python 3.10+
- `mcp`, `ligo-gracedb`, `gwosc`, `python-dotenv`
- Optional: `gwpy` (for `download_strain_data` and `utc_to_gps`) — installed via the `strain` extra.

## Install

Run straight from PyPI with [uv](https://docs.astral.sh/uv/):

```bash
uvx --from "gracedb-mcp[strain]" gracedb-mcp
```

Or from source:

```bash
git clone https://github.com/izabelamaria24/gracedb-mcp.git
cd gracedb-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[strain]"     # omit [strain] if you don't need strain download
```

## Configuration

Copy `.env.example` to `.env` and adjust. Anonymous access is enough for **public** superevents:

```bash
cp .env.example .env
```

| Variable | Purpose |
| --- | --- |
| `GRACEDB_SERVICE_URL` | API base URL (production or playground). |
| `GRACEDB_FORCE_NOAUTH` | `true` for anonymous public access. |
| `GRACEDB_PROXY` / `GRACEDB_CERT` + `GRACEDB_KEY` | LVK member authentication (X.509). |
| `GRACEDB_MCP_DATA_DIR` | Where downloaded files are written (default `~/.cache/gracedb-mcp`; `/data` in Docker). |

## Run locally

```bash
gracedb-mcp          # speaks MCP JSON-RPC over stdio
```

## Development

Requires Python 3.11+ (the `mcp` 2.x SDK needs 3.10+).

```bash
pip install -e ".[dev]"
pytest -q            # tests mock the GraceDB client, no network needed
```

## Run with Docker

A prebuilt image (linux/amd64 and arm64) is published to GHCR:

```bash
docker run --rm -i -v gracedb-data:/data ghcr.io/izabelamaria24/gracedb-mcp:latest
```

| Tag | Download | Contents |
| --- | --- | --- |
| `latest`, `X.Y.Z` | ~165 MB | Everything, including strain download (`gwpy`). |
| `latest-slim`, `X.Y.Z-slim` | ~60 MB | All tools except `download_strain_data` and `utc_to_gps`. |

Or build it yourself (add `--build-arg EXTRAS=` for the slim variant):

```bash
docker build -t gracedb-mcp .
docker run --rm -i -v gracedb-data:/data gracedb-mcp
```

## Connect to an MCP client

### Claude Desktop / VS Code (`mcp.json`)

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "gracedb": {
      "command": "gracedb-mcp",
      "env": {
        "GRACEDB_FORCE_NOAUTH": "true",
        "GRACEDB_MCP_DATA_DIR": "./data"
      }
    }
  }
}
```

Or run the container directly:

```json
{
  "mcpServers": {
    "gracedb": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "-v", "gracedb-data:/data", "ghcr.io/izabelamaria24/gracedb-mcp:latest"]
    }
  }
}
```

## Example agent prompts

- "Search for superevents from 2023-05-01 to 2023-06-01 with a FAR below 1e-8."
- "What's the source classification for S230518h? Is it more likely a BBH or an NSBH?"
- "Download the skymap and p_astro file for S230518h."
- "Fetch H1 and L1 strain around GW150914."
- "What are the masses of GW190814, and how certain are they?"
- "List GWTC-3 events with total mass under 10 solar masses."
- "Was S190814bv checked by a human? What do its labels mean?"
- "Is there public strain for GW190814 from every detector?"

## RL environment (experimental)

`gw_rl` is a Gymnasium environment built on strain fetched by this server. Each episode shows the
agent a 1 s spectrogram and it answers **noise**, **chirp** or **glitch** (+1 correct, -1 wrong).

Real chirps are scarce, so training injects synthetic chirps into real detector noise; the real
catalogue events are kept for evaluation. Chirps are IMRPhenomD waveforms from LALSimulation
(`--chirp imr`, default) or a simple analytic sweep (`--chirp toy`). Glitches are real LIGO
glitches labelled by [Gravity Spy](https://zenodo.org/records/1486046) (`--glitch real`, default)
or sine-Gaussian bursts (`--glitch synthetic`).

It is not part of the PyPI package; run it from a clone of this repository (needs pip 25.1+):

```bash
pip install -e . --group rl
python scripts/fetch_training_strain.py --catalog GWTC-3-confident  # small files, a few MB
python scripts/fetch_glitches.py --files 30  # ~3 GB of GWOSC hour files, cached by gwpy
python scripts/train_agent.py --algo ppo mlp cnn --verbose
```

## Roadmap

- Streamable-HTTP transport (in addition to stdio) for remote deployments.
- MCP **resources** for cached skymaps and p_astro files.

## Disclaimer

Unofficial; not affiliated with or endorsed by LIGO, Virgo, KAGRA or GraceDB. Respect the
[GWOSC](https://gwosc.org) and GraceDB data-use policies.

If you publish results using data fetched by this server, cite the data sources:
[GWOSC](https://gwosc.org/acknowledge/) (CC BY 4.0) and, for glitch labels,
[Gravity Spy](https://zenodo.org/records/1486046).

## Citing

If this server helped your work, please cite it: [doi:10.5281/zenodo.23810872](https://doi.org/10.5281/zenodo.23810872)
(all versions; Zenodo also lists a DOI per release). GitHub's **Cite this repository** button
(from [`CITATION.cff`](https://github.com/izabelamaria24/gracedb-mcp/blob/main/CITATION.cff))
gives APA and BibTeX.

## License

[GPL-3.0-or-later](LICENSE), matching its core dependencies `ligo-gracedb` and `gwpy`.
