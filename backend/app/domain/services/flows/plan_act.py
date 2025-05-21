import logging
from app.domain.services.flows.base import BaseFlow
from app.domain.models.agent import Agent
from app.domain.events.agent_events import AgentEvent
from typing import AsyncGenerator
from enum import Enum
from app.domain.events.agent_events import (
    AgentEvent, 
    PlanCreatedEvent, 
    PlanCompletedEvent,
    DoneEvent
)
from app.domain.models.plan import ExecutionStatus
from app.domain.services.agents.planner import PlannerAgent
from app.domain.services.agents.execution import ExecutionAgent
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.browser import Browser
from app.domain.external.search import SearchEngine
from app.domain.repositories.agent_repository import AgentRepository

logger = logging.getLogger(__name__)

class AgentStatus(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    UPDATING = "updating"

class PlanActFlow(BaseFlow):
    def __init__(
        self,
        agent_id: str,
        agent_repository: AgentRepository,
        llm: LLM,
        sandbox: Sandbox,
        browser: Browser,
        search_engine: SearchEngine
    ):
        super().__init__(agent_id, agent_repository)
        self.status = AgentStatus.IDLE
        self.plan = None
        # Create planner and execution agents
        self.planner = PlannerAgent(
            agent_id=self._agent_id,
            agent_repository=self._repository,
            llm=llm,
        )
        logger.debug(f"Created planner agent for Agent {self._agent_id}")
        
        self.executor = ExecutionAgent(
            agent_id=self._agent_id,
            agent_repository=self._repository,
            llm=llm,
            sandbox=sandbox,
            browser=browser,
            search_engine=search_engine,
        )
        logger.debug(f"Created execution agent for Agent {self._agent_id}")

    async def run(self, message: str) -> AsyncGenerator[AgentEvent, None]:
        if not self.is_done():
            # interrupt the current flow
            self.status = AgentStatus.PLANNING
            self.planner.roll_back()
            self.executor.roll_back()

        logger.info(f"Agent {self._agent_id} started processing message: {message[:50]}...")
        step = None
        while True:
            if self.status == AgentStatus.IDLE:
                logger.info(f"Agent {self._agent_id} state changed from {AgentStatus.IDLE} to {AgentStatus.PLANNING}")
                self.status = AgentStatus.PLANNING
            elif self.status == AgentStatus.PLANNING:
                # Create plan
                logger.info(f"Agent {self._agent_id} started creating plan")
                async for event in self.planner.create_plan(message):
                    if isinstance(event, PlanCreatedEvent):
                        self.plan = event.plan
                        logger.info(f"Agent {self._agent_id} created plan successfully with {len(event.plan.steps)} steps")
                    yield event
                logger.info(f"Agent {self._agent_id} state changed from {AgentStatus.PLANNING} to {AgentStatus.EXECUTING}")
                self.status = AgentStatus.EXECUTING
                    
            elif self.status == AgentStatus.EXECUTING:
                # Execute plan
                self.plan.status = ExecutionStatus.RUNNING
                step = self.plan.get_next_step()
                if not step:
                    logger.info(f"Agent {self._agent_id} has no more steps, state changed from {AgentStatus.EXECUTING} to {AgentStatus.COMPLETED}")
                    self.status = AgentStatus.COMPLETED
                    continue
                # Execute step
                logger.info(f"Agent {self._agent_id} started executing step {step.id}: {step.description[:50]}...")
                async for event in self.executor.execute_step(self.plan, step):
                    yield event
                logger.info(f"Agent {self._agent_id} completed step {step.id}, state changed from {AgentStatus.EXECUTING} to {AgentStatus.UPDATING}")
                self.status = AgentStatus.UPDATING
            elif self.status == AgentStatus.UPDATING:
                # Update plan
                logger.info(f"Agent {self._agent_id} started updating plan")
                async for event in self.planner.update_plan(self.plan):
                    yield event
                logger.info(f"Agent {self._agent_id} plan update completed, state changed from {AgentStatus.UPDATING} to {AgentStatus.EXECUTING}")
                self.status = AgentStatus.EXECUTING
            elif self.status == AgentStatus.COMPLETED:
                self.plan.status = ExecutionStatus.COMPLETED
                logger.info(f"Agent {self._agent_id} plan has been completed")
                yield PlanCompletedEvent(plan=self.plan) 
                self.status = AgentStatus.IDLE
                break
        yield DoneEvent()
        
        logger.info(f"Agent {self._agent_id} message processing completed")
    
    def is_done(self) -> bool:
        return self.status == AgentStatus.IDLE