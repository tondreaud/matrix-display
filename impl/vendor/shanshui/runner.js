// Headless driver for the shan-shui-inf generator core.
// Copyright (c) 2018 Lingdong Huang (generator, see LICENSE);
// this driver is part of matrix-display.
//
// Run: qjs --std runner.js <core.js> <seed>
// Protocol (stdin/stdout lines):
//   in : STRIP <world_x> <world_width> <out_svg_path>
//   out: OK <svg_chars>   |   ERR <message>
// The process stays alive across requests so the infinite world's
// chunk state (MEM) persists and scrolling is continuous.

// --- browser shims (the core expects window/btoa/location) ---
// scriptArgs[0] is this script's own path; real args follow it.
var args = scriptArgs.slice(1);
var seed = args[1] || "matrix";
globalThis.window = globalThis;
window.location = { href: "local://shan-shui?seed=" + seed };
window.btoa = function (s) {
  // Base64 for ASCII input (seeds are sanitized to [A-Za-z0-9-] upstream).
  var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  var out = "";
  for (var i = 0; i < s.length; i += 3) {
    var a = s.charCodeAt(i);
    var b = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
    var c = i + 2 < s.length ? s.charCodeAt(i + 2) : 0;
    out += chars[a >> 2];
    out += chars[((a & 3) << 4) | (b >> 4)];
    out += i + 1 < s.length ? chars[((b & 15) << 2) | (c >> 6)] : "=";
    out += i + 2 < s.length ? chars[c & 63] : "=";
  }
  return out;
};

// The generator logs liberally via console.log; stdout is our protocol
// channel, so send its chatter to stderr instead.
console.log = function () {
  var parts = [];
  for (var i = 0; i < arguments.length; i++) {
    parts.push(String(arguments[i]));
  }
  std.err.puts(parts.join(" ") + "\n");
};

std.loadScript(args[0]);

var VIEW_Y = 700; // square window height in world units (of 800 total)

var line;
while ((line = std.in.getline()) !== null) {
  var parts = line.trim().split(/\s+/);
  if (parts.length === 0 || parts[0] === "") {
    continue;
  }
  if (parts[0] === "QUIT") {
    break;
  }
  if (parts[0] !== "STRIP" || parts.length !== 4) {
    std.out.puts("ERR bad request\n");
    std.out.flush();
    continue;
  }
  try {
    var x = parseInt(parts[1], 10);
    var w = parseInt(parts[2], 10);
    var outPath = parts[3];
    chunkloader(x, x + w);
    chunkrender(x, x + w);
    // Drop chunks well behind the viewport so memory stays flat
    // while scrolling forward forever.
    MEM.chunks = MEM.chunks.filter(function (c) { return c.x > x - 2048; });
    var svg =
      '<svg xmlns="http://www.w3.org/2000/svg" width="' + w +
      '" height="' + VIEW_Y + '" viewBox="' + x + " 0 " + w + " " + VIEW_Y +
      '"><rect x="' + x + '" y="0" width="' + w + '" height="' + VIEW_Y +
      '" fill="white"/>' + MEM.canv + "</svg>";
    var f = std.open(outPath, "w");
    f.puts(svg);
    f.close();
    std.out.puts("OK " + svg.length + "\n");
  } catch (e) {
    std.out.puts("ERR " + e + "\n");
  }
  std.out.flush();
}
