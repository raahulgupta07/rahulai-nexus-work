"""Make `localhost:8180` mean Keycloak inside the app container too.

★★★An OIDC issuer must be ONE URL that both the browser and the server can
reach. The browser reaches the rig at `localhost:8180` (a published port); the
container cannot — its own localhost is itself, and the container-side name
`test-keycloak:8080` is meaningless to the browser. Configure either one and the
other half of the flow breaks: the browser cannot resolve the authorize URL, or
the server cannot exchange the code.

This forwards the container's own 127.0.0.1:8180 to the rig, so the single URL
`http://localhost:8180` is correct on both sides. The alternative is an
/etc/hosts entry on the developer's machine, which is a system file and not
something a test rig should be editing.

DEVELOPMENT ONLY — it is started by hand and dies with the container.
"""
import asyncio

TARGET_HOST, TARGET_PORT = "test-keycloak", 8080
LISTEN_PORT = 8180


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(local_reader, local_writer):
    try:
        remote_reader, remote_writer = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
    except Exception:
        local_writer.close()
        return
    await asyncio.gather(pipe(local_reader, remote_writer), pipe(remote_reader, local_writer))


async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", LISTEN_PORT)
    print(f"forwarding 127.0.0.1:{LISTEN_PORT} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
    async with server:
        await server.serve_forever()

asyncio.run(main())
