// resolve @babel/parser + traverse from the TARGET repo first, else the skill's own vendored copy.
// Lets probes run on repos that don't ship babel in node_modules (e.g. gwent server/).
const path = require("path");
module.exports = function babelFrom(targetDir) {
  const dirs = [targetDir, path.join(__dirname, "..")]; // target, then skill root (../node_modules)
  for (const d of dirs) {
    try {
      const parser = require(path.join(d, "node_modules", "@babel/parser"));
      let traverse = require(path.join(d, "node_modules", "@babel/traverse"));
      traverse = traverse.default || traverse;
      return { parser, traverse };
    } catch (_) {}
  }
  throw new Error("@babel/parser+traverse not found in: " + dirs.map((d) => d + "/node_modules").join(" , "));
};
