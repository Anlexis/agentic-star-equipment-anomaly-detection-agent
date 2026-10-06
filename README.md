# Equipment Anomaly Detection Agent

AI agent for detecting manufacturing equipment anomalies, built with Agentic Star.

> **Category**: Cat 2 (multi-step domain workflow)
> **Industry**: Manufacturing
> **Template ID**: MFG-C2-010

## Overview

Turns a window of raw manufacturing equipment sensor readings into a maintenance decision.
Given an equipment identifier and a set of timestamped readings, the agent summarises each
sensor to statistics, compares them against that machine's normal operating profile, scores
the deviation, classifies the equipment as NORMAL, WARNING or ALERT, infers a probable
mechanical cause from the worst-affected sensor, and recommends an action — from continuing
production to stopping the machine.

The response is an aggregate report: the equipment identifier, the flag, the anomaly score,
the probable cause, the recommended action and a short narrative for the maintenance team.
Individual readings and the per-sensor statistics derived from them stay inside the agent and
are never returned.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at
graph compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/` for the design and the test specification.

## Configuration

`config/agent.yaml` is the registration manifest — identity only. Every runtime parameter lives
in `config/config.yaml`, which the platform loads and passes to the graph:

| Key | Meaning |
|---|---|
| `max_retry` | Backbone retry budget. |
| `ingestion.min_samples` | Readings a sensor needs before its summary is treated as reliable. |
| `baseline.path` | Directory of per-equipment `<equipment_id>.json` normal-operating profiles. |
| `anomaly.warning_threshold` / `anomaly.alert_threshold` | Score bands for WARNING and ALERT. |
| `anomaly.zscore_saturation` | Deviation magnitude that scores a sensor at the maximum. |

Each value is range-checked before it is used; an out-of-range entry is dropped and the
consuming node keeps its documented default.

## Customising

1. Adjust `config/config.yaml` for your own equipment and tolerances.
2. Add per-equipment baseline profiles under `baseline.path` — the built-in profile is a sample.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.

