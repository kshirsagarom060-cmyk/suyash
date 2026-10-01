# System Architecture

## Architecture Overview

The AI Energy & EV Fleet Optimization Agent architecture is organized into four main layers:

```mermaid
graph TD
    A[Settings & Raw Inputs] --> B[Data Simulator & Loaders]
    B --> C[7-Agent Pipeline Orchestrator]
    subgraph Agents
        C1[Fleet Agent] --> C2[Battery Agent]
        C2 --> C3[Route Agent]
        C3 --> C4[Charging Agent]
        C4 --> C5[Cost Agent]
        C5 --> C6[Optimization Agent]
        C6 --> C7[Recommendation Agent]
    end
    C6 --> D[PuLP MILP Model / Heuristic Fallback]
    D --> E[Plan Validator & Cost Evaluator]
    E --> F[Outputs: latest/ & run_timestamp/]
    F --> G[Streamlit Multi-Page Interactive Dashboard]
```

### Module Responsibilities
- `src/common/`: Shared configuration parsing, typed dataclasses/schemas, time grid slot conversions, structured logging, and IO utilities.
- `src/data/`: Synthetic scenario generator, scenario mutators, external API adapters with caching, CSV loaders, and schema validation.
- `src/agents/`: Specialized modular agents exchanging state via `AgentContext`.
- `src/optimization/`: MILP formulation (PuLP), baseline unmanaged charging policy, greedy price-aware heuristic fallback, invariant validator, and cost evaluator.
- `src/dashboard/`: Interactive Streamlit dashboard with Plotly visualization components and scenario runner.
