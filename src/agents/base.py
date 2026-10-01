"""Base agent class and execution harness."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from src.common.schemas import AgentContext, AgentResult

logger = logging.getLogger("ev_fleet_optimizer.agents")


class BaseAgent(ABC):
    """Abstract base class for all specialized domain agents."""
    name: str = "base_agent"

    def run(self, ctx: AgentContext) -> AgentResult:
        """Standard execution harness with timing and structured exception handling."""
        start_time = time.perf_counter()
        logger.info(f"Agent '{self.name}' starting execution...")
        try:
            result = self._execute(ctx)
            duration = time.perf_counter() - start_time
            result.duration_s = round(duration, 4)
            if not result.agent:
                result.agent = self.name
            logger.info(f"Agent '{self.name}' completed with status '{result.status}' in {result.duration_s}s")
            return result
        except Exception as e:
            duration = time.perf_counter() - start_time
            logger.error(f"Agent '{self.name}' failed with error: {e}", exc_info=True)
            return AgentResult(
                agent=self.name,
                status="failed",
                outputs={},
                warnings=[f"Execution exception: {str(e)}"],
                metrics={},
                duration_s=round(duration, 4),
            )

    @abstractmethod
    def _execute(self, ctx: AgentContext) -> AgentResult:
        """Domain-specific logic to be implemented by each specialized agent."""
        pass
