# Browser chat for newton-claw. node runs agents/nemoclaw/newton-mcp.mjs (the tool server that
# agents/chat.py spawns per call); python runs server.py. The repo is mounted read-only at /workspace.
FROM node:22-alpine
RUN apk add --no-cache python3
WORKDIR /app
COPY server.py index.html samples.json ./
USER node
EXPOSE 8095
CMD ["python3", "/app/server.py"]
