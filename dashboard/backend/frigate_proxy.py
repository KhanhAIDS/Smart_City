import httpx
import logging
from fastapi import Request, Response, HTTPException, WebSocket
import asyncio
import websockets

logger = logging.getLogger(__name__)

FRIGATE_PROXY_TIMEOUT = 30.0

async def proxy_frigate_api(request: Request, path: str, frigate_api_url: str):
    method = request.method
    
    # POST /api/webrtc is allowed, others must be GET or HEAD
    if method not in ["GET", "HEAD"] and not (method == "POST" and path == "webrtc"):
        raise HTTPException(status_code=405, detail="Method Not Allowed")
    
    if path == "webrtc":
        target_base = frigate_api_url.replace(":5000", ":1984")
        url = f"{target_base}/api/webrtc"
    else:
        url = f"{frigate_api_url}/api/{path}"
        
    query_params = request.url.query
    if query_params:
        url += f"?{query_params}"

    headers = dict(request.headers)
    hop_by_hop = ['host', 'connection', 'upgrade', 'proxy-connection', 'keep-alive', 'transfer-encoding', 'content-encoding']
    for h in hop_by_hop:
        headers.pop(h, None)

    body = await request.body()
    
    try:
        async with httpx.AsyncClient(timeout=FRIGATE_PROXY_TIMEOUT) as client:
            resp = await client.request(
                method,
                url,
                headers=headers,
                content=body
            )
            
            response_headers = dict(resp.headers)
            for h in hop_by_hop:
                response_headers.pop(h, None)
                
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=response_headers,
                media_type=resp.headers.get("content-type")
            )
    except Exception as e:
        logger.error(f"Error proxying to {url}: {e}")
        raise HTTPException(status_code=502, detail="Bad Gateway")

async def proxy_frigate_ws(websocket: WebSocket, path: str, frigate_api_url: str):
    await websocket.accept()
    
    ws_url = frigate_api_url.replace("http://", "ws://").replace("https://", "wss://")
    target_url = f"{ws_url}/live/{path}"
    
    query = websocket.scope.get('query_string', b'').decode('utf-8')
    if query:
        target_url += f"?{query}"
        
    try:
        async with websockets.connect(target_url) as target_ws:
            async def forward_to_target():
                try:
                    while True:
                        message = await websocket.receive()
                        if 'text' in message:
                            await target_ws.send(message['text'])
                        elif 'bytes' in message:
                            await target_ws.send(message['bytes'])
                except Exception:
                    pass

            async def forward_to_client():
                try:
                    while True:
                        data = await target_ws.recv()
                        if isinstance(data, bytes):
                            await websocket.send_bytes(data)
                        else:
                            await websocket.send_text(data)
                except Exception:
                    pass
            
            await asyncio.gather(
                forward_to_target(),
                forward_to_client()
            )
    except Exception as e:
        logger.error(f"WebSocket proxy error for {target_url}: {e}")
    finally:
        try:
            await websocket.close()
        except:
            pass
