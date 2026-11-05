// Switch Console workaround (Windows): Switch's Claude Code sign-in check runs
// `claude -p ...` with stdin left open, so Claude waits for input and the check
// times out. This shim closes stdin only for one-shot print runs that don't use
// --input-format. Real Switch sessions (--input-format stream-json) and
// interactive terminal use pass straight through.
// Remove this folder from PATH once Switch fixes the bug.
const { spawn } = require("child_process");
const path = require("path");
const os = require("os");

const real = path.join(os.homedir(), ".local", "bin", "claude.exe");
const args = process.argv.slice(2);
const isPrint = args.includes("-p") || args.includes("--print");
const needsStdin = args.includes("--input-format") || process.stdin.isTTY || !isPrint;

const child = spawn(real, args, {
  stdio: [needsStdin ? "inherit" : "ignore", "inherit", "inherit"],
  windowsHide: true,
});
child.on("error", (err) => {
  console.error(`claude-shim: could not start ${real}: ${err.message}`);
  process.exit(1);
});
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
