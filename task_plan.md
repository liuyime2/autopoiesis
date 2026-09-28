# Task Plan: QuantVoyager Implementation

## Goal
To pivot the QuantGroup project into "QuantVoyager": an autonomous, lifelong learning quantitative trading agent inspired by the MineDojo Voyager architecture. The agent will use a Curriculum Agent to propose tasks, an Action Agent to write trading strategies (Skills), a Critic Agent to evaluate performance and enforce risk limits, and a Skill Library to store and retrieve successful code.

## Current Phase
Planning

## Phases

### Phase 1: Architecture Planning & Design
- [x] Analyze the original Voyager codebase (`history_version/Voyager`).
- [x] Create the initial `implementation_plan.md` mapping Voyager concepts to the stock market.
- [ ] Receive User Approval on the implementation plan.
- **Status:** In Progress

### Phase 2: Primitives & Sandbox Setup
- [ ] Define the `control_primitives` (API wrappers for fetching data, querying portfolios, executing trades).
- [ ] Implement the `Guardian` risk-limit layer (immutable, non-LLM safety net).
- [ ] Set up the Paper Trading or Backtesting environment hook for the Action Agent.
- **Status:** Pending

### Phase 3: Agent Implementation
- [ ] Build the `SkillManager` module (vector store for retrieving successful strategy code).
- [ ] Build the `Critic Agent` (evaluates Action Agent output against PnL and Risk criteria).
- [ ] Build the `Action Agent` (iterative prompting loop for writing and fixing strategy code).
- [ ] Build the `Curriculum Agent` (proposes the next trading concept or goal to explore).
- **Status:** Pending

### Phase 4: Integration & Lifelong Learning Loop
- [ ] Connect the 4 core components into the main learning loop.
- [ ] Run the initial loop in a controlled paper environment to populate the first base skills.
- [ ] Monitor and refine the Curriculum progression.
- **Status:** Pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Adopt Voyager Architecture | User requested the system to be a stock market version of Voyager, leveraging its Curriculum, Action, Critic, and Skill Library loop. |
| Strict Guardian Layer | Voyager Minecraft agents can die and respawn; trading agents cannot blow up accounts. An immutable risk layer must sit inside the Critic/Environment. |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| None yet | N/A | N/A |
