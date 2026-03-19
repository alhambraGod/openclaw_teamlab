/**
 * TCP proxy: 127.0.0.1:10301 -> claw-teamlab:10301
 *
 * Ensures the OpenClaw agent's curl/fetch to localhost:10301 reaches
 * the TeamLab container via Docker DNS, regardless of what address the
 * LLM chooses to use.
 *
 * Runs as a background daemon inside claw-openclaw before the main
 * process starts. Exits silently if the port is already in use.
 */
import net from "node:net";

const LOCAL_PORT  = 10301;
const REMOTE_HOST = "claw-teamlab";
const REMOTE_PORT = 10301;

const server = net.createServer((client) => {
  const remote = net.createConnection(REMOTE_PORT, REMOTE_HOST, () => {
    client.pipe(remote);
    remote.pipe(client);
  });
  remote.on("error", () => client.destroy());
  client.on("error", () => remote.destroy());
});

server.on("error", (err) => {
  if (err.code === "EADDRINUSE") {
    process.exit(0);
  }
  console.error("[teamlab-proxy]", err.message);
  process.exit(1);
});

server.listen(LOCAL_PORT, "127.0.0.1", () => {
  console.log(`[teamlab-proxy] 127.0.0.1:${LOCAL_PORT} -> ${REMOTE_HOST}:${REMOTE_PORT}`);
});
