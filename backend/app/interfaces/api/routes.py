from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sse_starlette.sse import EventSourceResponse
from typing import AsyncGenerator, Dict, Any
from sse_starlette.event import ServerSentEvent
import asyncio
import websockets
import logging
from app.application.services.agent import AgentService
from app.application.schemas.request import ChatRequest, FileViewRequest, ShellViewRequest
from app.application.schemas.response import APIResponse, AgentResponse, ShellViewResponse, FileViewResponse

router = APIRouter()
agent_service = AgentService()
logger = logging.getLogger(__name__)

@router.post("/agents", response_model=APIResponse[AgentResponse])
async def create_agent() -> APIResponse[AgentResponse]:
    agent = await agent_service.create_agent()
    return APIResponse.success(
        AgentResponse(
            agent_id=agent.id,
            status="created",
            message="Agent created successfully"
        )
    )

@router.post("/agents/{agent_id}/chat")
async def chat(agent_id: str, request: ChatRequest) -> EventSourceResponse:
    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        async for event in agent_service.chat(agent_id, request.message, request.timestamp):
            yield ServerSentEvent(
                event=event.event,
                data=event.data.model_dump_json() if event.data else None
            )

    return EventSourceResponse(event_generator()) 


@router.post("/agents/{agent_id}/shell", response_model=APIResponse[ShellViewResponse])
async def view_shell(agent_id: str, request: ShellViewRequest) -> APIResponse[ShellViewResponse]:
    """View shell session output
    
    If the agent does not exist or fails to get shell output, an appropriate exception will be thrown and handled by the global exception handler
    """
    result = await agent_service.shell_view(agent_id, request.session_id)
    return APIResponse.success(result)


@router.post("/agents/{agent_id}/file", response_model=APIResponse[FileViewResponse])
async def view_file(agent_id: str, request: FileViewRequest) -> APIResponse[FileViewResponse]:
    """View file content
    
    If the agent does not exist or fails to get file content, an appropriate exception will be thrown and handled by the global exception handler
    
    Args:
        agent_id: Agent ID
        file: File path
        
    Returns:
        APIResponse containing file content
    """
    result = await agent_service.file_view(agent_id, request.file)
    return APIResponse.success(result)


@router.websocket("/agents/{agent_id}/vnc")
async def vnc_websocket(websocket: WebSocket, agent_id: str):
    """VNC WebSocket endpoint (binary mode)
    
    Establishes a connection with the VNC WebSocket service in the sandbox environment and forwards data bidirectionally
    
    Args:
        websocket: WebSocket connection
        agent_id: Agent ID
    """
    await websocket.accept(subprotocol="binary")
    
    try:
    
        # Get sandbox environment address
        sandbox_ws_url = await agent_service.get_vnc_url(agent_id)

        logger.info(f"Connecting to VNC WebSocket at {sandbox_ws_url}")
    
        # Connect to sandbox WebSocket
        async with websockets.connect(sandbox_ws_url) as sandbox_ws:
            logger.info(f"Connected to VNC WebSocket at {sandbox_ws_url}")
            # Create two tasks to forward data bidirectionally
            async def forward_to_sandbox():
                try:
                    while True:
                        data = await websocket.receive_bytes()
                        await sandbox_ws.send(data)
                except WebSocketDisconnect:
                    logger.info("Web -> VNC connection closed")
                    pass
                except Exception as e:
                    logger.error(f"Error forwarding data to sandbox: {e}")
            
            async def forward_from_sandbox():
                try:
                    while True:
                        data = await sandbox_ws.recv()
                        await websocket.send_bytes(data)
                except websockets.exceptions.ConnectionClosed:
                    logger.info("VNC -> Web connection closed")
                    pass
                except Exception as e:
                    logger.error(f"Error forwarding data from sandbox: {e}")
            
            # Run two forwarding tasks concurrently
            forward_task1 = asyncio.create_task(forward_to_sandbox())
            forward_task2 = asyncio.create_task(forward_from_sandbox())
            
            # Wait for either task to complete (meaning connection has closed)
            done, pending = await asyncio.wait(
                [forward_task1, forward_task2],
                return_when=asyncio.FIRST_COMPLETED
            )

            logger.info("WebSocket connection closed")
            
            # Cancel pending tasks
            for task in pending:
                task.cancel()
    
    except ConnectionError as e:
        logger.error(f"Unable to connect to sandbox environment: {str(e)}")
        await websocket.close(code=1011, reason=f"Unable to connect to sandbox environment: {str(e)}")
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
        await websocket.close(code=1011, reason=f"WebSocket error: {str(e)}")


