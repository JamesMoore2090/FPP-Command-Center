from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/api/ws")
async def ws(websocket: WebSocket):
    c = websocket.app.state.collector
    await websocket.accept()
    c.hub.clients.add(websocket)
    try:
        await websocket.send_text(c.snapshot().model_dump_json())
        while True:
            await websocket.receive_text()   # keepalive pings from the client; nothing else expected
    except WebSocketDisconnect:
        pass
    finally:
        c.hub.clients.discard(websocket)
