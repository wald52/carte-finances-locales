// Serveur local de mesure perf — réplique le comportement de GitHub Pages :
//   - html/js/css/webmanifest/svg bruts -> compressés à la volée (content-encoding: gzip)
//   - *.json.gz -> servis tels quels (application/gzip, SANS content-encoding ;
//     c'est le JS qui décompresse via DecompressionStream, cf. CLAUDE.md §13)
// Usage : node scripts/_perf_server.js [port]
const http = require("http");
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

const ROOT = path.resolve(__dirname, "..");
const PORT = parseInt(process.argv[2] || "8123", 10);
// Latence artificielle par requête (ms) pour simuler le RTT du CDN GitHub Pages
// et rendre la mesure Lighthouse locale représentative de l'online.
const LATENCY = parseInt(process.env.LATENCY || "0", 10);

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".webmanifest": "application/manifest+json",
  ".png": "image/png",
  ".gz": "application/gzip",
  ".ico": "image/x-icon",
};
const COMPRESS = new Set([".html", ".js", ".css", ".json", ".svg", ".webmanifest"]);

http
  .createServer((req, res) => {
    let urlPath = decodeURIComponent(req.url.split("?")[0]);
    if (urlPath.endsWith("/")) urlPath += "index.html";
    const filePath = path.join(ROOT, urlPath);
    if (!filePath.startsWith(ROOT)) {
      res.writeHead(403).end();
      return;
    }
    fs.readFile(filePath, (err, buf) => {
      if (err) {
        res.writeHead(404).end("not found");
        return;
      }
      if (LATENCY > 0) {
        return setTimeout(() => send(res, filePath, buf), LATENCY);
      }
      send(res, filePath, buf);
    });
  })
  .listen(PORT, () => console.log(`perf server on http://localhost:${PORT}/ (latency ${LATENCY}ms)`));

function send(res, filePath, buf) {
      const ext = path.extname(filePath).toLowerCase();
      const headers = {
        "content-type": TYPES[ext] || "application/octet-stream",
        "cache-control": "max-age=600",
      };
      // Les .gz (données) : bruts, sans content-encoding (le JS décompresse).
      // Les assets texte : gzip content-encoding à la volée (comme le CDN).
      if (COMPRESS.has(ext)) {
        const gz = zlib.gzipSync(buf, { level: 6 });
        headers["content-encoding"] = "gzip";
        headers["content-length"] = gz.length;
        res.writeHead(200, headers).end(gz);
      } else {
        headers["content-length"] = buf.length;
        res.writeHead(200, headers).end(buf);
      }
}
